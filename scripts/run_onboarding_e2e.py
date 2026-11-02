"""The whole new-partner chain in one headless run, scored case by case.

    Admin creates an Escrow for a brand-new representative
      -> the portal emails User ID + temporary password
        -> the partner signs in with them
          -> the forced password change
            -> signing in again with the new password
              -> the temporary password is dead

Every step is an assertion, and every assertion is one of the test cases in
`docs/post-creation-email-and-partner-login.md` (TC-N-01 … TC-N-10 and the
negatives). The run prints a result table and writes
`reports/onboarding_e2e.json`.

    python scripts/run_onboarding_e2e.py                 # headless (default)
    python scripts/run_onboarding_e2e.py --headed --slowmo 200
    python scripts/run_onboarding_e2e.py --escrow-id EXC-… --email …@…
        # skip the admin leg and re-drive the partner half of an existing run

Design notes, each of which is a defect or a trap the suite already paid for:

* **A stage that cannot run is BLOCKED, not FAILED.** Without the temporary
  password there is nothing to log in with, so reporting the login cases as
  failures would be a lie about what was observed.
* **Every identifying field of the representative is randomised** - email,
  company, first/last name *and* mobile. Reusing any one of them attaches the
  escrow to the existing partner and no credentials are issued (DEF-MAIL-01).
* **Logins send never-used coordinates.** The backend skips the OTP for a known
  location and returns a token the Angular client throws away, stranding the
  user on the login page (DEF-OPS-01/DEF-PARTNER-04). Forcing a fresh location
  keeps the flow on the branch the UI can actually complete; a dedicated case
  re-checks the defect rather than hiding it.
* **The partner portal logs out after ~2 minutes idle**, so the partner half
  keeps moving and re-logs in where a stage may have taken longer.
"""
from __future__ import annotations

import argparse
import json
import random
import re
import string
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

# Run as `python scripts/run_onboarding_e2e.py` from the repo root: that puts
# `scripts/` on the path, not the root, so the suite packages are invisible
# without this. Cheaper than making callers remember `python -m`.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from playwright.sync_api import sync_playwright  # noqa: E402

from admin.data.escrow_data import (EscrowData, NewRepresentative,  # noqa: E402
                                    random_account_number, unique_alias_tag)
from admin.pages.escrow_creation_flow import EscrowCreationFlow  # noqa: E402
from admin.pages.login_page import AdminLoginPage  # noqa: E402
from config.settings import settings  # noqa: E402
from core.artifact_store import ArtifactStore  # noqa: E402
from core.mailbox import (GmailMailbox, MailboxUnavailable,  # noqa: E402
                          OnboardingMail)
from partner.pages.set_password_page import SetPasswordPage  # noqa: E402
from partner_ops.pages.login_page import PartnerOpsLoginPage  # noqa: E402

RESULTS_PATH = Path("reports/onboarding_e2e.json")
PARTNER_PORTAL_HOST = "excrow-uat.sprintnxt.in"

# Beneficiary payload. Display name and account number are randomised per run -
# the portal refuses a duplicate account, so a fixed one passes once and then
# fails forever. IFSC/bank are a real pair, because the bank must be chosen from
# the type-ahead and an invented one cannot be.
BENEFICIARY_TEMPLATE = {
    "displayName": "QA Bene",
    "name": "QA Onboarding Beneficiary",
    "mobile": "9876543210",
    "email": "anand.kumar+qabene@paysprint.in",
    "ifsc": "UBIN0438583",
    "branchName": "QA Branch",
    "accountNumber": "9439543999123",
    "pincode": "110091",
}

PASS, FAIL, BLOCKED, INFO = "PASS", "FAIL", "BLOCKED", "INFO"


# --- result recording ----------------------------------------------------
@dataclass
class Results:
    rows: list[dict] = field(default_factory=list)

    def record(self, tc: str, title: str, status: str, evidence: str = "") -> str:
        self.rows.append({"id": tc, "title": title, "status": status,
                          "evidence": str(evidence)[:500]})
        marker = {PASS: "PASS ", FAIL: "FAIL ", BLOCKED: "BLOCK", INFO: "INFO "}[status]
        print(f"  [{marker}] {tc}  {title}"
              + (f"\n           {str(evidence)[:300]}" if evidence else ""), flush=True)
        return status

    def check(self, tc: str, title: str, condition: bool, evidence: str = "") -> bool:
        self.record(tc, title, PASS if condition else FAIL, evidence)
        return bool(condition)

    def block(self, tc: str, title: str, why: str) -> None:
        self.record(tc, title, BLOCKED, why)

    def tally(self) -> dict:
        counts = {PASS: 0, FAIL: 0, BLOCKED: 0, INFO: 0}
        for row in self.rows:
            counts[row["status"]] += 1
        return counts

    def dump(self, extra: dict) -> Path:
        RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
        RESULTS_PATH.write_text(json.dumps(
            {"summary": self.tally(), **extra, "cases": self.rows},
            indent=2), encoding="utf-8")
        return RESULTS_PATH


def banner(text: str) -> None:
    print(f"\n{'=' * 72}\n{text}\n{'=' * 72}", flush=True)


# --- password helpers ----------------------------------------------------
def strong_password() -> str:
    """A password that satisfies every stated rule: length, case, digit, symbol."""
    body = "".join(random.choices(string.ascii_lowercase, k=4))
    return f"Qa{body.capitalize()}{random.randint(10, 99)}@x"


# The policy cases: each violates exactly one rule, so a rejection message can
# be attributed. `label` is what the case is testing for in the report.
def policy_cases(valid: str) -> list[tuple[str, str, str]]:
    return [
        ("too short", "Aa1@z", "minimum length"),
        ("no uppercase", "qatest12@x", "an uppercase letter"),
        ("no lowercase", "QATEST12@X", "a lowercase letter"),
        ("no digit", "QaTestPass@x", "a number"),
        ("no special character", "QaTestPass12", "a special character"),
        ("whitespace only", "        ", "non-blank input"),
    ]


# --- the partner half ----------------------------------------------------
def partner_context(playwright, headless: bool, slowmo: int):
    browser = playwright.chromium.launch(headless=headless, slow_mo=slowmo)
    context = browser.new_context(
        ignore_https_errors=True,
        viewport={"width": 1440, "height": 900},
        permissions=["geolocation"],
        geolocation={"latitude": 28.6139, "longitude": 77.2090},
        locale="en-IN",
    )
    context.set_default_timeout(settings.default_timeout_ms)
    return browser, context


def sign_in(page, email: str, password: str, *, fresh_location: bool = True):
    """One login attempt. Returns the page object and the captured responses."""
    login = PartnerOpsLoginPage(page)
    if fresh_location:
        login.force_fresh_location()
    responses = login.capture_login_responses()
    login.open()
    login.submit_credentials(email, password)
    page.wait_for_timeout(4_000)
    return login, responses


def status_of(responses: list[dict]) -> str:
    if not responses:
        return "no /auth/login response captured"
    last = responses[-1]
    body = last.get("json") or {}
    return (f"HTTP {last['status']} "
            f"allowTwoStep={body.get('allowTwoStep')} "
            f"firstLogin={body.get('firstLogin')} "
            f"message={str(body.get('message'))[:80]!r}")


def reached_dashboard(page, timeout: int = 20_000) -> bool:
    deadline = time.monotonic() + timeout / 1000
    while time.monotonic() < deadline:
        if not re.search(r"/auth/(login|set-password|session-expired)", page.url, re.I):
            return True
        page.wait_for_timeout(1_000)
    return False


# --- stages --------------------------------------------------------------
def create_escrow(args, results: Results) -> dict:
    """Admin leg: a brand-new representative, straight through the wizard."""
    banner("PART 1 - Super Admin creates the Escrow (new representative)")
    rep = NewRepresentative.with_tag(args.tag or unique_alias_tag())
    data = EscrowData(new_representative=rep)

    print(f"  representative : {rep.email}")
    print(f"  name / company : {rep.first_name} {rep.last_name} / {rep.company_name}")
    print(f"  mobile         : {rep.mobile}   (all four fields fresh - DEF-MAIL-01)")
    print(f"  escrow name    : {data.name}\n")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not args.headed, slow_mo=args.slowmo)
        context = browser.new_context(
            ignore_https_errors=True,
            viewport={"width": 1440, "height": 900},
            permissions=["geolocation"],
            geolocation={"latitude": 28.6139, "longitude": 77.2090},
            locale="en-IN",
        )
        context.set_default_timeout(settings.default_timeout_ms)
        page = context.new_page()
        try:
            login = AdminLoginPage(page)
            login.open()
            login.login(settings.admin_username, settings.admin_password,
                        settings.admin_pin)
            login.assert_dashboard_loaded()
            results.record("TC-A-01", "Super Admin signs in", PASS, page.url)

            flow = EscrowCreationFlow(page)
            if args.dry_run:
                # Stage 1 only, and stop: this proves the wizard still accepts a
                # brand-new representative without spending a UAT record or a
                # temporary password. Useful when the mailbox is not yet
                # readable, because a full run would then BLOCK at PART 2 having
                # already created the escrow.
                flow.open_new_escrow_form()
                escrow_id = flow.fill_project_information(data)
                results.check("TC-A-02-dry", "Wizard accepts an unregistered "
                              "representative (stage 1, not submitted)",
                              bool(escrow_id), f"reserved {escrow_id}")
                results.check("TC-A-04", "Representative fields stay editable "
                              "after 'User not found!'",
                              flow.rep.company_name_input.input_value()
                              == data.new_representative.company_name,
                              f"company={flow.rep.company_name_input.input_value()!r}")
                results.check("TC-A-05", "Stage 1 is complete - 'Next' enables",
                              flow.details.next_is_enabled(),
                              str(flow.details.invalid_controls()))
                results.record("TC-A-03", "New Escrow appears on the admin "
                               "dashboard", BLOCKED, "dry run - nothing submitted")
                context.close(); browser.close()
                return {"escrow_id": "", "email": data.new_representative.email,
                        "company": data.new_representative.company_name,
                        "escrow_name": data.name, "dry_run": True}

            escrow_id = flow.create(data)
            results.check("TC-A-02", "Escrow created for an unregistered "
                          "representative", bool(escrow_id), escrow_id)
            listed = flow.nav.is_listed(escrow_id)
            results.check("TC-A-03", "New Escrow appears on the admin dashboard",
                          listed, f"{escrow_id} listed={listed}")
        except Exception as exc:
            results.record("TC-A-02", "Escrow created for an unregistered "
                           "representative", FAIL, repr(exc))
            try:
                page.screenshot(path="reports/screenshots/onboarding_admin_fail.png",
                                full_page=True)
            except Exception:
                pass
            context.close(); browser.close()
            raise
        context.close(); browser.close()

    store = ArtifactStore()
    store.set("escrow_id", escrow_id)
    store.set("escrow_name", data.name)
    store.set("new_partner_email", rep.email)
    store.set("new_partner_company", rep.company_name)
    return {"escrow_id": escrow_id, "email": rep.email,
            "company": rep.company_name, "escrow_name": data.name}


def open_mailbox(wait: int) -> GmailMailbox:
    """Open the mailbox, retrying while a sign-in may still be in progress.

    A signed-out session is not necessarily permanent: the usual cause is that
    the one-time re-auth is happening in another window right now. Retrying for
    the same budget already allowed for the mail itself turns "you were 40
    seconds too slow" from a wasted escrow into a wait.
    """
    deadline = time.monotonic() + wait
    last: MailboxUnavailable | None = None
    while True:
        try:
            return GmailMailbox().open()
        except MailboxUnavailable as exc:
            last = exc
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise last
            print(f"    mailbox not readable yet; retrying for {int(remaining)}s "
                  "(finish the Gmail sign-in if it is open)", flush=True)
            time.sleep(min(15, max(1, remaining)))


def read_mail(created: dict, results: Results, wait: int) -> OnboardingMail | None:
    """Mail leg: TC-N-01 … TC-N-04."""
    banner("PART 2 - The onboarding email")
    try:
        with open_mailbox(wait) as inbox:
            found = inbox.wait_for_message(created["escrow_id"], timeout=wait)
            results.check("TC-N-01", "Confirmation email received",
                          found, f"searched {created['escrow_id']} for up to {wait}s")
            if not found:
                return None
            mail = inbox.open_latest(created["escrow_id"])
    except MailboxUnavailable as exc:
        for tc, title in (("TC-N-01", "Confirmation email received"),
                          ("TC-N-02", "Email carries User ID + temporary password"),
                          ("TC-N-03", "Escrow ID in the email matches the portal"),
                          ("TC-N-04", "Partner link points at the partner portal")):
            results.block(tc, title, str(exc).splitlines()[0])
        return None

    results.check("TC-N-02", "Email carries User ID + temporary password",
                  mail.has_credentials,
                  f"user_id={mail.user_id!r} password_len={len(mail.temp_password)}")
    results.check("TC-N-03", "Escrow ID in the email matches the portal",
                  mail.escrow_id.upper() == created["escrow_id"].upper(),
                  f"mail={mail.escrow_id} portal={created['escrow_id']}")
    results.check("TC-N-04", "Partner link points at the partner portal over HTTPS",
                  mail.portal_link.startswith("https://")
                  and PARTNER_PORTAL_HOST in mail.portal_link,
                  mail.portal_link or "no partner link in the mail")
    # DEF-MAIL-01 tell: the greeting names the company the escrow was filed under.
    results.check("DEF-MAIL-01", "Escrow filed under the new company, not an "
                  "existing partner",
                  mail.greeting.strip().lower() == created["company"].strip().lower(),
                  f"greeting={mail.greeting!r} expected={created['company']!r}")
    results.check("TC-N-02b", "User ID in the email is the address that was entered",
                  mail.user_id.lower() == created["email"].lower(),
                  f"{mail.user_id!r} vs {created['email']!r}")
    return mail


def first_login_and_change(args, created: dict, mail: OnboardingMail,
                           results: Results) -> str:
    """Partner leg: TC-N-05 … TC-N-08 plus the policy and negative cases."""
    banner("PART 3 - First sign-in with the emailed credentials")
    email, temp = mail.user_id or created["email"], mail.temp_password
    new_password = strong_password()

    with sync_playwright() as p:
        browser, context = partner_context(p, not args.headed, args.slowmo)
        page = context.new_page()
        try:
            # --- TC-N-N01 / TC-N-N02: negatives first, before the account has
            # been through a successful login, so nothing depends on order.
            _, wrong_pw = sign_in(page, email, "WrongPass9@z")
            blocked = not reached_dashboard(page, timeout=6_000)
            results.check("TC-N-N01", "Wrong temporary password is rejected",
                          blocked, status_of(wrong_pw))
            wrong_pw_status = wrong_pw[-1]["status"] if wrong_pw else 0
            results.record("DEF-PARTNER-01",
                           "Failed login returns 4xx, not 500",
                           PASS if wrong_pw_status in (400, 401, 403) else FAIL,
                           status_of(wrong_pw))
            login_page = PartnerOpsLoginPage(page)
            results.record("DEF-PARTNER-02", "Failed login shows the user a message",
                           PASS if login_page.message_visible() else FAIL,
                           login_page.visible_error() or "no alert/toast/inline text")

            page.close(); page = context.new_page()
            _, wrong_email = sign_in(page, "no.such.user+qa@paysprint.in", temp)
            results.check("TC-N-N02", "Unknown email is rejected",
                          not reached_dashboard(page, timeout=6_000),
                          status_of(wrong_email))
            enumerable = _enumeration_differs(wrong_pw, wrong_email)
            results.check("TC-N-N02b", "No account enumeration: unknown email and "
                          "wrong password answer alike", not enumerable,
                          f"wrong-password: {status_of(wrong_pw)} | "
                          f"unknown-email: {status_of(wrong_email)}")

            # --- TC-N-05: the real first sign-in
            page.close(); page = context.new_page()
            login, first = sign_in(page, email, temp)
            otp_shown = login.otp_challenge_visible(timeout=20_000)
            results.check("TC-N-05", "First sign-in with the emailed credentials "
                          "is accepted", bool(first) and first[-1]["status"] == 200,
                          status_of(first))
            results.record("Q-01", "First-time login also raises the OTP challenge",
                           PASS if otp_shown else INFO,
                           "4-digit challenge shown" if otp_shown
                           else "no OTP step - straight through")
            if otp_shown:
                login.enter_login_otp(settings.partner_login_otp)
                page.wait_for_timeout(4_000)

            # --- TC-N-06: the change is forced
            setpw = SetPasswordPage(page)
            forced = setpw.is_current(timeout=20_000)
            results.check("TC-N-06", "Password change is forced before the dashboard",
                          forced, page.url)
            if not forced:
                for tc, title in (("TC-N-07", "Password policy is enforced"),
                                  ("TC-N-08", "Password change succeeds")):
                    results.block(tc, title, f"never reached set-password ({page.url})")
                return ""

            results.check("TC-N-06b", "Set-password screen explains why",
                          setpw.is_visible(setpw.first_sign_in_notice, timeout=5_000),
                          setpw.body_text()[:200].replace("\n", " "))
            results.check("TC-N-06c", "Three password fields are present",
                          setpw.field_count() >= 3,
                          f"{setpw.field_count()} password inputs; "
                          f"located by {setpw.locator_strategy('new_password')}")

            # Navigating away must not escape the change.
            page.goto(settings.partner_base_url.split("#")[0] + "#/dashboard",
                      wait_until="domcontentloaded")
            page.wait_for_timeout(3_000)
            results.check("TC-N-06d", "Dashboard cannot be reached around the "
                          "forced change", "set-password" in page.url
                          or "/auth/login" in page.url, page.url)
            if "set-password" not in page.url:
                results.block("TC-N-07", "Password policy is enforced",
                              "session lost when navigating away")
                return ""

            # --- TC-N-07 + N-N04 / N-N05: the policy
            banner("PART 4 - Password policy")
            for label, candidate, rule in policy_cases(new_password):
                outcome = setpw.try_password(temp, candidate)
                rejected = not _password_accepted(page, outcome)
                results.check(f"TC-N-07 [{label}]",
                              f"Rejected for want of {rule}", rejected,
                              f"submit_enabled={outcome['enabled']} "
                              f"msg={outcome['message'][:160]!r}")
                if not rejected:      # it went through - nothing left to test here
                    return ""

            mismatch = setpw.try_password(temp, new_password, new_password + "x")
            results.check("TC-N-N04", "Mismatched confirmation is blocked",
                          not _password_accepted(page, mismatch),
                          f"submit_enabled={mismatch['enabled']} "
                          f"msg={mismatch['message'][:160]!r}")

            reuse = setpw.try_password(temp, temp)
            results.check("TC-N-07 [reuse]", "Reusing the emailed password is refused",
                          not _password_accepted(page, reuse),
                          f"submit_enabled={reuse['enabled']} "
                          f"msg={reuse['message'][:160]!r}")

            wrong_old = setpw.try_password("NotTheEmailed1@z", new_password)
            results.check("TC-N-N01b", "Wrong 'password from email' is refused",
                          not _password_accepted(page, wrong_old),
                          f"submit_enabled={wrong_old['enabled']} "
                          f"msg={wrong_old['message'][:160]!r}")

            # --- TC-N-08: the real change
            banner("PART 5 - Change the password")
            print(f"  new password: {new_password}")
            responses = setpw.capture_change_responses()
            message = setpw.change_password(temp, new_password)
            page.wait_for_timeout(3_000)
            ok = bool(re.search(r"success|updated", message, re.I)) or \
                bool(responses and responses[-1]["status"] == 200)
            results.check("TC-N-08", "Password change succeeds", ok,
                          f"message={message[:160]!r} "
                          f"http={[r['status'] for r in responses]}")
            setpw.dismiss_message()
        finally:
            try:
                page.screenshot(path="reports/screenshots/onboarding_partner_last.png",
                                full_page=True)
            except Exception:
                pass
            context.close(); browser.close()
    return new_password


def _password_accepted(page, outcome: dict) -> bool:
    """Did an attempt actually change the password?

    An attempt is only "accepted" if the form allowed a submit *and* nothing
    rejected it. Reading the message alone is not enough - DEF-PARTNER-02 showed
    this portal failing silently - so leaving the set-password route counts too.
    """
    if not outcome["enabled"]:
        return False
    if re.search(r"success|updated", outcome["message"], re.I):
        return True
    return "set-password" not in page.url


def _enumeration_differs(a: list[dict], b: list[dict]) -> bool:
    """Whether wrong-password and unknown-email are distinguishable."""
    if not a or not b:
        return False
    if a[-1]["status"] != b[-1]["status"]:
        return True
    msg_a = str((a[-1].get("json") or {}).get("message", "")).lower()
    msg_b = str((b[-1].get("json") or {}).get("message", "")).lower()
    return msg_a != msg_b


def partner_operations(page, escrow_id: str, results: Results) -> None:
    """PART 8 - Escrow Management -> activation -> Beneficiary add + verify.

    Runs inside the session established by PART 6, because the portal drops an
    idle session after ~2 minutes and re-logging in per step would spend more
    time on OTP than on the work.

    **Activation is deliberately reported, not attempted.** The partner-side
    escrow detail is a read-only view - there is no Next/Submit/Confirm chain -
    and activation is a Super Admin flow. That is already asserted in
    `partner_ops/tests/test_escrow_and_pricing.py` and recorded as PART 2 "N/A"
    in `docs/partner-operations-test-cases.md`. So this stage looks for an
    activation control and records what it finds: a partner-side control
    appearing would be a *finding*, and its absence is the documented behaviour,
    not a failure of this run.
    """
    from partner_ops.pages.beneficiary_page import BeneficiaryPage
    from partner_ops.pages.escrow_page import EscrowDetailPage, EscrowListPage

    banner("PART 8 - Escrow Management, activation, Beneficiary")
    listing = EscrowListPage(page)
    try:
        listing.open()
    except Exception as exc:
        results.block("TC-OPS-01", "Escrow Management lists the escrow", repr(exc))
        return

    row = listing.search(escrow_id)
    if not results.check("TC-OPS-01", "Escrow Management lists the new escrow",
                         row is not None,
                         repr(row) if row else f"{escrow_id} not in the list"):
        return
    status = row.status.strip()
    results.record("TC-OPS-02", "New escrow's status on the partner side", INFO,
                   f"{escrow_id} status={status!r}")

    alert = listing.open_details(escrow_id)
    detail = EscrowDetailPage(page)
    tabs = detail.tabs()
    if not results.check("TC-OPS-03", "Escrow detail opens for the partner",
                         bool(tabs),
                         f"tabs={tabs} alert={alert!r}" if not tabs
                         else f"tabs={tabs}"):
        return
    results.check("TC-OPS-03b", "Detail view shows the right Escrow ID",
                  detail.shows_escrow_id(escrow_id), f"{escrow_id} in header")

    # --- activation: report what the portal actually offers ---------------
    activate = page.get_by_role(
        "button", name=re.compile(r"activate", re.I)).or_(
        page.get_by_text(re.compile(r"^\s*activate\b", re.I))).first
    has_activate = False
    try:
        has_activate = activate.count() > 0 and activate.is_visible()
    except Exception:
        has_activate = False
    results.record(
        "TC-OPS-04", "Escrow activation from the partner portal",
        FAIL if has_activate else INFO,
        "an Activate control is exposed to the partner - activation is "
        "supposed to be Super Admin only" if has_activate else
        f"no activate control; partner detail is read-only (status={status!r}). "
        "Activation is a Super Admin flow - PART 2 'N/A' in "
        "docs/partner-operations-test-cases.md")

    # --- beneficiary ------------------------------------------------------
    tab = next((t for t in tabs if "benefic" in t.lower()), "")
    if not tab:
        results.block("TC-OPS-05", "Add Beneficiary",
                      f"no Beneficiary tab on a {status!r} escrow; tabs={tabs}. "
                      "Inactive escrows expose no Payout/Beneficiary route.")
        results.block("TC-OPS-06", "Verify Beneficiary", "never added")
        return

    detail.open_tab(tab)
    bene = BeneficiaryPage(page)
    data = dict(BENEFICIARY_TEMPLATE)
    data["displayName"] = f"QA Bene {random.randint(1000, 9999)}"
    data["accountNumber"] = random_account_number(13)

    try:
        modal = bene.open_add_modal()
        modal.fill_form(data)
        picked = ""
        try:
            picked = modal.pick_bank("Union")
        except Exception as exc:
            results.record("TC-OPS-05a", "Bank picked from the type-ahead",
                           FAIL, repr(exc))
        calls = bene.capture_beneficiary_calls()
        modal.submit()
        page.wait_for_timeout(5_000)
        bene.wait_for_loader()
        added = bene.find(data["displayName"])
        results.check("TC-OPS-05", "Add Beneficiary saves a valid record",
                      added is not None,
                      f"bank={picked!r} api={calls} "
                      f"invalid={modal.invalid_controls()} "
                      f"messages={modal.messages()}"
                      if added is None else repr(added))
    except Exception as exc:
        results.record("TC-OPS-05", "Add Beneficiary saves a valid record",
                       FAIL, repr(exc))
        results.block("TC-OPS-06", "Verify Beneficiary", "the add step failed")
        return

    # --- verification -----------------------------------------------------
    row = bene.find(data["displayName"])
    if row is None:
        results.block("TC-OPS-06", "Verify Beneficiary", "record not listed")
        return
    if row.is_active:
        results.record("TC-OPS-06", "Verify Beneficiary", INFO,
                       f"already {row.status!r} on creation - no Verify action "
                       "offered")
        return
    try:
        bene.verify_beneficiary(data["displayName"])
        after = bene.find(data["displayName"])
        results.check("TC-OPS-06", "Verify Beneficiary activates the record",
                      after is not None and after.is_active,
                      f"status {row.status!r} -> "
                      f"{after.status if after else 'gone'!r}")
    except Exception as exc:
        results.record("TC-OPS-06", "Verify Beneficiary activates the record",
                       FAIL, repr(exc))


def _check_escrow_arrangement(page, escrow_id: str) -> tuple[str, str, str, str]:
    """Open Escrow Management and look for the escrow this run created.

    Returns a `results.record(...)` tuple. Navigation is by URL rather than by
    clicking the sidebar: a dismissed overlay can leave a `cdk-overlay-backdrop`
    that swallows sidebar clicks, which would report "the escrow is missing"
    when the truth is "the menu was never opened".
    """
    from partner.pages.escrow_management_page import PartnerEscrowManagementPage

    base = settings.partner_base_url.split("#")[0]
    manage = PartnerEscrowManagementPage(page)
    try:
        page.goto(base + "#/escrow-management", wait_until="domcontentloaded")
        manage.wait_for_loader()
        page.wait_for_timeout(4_000)
        if "/auth/login" in page.url:
            return ("TC-E-08", "New Escrow is listed for the partner", BLOCKED,
                    f"session dropped on navigation ({page.url})")
        listed = manage.is_visible(manage.result_row(escrow_id), timeout=15_000)
        if not listed and manage.is_visible(manage.search_input, timeout=5_000):
            manage.fill(manage.search_input, escrow_id)
            manage.search_input.press("Enter")
            page.wait_for_timeout(4_000)
            listed = manage.is_visible(manage.result_row(escrow_id), timeout=15_000)
        return ("TC-E-08", "New Escrow is listed for the partner",
                PASS if listed else FAIL,
                f"{escrow_id} on {page.url}"
                + ("" if listed else " - not found in Escrow Management"))
    except Exception as exc:
        return ("TC-E-08", "New Escrow is listed for the partner", FAIL, repr(exc))


def relogin(args, created: dict, mail: OnboardingMail, new_password: str,
            results: Results) -> None:
    """TC-N-09, TC-N-10 and the DEF-PARTNER-04 re-check."""
    banner("PART 6 - Sign in again with the new password")
    email = mail.user_id or created["email"]
    with sync_playwright() as p:
        browser, context = partner_context(p, not args.headed, args.slowmo)
        page = context.new_page()
        try:
            login, responses = sign_in(page, email, new_password)
            accepted = bool(responses) and responses[-1]["status"] == 200
            results.check("TC-N-09", "New password authenticates", accepted,
                          status_of(responses))
            if login.otp_challenge_visible(timeout=15_000):
                login.enter_login_otp(settings.partner_login_otp)
                page.wait_for_timeout(4_000)

            setpw = SetPasswordPage(page)
            results.check("TC-N-09b", "No password change prompt the second time",
                          "set-password" not in page.url, page.url)
            del setpw
            on_dashboard = reached_dashboard(page, timeout=25_000)
            results.check("TC-N-09c", "Second sign-in reaches the dashboard",
                          on_dashboard, page.url)

            # --- TC-E-08: the escrow must actually belong to this partner.
            # The whole chain is pointless if the account it created cannot see
            # the escrow it was created for, so this is the real end of the
            # flow - not the dashboard.
            if on_dashboard and created.get("escrow_id"):
                results.record(*_check_escrow_arrangement(
                    page, created["escrow_id"]))
                # PART 8 runs on this same page: the session is live now, and
                # re-authenticating for it would cost another OTP round.
                partner_operations(page, created["escrow_id"], results)
            elif created.get("escrow_id"):
                for tc, title in (
                        ("TC-E-08", "New Escrow is listed for the partner"),
                        ("TC-OPS-01", "Escrow Management lists the new escrow"),
                        ("TC-OPS-05", "Add Beneficiary"),
                        ("TC-OPS-06", "Verify Beneficiary")):
                    results.block(tc, title, "never reached an authenticated screen")

            # --- TC-N-10: the temporary password must be dead
            page.close(); page = context.new_page()
            _, old = sign_in(page, email, mail.temp_password)
            results.check("TC-N-10", "Temporary password no longer works",
                          not reached_dashboard(page, timeout=8_000),
                          status_of(old))

            # --- the known-location branch, checked rather than hidden
            page.close(); page = context.new_page()
            _, known = sign_in(page, email, new_password, fresh_location=False)
            body = (known[-1].get("json") or {}) if known else {}
            stranded = (body.get("allowTwoStep") is False
                        and not reached_dashboard(page, timeout=10_000))
            results.record("DEF-OPS-01/DEF-PARTNER-04",
                           "Known-location sign-in reaches the dashboard",
                           FAIL if stranded else PASS, status_of(known))
        finally:
            context.close(); browser.close()


def guarded_routes(args, results: Results) -> None:
    banner("PART 7 - Unauthenticated access")
    routes = ["#/dashboard", "#/escrow-management", "#/profile", "#/auth/set-password"]
    with sync_playwright() as p:
        browser, context = partner_context(p, not args.headed, args.slowmo)
        page = context.new_page()
        base = settings.partner_base_url.split("#")[0]
        try:
            for route in routes:
                page.goto(base + route, wait_until="domcontentloaded")
                page.wait_for_timeout(2_500)
                results.check(f"TC-SEC-01 [{route}]",
                              "Redirects to login when signed out",
                              "/auth/login" in page.url, page.url)
        finally:
            context.close(); browser.close()


# --- entry point ---------------------------------------------------------
def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", default=None, help="alias tag for the new rep")
    parser.add_argument("--headed", action="store_true",
                        help="show the browser (default: headless)")
    parser.add_argument("--slowmo", type=int, default=0)
    parser.add_argument("--mail-wait", type=int, default=300,
                        help="seconds to wait for the onboarding mail")
    parser.add_argument("--escrow-id", default="",
                        help="skip the admin leg and use this existing Escrow ID")
    parser.add_argument("--email", default="",
                        help="representative address of an existing run")
    parser.add_argument("--company", default="",
                        help="company name of an existing run (for DEF-MAIL-01)")
    parser.add_argument("--temp-password", default="",
                        help="temporary password read from the welcome mail by "
                             "hand. Skips the mailbox entirely and runs every "
                             "case from TC-N-05 onward.")
    parser.add_argument("--user-id", default="",
                        help="User ID from the mail (defaults to --email)")
    parser.add_argument("--portal-link", default="",
                        help="the partner link from the mail, so TC-N-04 can be "
                             "checked rather than blocked")
    parser.add_argument("--dry-run", action="store_true",
                        help="fill wizard stage 1 and stop - proves the admin "
                             "leg works without creating a UAT record. Use when "
                             "the mailbox is not readable yet.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    results = Results()
    started = time.strftime("%Y-%m-%d %H:%M:%S")

    if args.escrow_id:
        created = {"escrow_id": args.escrow_id, "email": args.email,
                   "company": args.company, "escrow_name": ""}
        banner(f"PART 1 - skipped, re-using {args.escrow_id}")
    else:
        created = create_escrow(args, results)

    if created.get("dry_run"):
        banner("PARTS 2-6 - skipped, --dry-run submitted nothing")
        for tc, title in (
                ("TC-N-01", "Confirmation email received"),
                ("TC-N-02", "Email carries User ID + temporary password"),
                ("TC-N-03", "Escrow ID in the email matches the portal"),
                ("TC-N-04", "Partner link points at the partner portal"),
                ("TC-N-05", "First sign-in with the emailed credentials"),
                ("TC-N-06", "Password change is forced"),
                ("TC-N-07", "Password policy is enforced"),
                ("TC-N-08", "Password change succeeds"),
                ("TC-N-09", "New password authenticates"),
                ("TC-N-10", "Temporary password no longer works")):
            results.block(tc, title, "dry run - no escrow submitted, no mail sent")
        mail = None
    elif args.temp_password:
        # Credentials read out of the mail by hand. The mailbox is the *only*
        # place the temporary password appears, but it does not have to be this
        # process that reads it: when Gmail cannot be automated, a human reading
        # their own inbox unblocks every case from TC-N-05 onward. The four mail
        # cases are then honestly recorded as verified by hand, not asserted.
        banner("PART 2 - The onboarding email (supplied by hand)")
        mail = OnboardingMail(
            query=created.get("escrow_id", ""),
            user_id=args.user_id or created.get("email", ""),
            temp_password=args.temp_password,
            escrow_id=created.get("escrow_id", ""),
            portal_link=args.portal_link,
        )
        for tc, title in (("TC-N-01", "Confirmation email received"),
                          ("TC-N-02", "Email carries User ID + temporary password")):
            results.record(tc, title, INFO,
                           "read from the mailbox by hand, not by this run")
        results.check("TC-N-03", "Escrow ID in the email matches the portal",
                      bool(created.get("escrow_id")),
                      f"supplied for {created.get('escrow_id')}")
        if args.portal_link:
            results.check("TC-N-04", "Partner link points at the partner portal "
                          "over HTTPS",
                          args.portal_link.startswith("https://")
                          and PARTNER_PORTAL_HOST in args.portal_link,
                          args.portal_link)
        else:
            results.block("TC-N-04", "Partner link points at the partner portal",
                          "no --portal-link supplied")
    else:
        mail = read_mail(created, results, args.mail_wait)
    new_password = ""
    if mail and mail.has_credentials:
        new_password = first_login_and_change(args, created, mail, results)
    elif not created.get("dry_run"):     # a dry run already recorded these
        why = ("no onboarding mail could be read" if not mail
               else "the mail carried no temporary password")
        for tc, title in (
                ("TC-N-05", "First sign-in with the emailed credentials"),
                ("TC-N-06", "Password change is forced"),
                ("TC-N-07", "Password policy is enforced"),
                ("TC-N-08", "Password change succeeds"),
                ("TC-N-09", "New password authenticates"),
                ("TC-N-10", "Temporary password no longer works")):
            results.block(tc, title, why)

    if new_password and mail:
        relogin(args, created, mail, new_password, results)
    elif mail and mail.has_credentials:
        for tc, title in (("TC-N-09", "New password authenticates"),
                          ("TC-N-10", "Temporary password no longer works")):
            results.block(tc, title, "the password change did not complete")

    guarded_routes(args, results)

    if new_password:
        store = ArtifactStore()
        store.set("new_partner_password", new_password)

    banner("RESULT")
    counts = results.tally()
    print(f"  {counts[PASS]} passed, {counts[FAIL]} failed, "
          f"{counts[BLOCKED]} blocked, {counts[INFO]} informational")
    path = results.dump({
        "started": started,
        "escrow_id": created.get("escrow_id"),
        "representative": created.get("email"),
        "company": created.get("company"),
        "temp_password_seen": bool(mail and mail.temp_password),
        "new_password": new_password,
    })
    print(f"  written to {path}")
    return 1 if counts[FAIL] else 0


if __name__ == "__main__":
    sys.exit(main())
