#!/usr/bin/env python3
"""The full chain, in two phases that can be run separately.

    PHASE 1 (admin)    Super Admin signs in once and creates an Escrow for
                       every create-case the suite knows about - the default
                       payload, all 7 Account Type combinations, and Fixed x
                       Percentage across each charge frequency. 14 escrows.
    PHASE 2 (partner)  Gmail is opened, the welcome mail for the chain escrow
                       is searched by its Escrow ID, the temporary password is
                       read out of it, the partner signs in **through the link
                       in that mail**, is forced through the password change,
                       signs in again with the new password - and then the
                       existing partner / partner_ops pytest suites are run
                       against that brand-new account and that escrow.

Run them together, or one at a time:

    python scripts/run_chain.py --phase all
    python scripts/run_chain.py --phase admin      # create everything, stop
    python scripts/run_chain.py --phase partner    # mail -> login -> modules

The two phases hand over through `shared/chain_state.json`, so the partner
phase can be run minutes or days after the admin phase, as many times as you
like, without creating another escrow. It can also run entirely on its own
against an escrow this script never created:

    python scripts/run_chain.py --phase partner \\
        --escrow-id EXC-0826-962517 --email anand.kumar+new4494@paysprint.in

Design decisions worth knowing before changing this:

* **Exactly one escrow gets a brand-new representative.** All 14 are created,
  but only the chain variant (`--chain-variant`, default `happy`) is filed
  under a fresh email/company/name/mobile - so only it produces a welcome mail
  carrying a User ID and temporary password. The other 13 use the existing
  `REPRESENTATIVE_EMAIL`, which the portal auto-fills. That is deliberate: a
  fresh representative per variant would leave 14 new partner accounts in
  shared UAT on every run, and the mail -> password -> login chain is proven
  just as well by one.
* **Every identifying field of that representative is randomised** - email,
  company, first/last name *and* mobile. Reusing any one of them attaches the
  escrow to an existing partner and no credentials are issued (DEF-MAIL-01).
* **State is written after every single create**, not at the end of the phase.
  A crash on variant 9 must not lose the eight real UAT records already made.
* **A variant that fails does not stop the phase.** It is recorded FAIL, the
  router is driven back to the escrow dashboard, and the next variant starts
  clean - so one broken pricing form does not hide the other thirteen results.
* **The module suites run in a subprocess with the new credentials injected**
  (`PARTNER_USERNAME` / `PARTNER_PASSWORD` / `PARTNER_OPS_ESCROW_ID`) rather
  than being reimplemented here. `load_dotenv()` does not override a variable
  that is already set, so the injected values win over `.env`.
* **Nothing that spends the wallet runs by default.** The partner_ops suite
  fences its financial tests behind `PARTNER_OPS_SPEND` /
  `PARTNER_OPS_CREATE_DATA`; pass `--spend` to lift both.

The run prints a scored table and writes `reports/chain_run.json`.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = Path(__file__).resolve().parent
# Run as `python scripts/run_chain.py` from the repo root: that puts `scripts/`
# on the path but not the root, so the suite packages are invisible without the
# first insert. The second lets us reuse run_onboarding_e2e's helpers rather
# than growing a second copy of them.
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(SCRIPTS))

from playwright.sync_api import sync_playwright  # noqa: E402

from admin.data.escrow_data import (EscrowData, NewRepresentative,  # noqa: E402
                                    unique_alias_tag)
from admin.pages.escrow_creation_flow import EscrowCreationFlow  # noqa: E402
from admin.pages.login_page import AdminLoginPage  # noqa: E402
from admin.pages.service_pricing_page import (FIXED, FREQUENCIES,  # noqa: E402
                                              PERCENTAGE)
from config.settings import settings  # noqa: E402
from core.artifact_store import ArtifactStore  # noqa: E402
from core.credentials import CredentialStore  # noqa: E402
from core.mailbox import MailboxUnavailable, OnboardingMail  # noqa: E402
from partner.pages.set_password_page import SetPasswordPage  # noqa: E402
from partner_ops.pages.login_page import PartnerOpsLoginPage  # noqa: E402

from run_onboarding_e2e import (BLOCKED, FAIL, INFO, PASS,  # noqa: E402
                                PARTNER_PORTAL_HOST, Results, banner,
                                open_mailbox, partner_context,
                                reached_dashboard, sign_in, status_of,
                                strong_password)

STATE_PATH = ROOT / "shared" / "chain_state.json"
RESULTS_PATH = ROOT / "reports" / "chain_run.json"
SCREENSHOT_DIR = ROOT / "reports" / "screenshots"

# Mirrors admin/tests/test_create_escrow.py - the combination decides which
# Service Pricing sections render, so each is a genuinely different form.
ACCOUNT_TYPE_COMBINATIONS = [
    ["Payout"],
    ["UPI Collection"],
    ["VID Collection"],
    ["Payout", "UPI Collection"],
    ["Payout", "VID Collection"],
    ["UPI Collection", "VID Collection"],
    ["Payout", "UPI Collection", "VID Collection"],
]

DEFAULT_MODULE_TARGETS = ["partner/tests", "partner_ops/tests"]


# --- the create matrix ---------------------------------------------------
@dataclass(frozen=True)
class Variant:
    """One create-case: an id to select it by, a title, and an EscrowData payload."""
    id: str
    title: str
    kwargs: dict = field(default_factory=dict)


def build_variants() -> list[Variant]:
    """Every escrow-creation case the admin suite covers, as one flat list."""
    variants = [Variant(
        "happy", "Default payload (Payout + VID Collection, Percentage/Monthly)")]

    for combo in ACCOUNT_TYPE_COMBINATIONS:
        slug = "-".join(a.split()[0].lower() for a in combo)
        variants.append(Variant(
            f"acct-{slug}", f"Account Type: {' + '.join(combo)}",
            {"account_types": list(combo)}))

    for rate_type in (FIXED, PERCENTAGE):
        for frequency in FREQUENCIES:
            variants.append(Variant(
                f"price-{rate_type.lower()}-{frequency.lower()}",
                f"Pricing: {rate_type} charges, {frequency} maintenance",
                {"rate_type": rate_type, "charge_frequency": frequency}))
    return variants


def select_variants(spec: str) -> list[Variant]:
    """Resolve `--variants` to a list, failing loudly on an unknown id."""
    everything = build_variants()
    if not spec or spec.strip().lower() == "all":
        return everything

    by_id = {v.id: v for v in everything}
    chosen, unknown = [], []
    for raw in spec.split(","):
        wanted = raw.strip()
        if not wanted:
            continue
        if wanted in by_id:
            chosen.append(by_id[wanted])
        else:
            unknown.append(wanted)
    if unknown:
        raise SystemExit(
            f"Unknown variant id(s): {', '.join(unknown)}\nAvailable:\n  "
            + "\n  ".join(v.id for v in everything))
    return chosen


# --- state ----------------------------------------------------------------
def load_state() -> dict:
    if not STATE_PATH.exists():
        return {}
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8") or "{}")
    except json.JSONDecodeError:
        return {}


def save_state(state: dict) -> Path:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, indent=2), encoding="utf-8")
    return STATE_PATH


def admin_context_args() -> dict:
    """Same context the admin conftest builds - geolocation is mandatory."""
    return {
        "ignore_https_errors": True,
        "viewport": {"width": 1440, "height": 900},
        "accept_downloads": True,
        "permissions": ["geolocation"],
        "geolocation": {"latitude": 28.6139, "longitude": 77.2090},
        "locale": "en-IN",
    }


def reset_to_escrow_dashboard(page, flow) -> None:
    """Leave whatever the last variant left behind, and land on a usable list.

    Between variants the app can be sitting on a fully populated Review with
    unsaved changes. `EscrowNavPage.goto_escrow_dashboard` drives the router by
    URL, but the add-escrow route differs from the dashboard route only in the
    hash - so that `goto` is a *same-document* navigation, and Angular is free
    to refuse it. Observed doing exactly that: the browser stayed on variant 1's
    Review, and variant 2 then timed out waiting for a blank stage-1 form on a
    page that was never going to show one.

    Bouncing through `about:blank` forces a genuine unload, so the second goto
    is a real cross-document load and the component state cannot survive it.
    Storage - and with it the admin session - is untouched by this.
    """
    base = page.url.split("#")[0] or settings.admin_base_url.split("#")[0]
    page.goto("about:blank", wait_until="domcontentloaded")
    page.goto(f"{base}#/excrow/dashboard", wait_until="domcontentloaded")
    page.wait_for_timeout(2_000)
    flow.nav.wait_for_loader()
    flow.nav.wait_visible(flow.nav.add_new_escrow_button)


def shoot(page, name: str) -> None:
    """Best-effort evidence. Never let a screenshot failure end a run."""
    try:
        SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
        safe = re.sub(r"[^A-Za-z0-9_.-]", "_", name)[:120]
        page.screenshot(path=str(SCREENSHOT_DIR / f"chain_{safe}.png"),
                        full_page=True)
    except Exception:
        pass


# ==========================================================================
# The login OTP
# ==========================================================================
# A second Playwright driver cannot run inside the first - the sync API raises
# "It looks like you are using Playwright Sync API inside the asyncio loop" -
# and the code has to be read *while* a partner browser is mid-login. So the
# mailbox is driven as a separate process. The welcome mail has no such problem:
# it is read before the partner browser is opened.
def _mailbox_cli(*extra: str, timeout: int) -> dict:
    cmd = [sys.executable, "-m", "core.mailbox", "--json", *extra]
    try:
        out = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True,
                             timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"error": f"mailbox call timed out after {timeout}s"}
    for line in reversed((out.stdout or "").strip().splitlines()):
        try:
            return json.loads(line)
        except json.JSONDecodeError:
            continue
    return {"error": (out.stderr or out.stdout or "no output").strip()[:200]}


def otp_snapshot() -> str | None:
    """Newest OTP-mail row id *before* a login, so a stale code is not reused.

    Returns None when the snapshot could not be taken - `find_login_otp` then
    accepts whatever is newest, which is the best available behaviour.
    """
    if not settings.partner_otp_from_mail:
        return None
    result = _mailbox_cli("--otp-snapshot", timeout=180)
    if "error" in result:
        print(f"    could not snapshot the mailbox: {result['error']}")
        return None
    return result.get("row_id") or None


def login_otp(after_row_id: str | None = None, *, length: int = 4) -> str:
    """The code to type. From Gmail when asked for, otherwise UAT's fixed one.

    Off by default (`PARTNER_OTP_FROM_MAIL`): UAT accepts a fixed code, that
    costs no mailbox round-trip, and turning it on makes every partner sign-in
    depend on a live Gmail session. A mailbox failure falls back to the fixed
    code rather than taking the login down, and says so.
    """
    if not settings.partner_otp_from_mail:
        return settings.partner_login_otp

    wait = settings.partner_otp_mail_wait
    extra = ["--otp", "--otp-length", str(length), "--wait", str(wait)]
    if after_row_id:
        extra += ["--after-row-id", after_row_id]
    print(f"    reading the login OTP from Gmail (up to {wait}s)", flush=True)
    result = _mailbox_cli(*extra, timeout=wait + 180)
    code = result.get("otp") or ""
    if code:
        print(f"    OTP from mail: {code}")
        return code
    print(f"    no OTP from mail ({result.get('error', 'not found')}); "
          f"falling back to PARTNER_LOGIN_OTP")
    return settings.partner_login_otp


def browser_is_gone(page, exc: Exception) -> bool:
    """Has the browser died, rather than this one variant having failed?

    Told apart because the two need opposite verdicts: a rejected payload is a
    FAIL worth reporting, while a closed browser makes every later variant
    unattemptable and would otherwise fill the report with identical
    `TargetClosedError`s that look like fourteen product defects.
    """
    if "TargetClosedError" in type(exc).__name__:
        return True
    if "has been closed" in str(exc) or "Target page, context or browser" in str(exc):
        return True
    try:
        return page.is_closed()
    except Exception:
        return True


# ==========================================================================
# PHASE 1 - admin
# ==========================================================================
def phase_admin(args, results: Results, state: dict) -> dict:
    """Create one escrow per variant in a single Super Admin session."""
    variants = select_variants(args.variants)
    chain_id = args.chain_variant
    if chain_id not in {v.id for v in variants}:
        # The chain variant is the only one that yields a temporary password.
        # Silently creating 14 escrows and no credentials would look like a
        # working run and block every partner case, so say so up front.
        raise SystemExit(
            f"--chain-variant {chain_id!r} is not in the selected variants. "
            f"Selected: {', '.join(v.id for v in variants)}")

    if args.dry_run and len(variants) > 1:
        # Measured against UAT: a wizard that is filled but never submitted is
        # persisted server-side as a draft, and "Add New Escrow" *resumes that
        # draft* instead of opening a blank form - the resumed Review survives a
        # full page reload, so it is the server's copy, not stale client state.
        # A looping dry run would therefore test variant 1's payload over and
        # over. There is no discard control on the dashboard (the only row
        # action is an edit icon), so this cannot be worked around from here.
        raise SystemExit(
            "--dry-run can only run one variant at a time.\n"
            "  An unsubmitted wizard is kept as a draft, and 'Add New Escrow' "
            "resumes it rather than\n"
            "  starting a blank form - so variants 2+ would re-test variant 1. "
            "Pick one, e.g.\n"
            f"      python scripts/run_chain.py --phase admin --dry-run "
            f"--variants {variants[0].id} --chain-variant {variants[0].id}\n"
            "  Drop --dry-run to create the records for real, which leaves no "
            "draft behind.")

    # `--representative` reuses a partner who has already been through the
    # onboarding. The escrow is then filed under the *existing* record (the
    # portal auto-fills the rest readonly) and no credentials are issued - which
    # is the point: that partner's password is already in the credential store,
    # so the partner phase signs in with it and never opens Gmail. Without the
    # flag a brand-new identity is minted and the mail leg is required.
    reuse = (args.representative or "").strip()
    rep = None if reuse else NewRepresentative.with_tag(
        args.tag or unique_alias_tag())
    if reuse:
        known = CredentialStore().get(reuse)
        if not (known and known.is_onboarded):
            print(f"  note: {reuse} has no saved password yet. The escrow will "
                  f"be filed under it,\n  but the partner phase will still need "
                  f"the mailbox (or --temp-password).\n")

    banner(f"PHASE 1 - Super Admin creates {len(variants)} escrows")
    if rep:
        print(f"  chain variant  : {chain_id}  (the only one with a fresh rep)")
        print(f"  representative : {rep.email}")
        print(f"  name / company : {rep.first_name} {rep.last_name} / "
              f"{rep.company_name}")
        print(f"  mobile         : {rep.mobile}   (all four fresh - DEF-MAIL-01)\n")
    else:
        print(f"  chain variant  : {chain_id}")
        print(f"  representative : {reuse}  (existing partner - reused)")
        print("  no credentials will be issued, and no mail is needed\n")

    state.setdefault("chain", {})
    state["admin"] = {
        "started": time.strftime("%Y-%m-%d %H:%M:%S"),
        "chain_variant": chain_id,
        "requested": [v.id for v in variants],
        "escrows": [],
    }
    save_state(state)

    failures = 0
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=not args.headed,
                                             slow_mo=args.slowmo)
        context = browser.new_context(**admin_context_args())
        context.set_default_timeout(settings.default_timeout_ms)
        page = context.new_page()
        try:
            try:
                login = AdminLoginPage(page)
                login.open()
                login.login(settings.admin_username, settings.admin_password,
                            settings.admin_pin)
                login.assert_dashboard_loaded()
                results.record("TC-A-01", "Super Admin signs in", PASS, page.url)
            except Exception as exc:
                # Without a session there is nothing to create, so every variant
                # is BLOCKED - not FAILED. They were never attempted.
                results.record("TC-A-01", "Super Admin signs in", FAIL, repr(exc))
                shoot(page, "admin_login_fail")
                for variant in variants:
                    results.block(f"TC-CREATE [{variant.id}]", variant.title,
                                  "no Super Admin session")
                return state

            flow = EscrowCreationFlow(page)
            for index, variant in enumerate(variants, start=1):
                is_chain = variant.id == chain_id
                marker = "  <- chain" if is_chain else ""
                print(f"\n  ({index}/{len(variants)}) {variant.id}{marker}",
                      flush=True)

                payload = dict(variant.kwargs)
                if is_chain:
                    if rep:
                        payload["new_representative"] = rep
                    else:
                        # Existing partner: the portal auto-fills first/last
                        # name, company and mobile readonly, and issues nothing.
                        payload["representative_email"] = reuse
                data = EscrowData(**payload)

                record = {
                    "variant": variant.id,
                    "title": variant.title,
                    "escrow_id": "",
                    "escrow_name": data.name,
                    "account_types": list(data.account_types),
                    "rate_type": data.rate_type,
                    "charge_frequency": data.charge_frequency,
                    "representative": data.rep_email,
                    "is_chain": is_chain,
                    "status": FAIL,
                    "listed": False,
                    "error": "",
                }
                try:
                    if index > 1:
                        reset_to_escrow_dashboard(page, flow)
                    if args.dry_run:
                        # Every stage this variant differs in - including the
                        # Service Pricing form the account-type combination
                        # decides - driven all the way to Review, and stopped
                        # there. Proves the payload is accepted without leaving
                        # a record or burning a temporary password.
                        flow.open_new_escrow_form(from_dashboard=index > 1)
                        escrow_id = flow.complete_project_information(data)
                        flow.complete_service_pricing(data)
                        flow.complete_documents(data)
                        record["escrow_id"] = escrow_id
                        record["status"] = PASS
                        record["dry_run"] = True
                        results.record(f"TC-CREATE [{variant.id}]", variant.title,
                                       PASS,
                                       f"dry run - reached Review with {escrow_id}, "
                                       "nothing submitted")
                        results.block(f"TC-LISTED [{variant.id}]",
                                      "Created escrow appears on the admin dashboard",
                                      "dry run - nothing submitted")
                        continue

                    # `from_dashboard` after the first create: a submitted wizard
                    # leaves an overlay backdrop that swallows the sidebar click.
                    escrow_id = flow.create(data, from_dashboard=index > 1)
                    record["escrow_id"] = escrow_id
                    record["status"] = PASS
                    results.record(f"TC-CREATE [{variant.id}]", variant.title,
                                   PASS, escrow_id)

                    # Creation is only real if the record is actually listed.
                    listed = flow.nav.is_listed(escrow_id)
                    record["listed"] = listed
                    results.check(f"TC-LISTED [{variant.id}]",
                                  "Created escrow appears on the admin dashboard",
                                  listed, f"{escrow_id} listed={listed}")

                    if is_chain and not args.dry_run:
                        known = None if rep else CredentialStore().get(reuse)
                        state["chain"] = {
                            "variant": variant.id,
                            "escrow_id": escrow_id,
                            "escrow_name": data.name,
                            "email": rep.email if rep else reuse,
                            "company": rep.company_name if rep else (
                                known.company if known else ""),
                            "first_name": rep.first_name if rep else "",
                            "last_name": rep.last_name if rep else "",
                            "mobile": rep.mobile if rep else "",
                            # Carried forward for a reused partner so the
                            # partner phase recognises them as onboarded and
                            # skips the mail leg entirely.
                            "user_id": known.user_id if known else "",
                            "temp_password": known.temp_password if known else "",
                            "portal_link": known.portal_link if known else "",
                            "new_password": known.new_password if known else "",
                        }
                        if not rep:
                            CredentialStore().record_onboarding(
                                reuse, escrow_id=escrow_id)
                except Exception as exc:
                    failures += 1
                    record["error"] = repr(exc)[:500]
                    results.record(f"TC-CREATE [{variant.id}]", variant.title,
                                   FAIL, repr(exc))
                    shoot(page, f"create_{variant.id}")
                    results.block(f"TC-LISTED [{variant.id}]",
                                  "Created escrow appears on the admin dashboard",
                                  "nothing was created")
                    # A closed browser cannot be recovered from, and every
                    # remaining variant would record an identical TargetClosedError
                    # as though its payload had been rejected. They were never
                    # attempted, so they are BLOCKED and the loop stops.
                    if browser_is_gone(page, exc):
                        # `break` still runs the `finally` below, so this
                        # variant's record is persisted there, not here.
                        remaining = variants[index:]
                        print(f"\n  browser/context closed - skipping "
                              f"{len(remaining)} remaining variant(s)")
                        for skipped in remaining:
                            results.block(f"TC-CREATE [{skipped.id}]", skipped.title,
                                          "browser or context was closed")
                            results.block(f"TC-LISTED [{skipped.id}]",
                                          "Created escrow appears on the admin "
                                          "dashboard", "nothing was created")
                        break
                    # Leave the app somewhere the next variant can start from.
                    try:
                        reset_to_escrow_dashboard(page, flow)
                    except Exception:
                        pass
                finally:
                    # After every variant, not at the end: a crash on variant 9
                    # must not lose the eight real UAT records already made.
                    state["admin"]["escrows"].append(record)
                    save_state(state)

                if args.maxfail and failures >= args.maxfail:
                    remaining = variants[index:]
                    print(f"\n  --maxfail {args.maxfail} reached; "
                          f"skipping {len(remaining)} remaining variant(s)")
                    for skipped in remaining:
                        results.block(f"TC-CREATE [{skipped.id}]", skipped.title,
                                      f"--maxfail {args.maxfail} reached")
                    break
        finally:
            context.close()
            browser.close()

    state["admin"]["ended"] = time.strftime("%Y-%m-%d %H:%M:%S")
    save_state(state)

    chain = state.get("chain") or {}
    if chain.get("escrow_id"):
        store = ArtifactStore()
        store.set("escrow_id", chain["escrow_id"])
        store.set("escrow_name", chain["escrow_name"])
        store.set("new_partner_email", chain["email"])
        store.set("new_partner_company", chain["company"])
        store.set("partner_ops_escrow_id", chain["escrow_id"])

    created = [e for e in state["admin"]["escrows"] if e["status"] == PASS]
    print(f"\n  created {len(created)}/{len(state['admin']['escrows'])} escrow(s)")
    for entry in created:
        print(f"    {entry['escrow_id']}  {entry['variant']}")
    return state


# ==========================================================================
# PHASE 2 - the mail
# ==========================================================================
def read_welcome_mail(chain: dict, results: Results, wait: int) -> OnboardingMail | None:
    """Search Gmail for the chain escrow and parse its welcome mail."""
    banner("PHASE 2a - The welcome email")
    escrow_id = chain["escrow_id"]
    print(f"  searching Gmail for {escrow_id} (up to {wait}s)\n")

    try:
        with open_mailbox(wait) as inbox:
            found = inbox.wait_for_message(escrow_id, timeout=wait)
            results.check("TC-MAIL-01", "Welcome email received for the escrow",
                          found, f"searched {escrow_id} for up to {wait}s")
            if not found:
                return None
            mail = inbox.open_latest(escrow_id)
    except MailboxUnavailable as exc:
        for tc, title in (
                ("TC-MAIL-01", "Welcome email received for the escrow"),
                ("TC-MAIL-02", "Email carries User ID + temporary password"),
                ("TC-MAIL-03", "Escrow ID in the email matches the portal"),
                ("TC-MAIL-04", "Partner link points at the partner portal")):
            results.block(tc, title, str(exc).splitlines()[0])
        return None

    results.check("TC-MAIL-02", "Email carries User ID + temporary password",
                  mail.has_credentials,
                  f"user_id={mail.user_id!r} password_len={len(mail.temp_password)}")
    results.check("TC-MAIL-03", "Escrow ID in the email matches the portal",
                  mail.escrow_id.upper() == escrow_id.upper(),
                  f"mail={mail.escrow_id} portal={escrow_id}")
    results.check("TC-MAIL-04", "Partner link points at the partner portal over HTTPS",
                  mail.portal_link.startswith("https://")
                  and PARTNER_PORTAL_HOST in mail.portal_link,
                  mail.portal_link or "no partner link in the mail")
    # DEF-MAIL-01 tell: the greeting names the company the escrow was filed under.
    if chain.get("company"):
        results.check("DEF-MAIL-01", "Escrow filed under the new company, not an "
                      "existing partner",
                      mail.greeting.strip().lower() == chain["company"].strip().lower(),
                      f"greeting={mail.greeting!r} expected={chain['company']!r}")
    return mail


# ==========================================================================
# PHASE 2 - password change and partner login
# ==========================================================================
def open_from_mail_link(page, link: str, results: Results) -> None:
    """Enter the portal through the link the mail actually carried.

    The brief is explicit that the partner arrives via the emailed link, so it
    is followed rather than assumed equivalent to `PARTNER_BASE_URL`. Where the
    two differ that is worth knowing, so the difference is recorded.
    """
    if not link:
        results.block("TC-LOGIN-00", "Partner enters through the emailed link",
                      "the mail carried no partner link; using PARTNER_BASE_URL")
        return
    page.goto(link, wait_until="domcontentloaded")
    page.wait_for_timeout(4_000)
    on_portal = PARTNER_PORTAL_HOST in page.url
    results.check("TC-LOGIN-00", "Emailed link opens the partner portal",
                  on_portal, f"{link} -> {page.url}")


def change_password_and_login(args, chain: dict, mail: OnboardingMail,
                              results: Results) -> str:
    """Sign in with the emailed password, and replace it. Returns the new one."""
    banner("PHASE 2b - First sign-in through the emailed link, forced change")
    email = mail.user_id or chain["email"]
    temp = mail.temp_password
    new_password = strong_password()

    with sync_playwright() as playwright:
        browser, context = partner_context(playwright, not args.headed, args.slowmo)
        page = context.new_page()
        try:
            open_from_mail_link(page, mail.portal_link, results)

            before = otp_snapshot()
            login, responses = sign_in(page, email, temp)
            results.check("TC-LOGIN-01", "First sign-in with the emailed credentials "
                          "is accepted",
                          bool(responses) and responses[-1]["status"] == 200,
                          status_of(responses))
            if login.otp_challenge_visible(timeout=20_000):
                login.enter_login_otp(login_otp(before))
                page.wait_for_timeout(4_000)

            setpw = SetPasswordPage(page)
            forced = setpw.is_current(timeout=20_000)
            results.check("TC-PWD-01", "Password change is forced before the dashboard",
                          forced, page.url)
            if not forced:
                # Not necessarily a failure of the change itself - it may simply
                # never have been reached - so the later cases are BLOCKED.
                for tc, title in (("TC-PWD-02", "Password change succeeds"),
                                  ("TC-LOGIN-02", "New password authenticates")):
                    results.block(tc, title, f"never reached set-password ({page.url})")
                shoot(page, "no_forced_change")
                return ""

            print(f"  new password: {new_password}")
            change_responses = setpw.capture_change_responses()
            message = setpw.change_password(temp, new_password)
            page.wait_for_timeout(3_000)
            changed = bool(re.search(r"success|updated", message, re.I)) or bool(
                change_responses and change_responses[-1]["status"] == 200)
            results.check("TC-PWD-02", "Password change succeeds", changed,
                          f"message={message[:160]!r} "
                          f"http={[r['status'] for r in change_responses]}")
            setpw.dismiss_message()
            if not changed:
                shoot(page, "password_change_failed")
                return ""
        finally:
            shoot(page, "partner_after_change")
            context.close()
            browser.close()
    return new_password


def relogin_as_partner(args, chain: dict, mail: OnboardingMail,
                       new_password: str, results: Results) -> None:
    """Sign in again with the new password, and prove the old one is dead."""
    banner("PHASE 2c - Sign in again with the new password")
    email = mail.user_id or chain["email"]

    with sync_playwright() as playwright:
        browser, context = partner_context(playwright, not args.headed, args.slowmo)
        page = context.new_page()
        try:
            before = otp_snapshot()
            login, responses = sign_in(page, email, new_password)
            results.check("TC-LOGIN-02", "New password authenticates",
                          bool(responses) and responses[-1]["status"] == 200,
                          status_of(responses))
            if login.otp_challenge_visible(timeout=15_000):
                login.enter_login_otp(login_otp(before))
                page.wait_for_timeout(4_000)

            results.check("TC-LOGIN-03", "No password change prompt the second time",
                          "set-password" not in page.url, page.url)
            results.check("TC-LOGIN-04", "Second sign-in reaches the dashboard",
                          reached_dashboard(page, timeout=25_000), page.url)

            # The temporary password must be dead. A new context, so nothing of
            # the live session can make a rejected login look accepted.
            if not mail.temp_password:
                # A resumed run whose state never recorded it. Submitting a
                # blank password leaves the login button disabled, which raises
                # from `submit_credentials` and would take the whole phase down
                # over a case that simply cannot be judged here.
                results.block("TC-PWD-03", "Temporary password no longer works",
                              "the temporary password is not in the state file "
                              "(onboarding ran in an earlier session)")
            else:
                page.close()
                page = context.new_page()
                _, old = sign_in(page, email, mail.temp_password)
                results.check("TC-PWD-03", "Temporary password no longer works",
                              not reached_dashboard(page, timeout=8_000),
                              status_of(old))
        finally:
            context.close()
            browser.close()


# ==========================================================================
# PHASE 2 - the module suites
# ==========================================================================
def run_module_suites(args, chain: dict, results: Results) -> int:
    """Hand the new credentials to the existing pytest suites."""
    banner("PHASE 2d - Module suites, as the new partner")
    targets = args.modules or DEFAULT_MODULE_TARGETS
    env = dict(os.environ)
    env["PARTNER_USERNAME"] = chain.get("user_id") or chain["email"]
    env["PARTNER_PASSWORD"] = chain["new_password"]
    env["PARTNER_OPS_ESCROW_ID"] = chain["escrow_id"]
    env["ESCROW_ID"] = chain["escrow_id"]
    if args.spend:
        # Both gates, together: the payout/VID/reconciliation tests need
        # PARTNER_OPS_SPEND and the beneficiary create needs its own flag.
        env["PARTNER_OPS_SPEND"] = "1"
        env["PARTNER_OPS_CREATE_DATA"] = "1"

    cmd = [sys.executable, "-m", "pytest", *targets,
           "--html", "reports/chain_modules.html", "--self-contained-html"]
    if args.module_marker:
        cmd += ["-m", args.module_marker]
    if args.headed:
        cmd.append("--headed")
    if args.slowmo:
        cmd += ["--slowmo", str(args.slowmo)]
    cmd += args.pytest_args

    print(f"  escrow    : {env['PARTNER_OPS_ESCROW_ID']}")
    print(f"  partner   : {env['PARTNER_USERNAME']}")
    print(f"  spending  : {'ALLOWED' if args.spend else 'fenced off'}")
    print(f"  >>> {' '.join(cmd)}\n", flush=True)

    code = subprocess.call(cmd, cwd=str(ROOT), env=env)
    # pytest's exit codes: 0 all passed, 1 tests failed, 5 nothing collected.
    # Only 0 and 1 mean the suite actually ran and judged something.
    if code == 0:
        results.record("TC-MODULES", "Partner module suites pass as the new partner",
                       PASS, f"pytest exit 0 over {' '.join(targets)}")
    elif code == 1:
        results.record("TC-MODULES", "Partner module suites pass as the new partner",
                       FAIL, "pytest exit 1 - see reports/chain_modules.html")
    else:
        results.block("TC-MODULES", "Partner module suites pass as the new partner",
                      f"pytest exit {code} - the suite did not run to a verdict")
    return code


# ==========================================================================
# PHASE 2 - driver
# ==========================================================================
def phase_partner(args, results: Results, state: dict) -> dict:
    """Mail -> forced password change -> partner login -> module suites."""
    chain = dict(state.get("chain") or {})
    if args.escrow_id:
        chain["escrow_id"] = args.escrow_id
    if args.email:
        chain["email"] = args.email
    if args.company:
        chain["company"] = args.company

    if not chain.get("escrow_id"):
        raise SystemExit(
            "No escrow to run the partner phase against.\n"
            f"  Run the admin phase first (it writes {STATE_PATH}),\n"
            "  or pass --escrow-id EXC-... --email <representative address>.")

    banner(f"PHASE 2 - Partner chain for {chain['escrow_id']}")

    # A password saved by an *earlier* run for this same representative. The
    # `chain` slot holds only the most recent escrow, so a later `--phase admin`
    # run wipes it; the credential store is keyed per representative and does
    # not. Without this, an already-onboarded partner would be sent back to the
    # mailbox for a temporary password that is dead and a mail that is stale.
    stored = CredentialStore().get(chain.get("email", ""))
    if stored and stored.is_onboarded and not chain.get("new_password"):
        chain["new_password"] = stored.new_password
        chain["user_id"] = chain.get("user_id") or stored.user_id
        chain["portal_link"] = chain.get("portal_link") or stored.portal_link
        chain["temp_password"] = chain.get("temp_password") or stored.temp_password
        print(f"  credentials for {chain['email']} recovered from "
              f"{CredentialStore().path.name} "
              f"(changed {stored.password_changed_at}) - no mailbox needed\n")

    # The onboarding half happens exactly once per partner: after the change the
    # temporary password is dead and the portal stops forcing the set-password
    # screen. Re-running this phase - which is the whole point of splitting it -
    # must therefore pick up at the login, not replay a change that cannot
    # happen twice, and must not report that as a failure.
    if chain.get("new_password") and not args.force_password_change:
        banner("PHASE 2a/2b - skipped, this partner is already onboarded")
        print(f"  {chain.get('user_id') or chain['email']} already has a new "
              f"password from an earlier run.\n"
              f"  Pass --force-password-change to drive the onboarding again "
              f"(it will fail if the\n  temporary password has already been "
              f"used).\n")
        for tc, title in (
                ("TC-MAIL-01", "Welcome email received for the escrow"),
                ("TC-MAIL-02", "Email carries User ID + temporary password"),
                ("TC-PWD-01", "Password change is forced before the dashboard"),
                ("TC-PWD-02", "Password change succeeds")):
            results.record(tc, title, INFO, "verified by an earlier run of this phase")
        mail = OnboardingMail(
            query=chain["escrow_id"],
            user_id=chain.get("user_id") or chain.get("email", ""),
            temp_password=chain.get("temp_password", ""),
            escrow_id=chain["escrow_id"],
            portal_link=chain.get("portal_link", ""),
        )
        relogin_as_partner(args, chain, mail, chain["new_password"], results)
        if args.skip_modules:
            results.block("TC-MODULES",
                          "Partner module suites pass as the new partner",
                          "--skip-modules")
        else:
            run_module_suites(args, chain, results)
        return state

    if args.temp_password:
        # The mailbox is the only place the temporary password appears, but it
        # need not be this process that reads it: a human reading their own
        # inbox unblocks every case from the first sign-in onward. Recorded as
        # verified by hand, not asserted.
        mail = OnboardingMail(
            query=chain["escrow_id"],
            user_id=args.user_id or chain.get("email", ""),
            temp_password=args.temp_password,
            escrow_id=chain["escrow_id"],
            portal_link=args.portal_link,
        )
        banner("PHASE 2a - The welcome email (supplied by hand)")
        for tc, title in (
                ("TC-MAIL-01", "Welcome email received for the escrow"),
                ("TC-MAIL-02", "Email carries User ID + temporary password")):
            results.record(tc, title, INFO,
                           "read from the mailbox by hand, not by this run")
    else:
        mail = read_welcome_mail(chain, results, args.mail_wait)

    if not mail or not mail.has_credentials:
        why = ("no welcome mail could be read" if not mail
               else "the mail carried no temporary password")
        for tc, title in (
                ("TC-LOGIN-01", "First sign-in with the emailed credentials"),
                ("TC-PWD-01", "Password change is forced"),
                ("TC-PWD-02", "Password change succeeds"),
                ("TC-PWD-03", "Temporary password no longer works"),
                ("TC-LOGIN-02", "New password authenticates"),
                ("TC-MODULES", "Partner module suites pass as the new partner")):
            results.block(tc, title, why)
        return state

    chain["user_id"] = mail.user_id or chain.get("email", "")
    chain["temp_password"] = mail.temp_password
    chain["portal_link"] = mail.portal_link
    state["chain"] = chain
    save_state(state)
    CredentialStore().record_onboarding(
        chain["email"], user_id=chain["user_id"], company=chain.get("company", ""),
        portal_link=mail.portal_link, temp_password=mail.temp_password,
        escrow_id=chain["escrow_id"])

    new_password = change_password_and_login(args, chain, mail, results)
    if not new_password:
        for tc, title in (
                ("TC-LOGIN-02", "New password authenticates"),
                ("TC-PWD-03", "Temporary password no longer works"),
                ("TC-MODULES", "Partner module suites pass as the new partner")):
            results.block(tc, title, "the password change did not complete")
        return state

    chain["new_password"] = new_password
    state["chain"] = chain
    save_state(state)
    ArtifactStore().set("new_partner_password", new_password)
    # The moment the mailbox stops being needed for this partner. Kept per
    # representative rather than in the single `chain` slot, which the next
    # `--phase admin` run overwrites - see core/credentials.py.
    CredentialStore().record_password_change(chain["email"], new_password)
    print(f"  saved credentials for {chain['email']} - later runs sign in "
          f"without reading Gmail")

    relogin_as_partner(args, chain, mail, new_password, results)

    if args.skip_modules:
        results.block("TC-MODULES", "Partner module suites pass as the new partner",
                      "--skip-modules")
    else:
        run_module_suites(args, chain, results)
    return state


# ==========================================================================
# entry point
# ==========================================================================
def run_admin_suite(args, results: Results) -> int:
    """Every admin case that leaves no record: positive, negative, and edit.

    Runs **before** the creates. Validating the wizard after filing fourteen
    real records against it is the wrong way round - if stage 1 rejects a valid
    IFSC, that is worth knowing before fourteen escrows are made, not after.
    `-m "admin and not create"` is the whole set bar the submitting ones, so it
    covers the stage +/- suites, the Review back/edit round-trip and the
    dashboard-list edit.

    Returns the pytest exit code so the caller can gate the creates on it.
    """
    banner("PHASE 1a - Admin test set: positive, negative, edit "
           "(nothing is created)")
    cmd = [sys.executable, "-m", "pytest", "admin/tests",
           "-m", "admin and not create",
           "--html", "reports/chain_admin_modules.html", "--self-contained-html"]
    if args.headed:
        cmd.append("--headed")
    if args.slowmo:
        cmd += ["--slowmo", str(args.slowmo)]
    print(f"  >>> {' '.join(cmd)}\n", flush=True)
    code = subprocess.call(cmd, cwd=str(ROOT))
    if code == 0:
        results.record("TC-ADMIN-MODULES", "Admin module suites pass", PASS,
                       "pytest exit 0 over admin/tests")
    elif code == 1:
        results.record("TC-ADMIN-MODULES", "Admin module suites pass", FAIL,
                       "pytest exit 1 - see reports/chain_admin_modules.html")
    else:
        results.block("TC-ADMIN-MODULES", "Admin module suites pass",
                      f"pytest exit {code} - the suite did not run to a verdict")
    return code


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--phase", choices=("admin", "partner", "all"),
                        default="all",
                        help="which half to run (default: all). The two hand "
                             f"over through {STATE_PATH.name}.")

    admin = parser.add_argument_group("phase 1 - admin")
    admin.add_argument("--variants", default="all",
                       help="comma-separated variant ids, or 'all' (default). "
                            "See --list-variants.")
    admin.add_argument("--chain-variant", default="happy",
                       help="which variant gets the brand-new representative, "
                            "and so drives the mail chain (default: happy)")
    admin.add_argument("--tag", default=None,
                       help="alias tag for the new representative")
    admin.add_argument("--representative", default="", metavar="EMAIL",
                       help="file the chain escrow under an existing partner "
                            "instead of minting a new identity. No credentials "
                            "are issued, and if that address is already in "
                            "shared/partner_credentials.json the partner phase "
                            "signs in with the saved password and never opens "
                            "Gmail.")
    admin.add_argument("--maxfail", type=int, default=0,
                       help="stop the admin phase after N failed creates "
                            "(default: 0 = never stop)")
    admin.add_argument("--with-admin-suite", action="store_true",
                       help="run the full admin test set - every positive and "
                            "negative stage case, the Review back/edit "
                            "round-trip and the dashboard-list edit - BEFORE "
                            "creating anything. Nothing in it leaves a record.")
    admin.add_argument("--suite-gates-creates", action="store_true",
                       help="with --with-admin-suite, skip the creates if that "
                            "suite fails, rather than creating 14 records on "
                            "top of a known-broken wizard")
    admin.add_argument("--dry-run", action="store_true",
                       help="drive every variant through the wizard to Review "
                            "and stop - proves each payload is accepted without "
                            "leaving records in UAT. No mail is sent, so the "
                            "partner phase is blocked.")
    admin.add_argument("--list-variants", action="store_true",
                       help="print the create matrix and exit")

    partner = parser.add_argument_group("phase 2 - partner")
    partner.add_argument("--escrow-id", default="",
                         help="run the partner phase against this escrow "
                              "instead of the one in the state file")
    partner.add_argument("--email", default="",
                         help="representative address for --escrow-id")
    partner.add_argument("--company", default="",
                         help="company name for --escrow-id (for DEF-MAIL-01)")
    partner.add_argument("--mail-wait", type=int, default=300,
                         help="seconds to wait for the welcome mail (default 300)")
    partner.add_argument("--temp-password", default="",
                         help="temporary password read from the mail by hand; "
                              "skips the mailbox entirely")
    partner.add_argument("--user-id", default="",
                         help="User ID from the mail (defaults to --email)")
    partner.add_argument("--portal-link", default="",
                         help="the partner link from the mail, used with "
                              "--temp-password")
    partner.add_argument("--modules", nargs="*", default=None,
                         metavar="PATH",
                         help="pytest targets for the module suites "
                              f"(default: {' '.join(DEFAULT_MODULE_TARGETS)})")
    partner.add_argument("--module-marker", default="",
                         help="marker expression for the module suites, "
                              "e.g. 'smoke' or 'not security'")
    partner.add_argument("--force-password-change", action="store_true",
                         help="re-drive the mail and forced-change steps even "
                              "if the state file already records a new "
                              "password. Only useful for a fresh escrow.")
    partner.add_argument("--skip-modules", action="store_true",
                         help="stop after the partner login; run no suites")
    partner.add_argument("--spend", action="store_true",
                         help="lift PARTNER_OPS_SPEND and "
                              "PARTNER_OPS_CREATE_DATA - these tests move real "
                              "money and create real records in UAT")

    common = parser.add_argument_group("common")
    common.add_argument("--headed", action="store_true",
                        help="show the browser (default: headless)")
    common.add_argument("--slowmo", type=int, default=0)
    common.add_argument("pytest_args", nargs="*", default=[],
                        help="extra args forwarded to the module suites")
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    if args.list_variants:
        variants = build_variants()
        print(f"{len(variants)} create variants:\n")
        for variant in variants:
            print(f"  {variant.id:<28} {variant.title}")
        return 0

    results = Results()
    started = time.strftime("%Y-%m-%d %H:%M:%S")
    state = load_state()
    state.setdefault("started", started)

    if args.phase in ("admin", "all"):
        # Validate the wizard *before* filing records against it. Nothing in
        # this set creates anything, so it costs no UAT data to run first.
        suite_code = run_admin_suite(args, results) if args.with_admin_suite else 0
        if suite_code and args.suite_gates_creates:
            banner("PHASE 1 - skipped, the admin test set did not pass")
            print("  --suite-gates-creates is set, so no escrow was created on "
                  "top of a failing wizard.\n  See "
                  "reports/chain_admin_modules.html.\n")
            for variant in select_variants(args.variants):
                results.block(f"TC-CREATE [{variant.id}]", variant.title,
                              "the admin test set failed and "
                              "--suite-gates-creates is set")
        else:
            state = phase_admin(args, results, state)

    if args.phase in ("partner", "all"):
        chain = state.get("chain") or {}
        if args.phase == "all" and args.dry_run:
            banner("PHASE 2 - skipped, --dry-run submitted nothing")
            for tc, title in (
                    ("TC-MAIL-01", "Welcome email received for the escrow"),
                    ("TC-MAIL-02", "Email carries User ID + temporary password"),
                    ("TC-LOGIN-01", "First sign-in with the emailed credentials"),
                    ("TC-PWD-01", "Password change is forced"),
                    ("TC-PWD-02", "Password change succeeds"),
                    ("TC-PWD-03", "Temporary password no longer works"),
                    ("TC-LOGIN-02", "New password authenticates"),
                    ("TC-MODULES", "Partner module suites pass as the new partner")):
                results.block(tc, title, "dry run - no escrow submitted, no mail sent")
        elif args.phase == "all" and not (args.escrow_id or chain.get("escrow_id")):
            # The admin phase ran but the chain variant never produced an
            # escrow, so there is no welcome mail to read. Say which cases that
            # blocks rather than crashing on a missing key.
            for tc, title in (
                    ("TC-MAIL-01", "Welcome email received for the escrow"),
                    ("TC-LOGIN-01", "First sign-in with the emailed credentials"),
                    ("TC-PWD-02", "Password change succeeds"),
                    ("TC-MODULES", "Partner module suites pass as the new partner")):
                results.block(tc, title,
                              "the chain variant did not create an escrow")
        else:
            state = phase_partner(args, results, state)

    save_state(state)

    banner("RESULT")
    counts = results.tally()
    print(f"  {counts[PASS]} passed, {counts[FAIL]} failed, "
          f"{counts[BLOCKED]} blocked, {counts[INFO]} informational")

    chain = state.get("chain") or {}
    RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    RESULTS_PATH.write_text(json.dumps({
        "summary": counts,
        "started": started,
        "ended": time.strftime("%Y-%m-%d %H:%M:%S"),
        "phase": args.phase,
        "chain": {k: v for k, v in chain.items() if k != "temp_password"},
        "escrows": (state.get("admin") or {}).get("escrows", []),
        "cases": results.rows,
    }, indent=2), encoding="utf-8")
    print(f"  state   -> {STATE_PATH}")
    print(f"  results -> {RESULTS_PATH}")
    return 1 if counts[FAIL] else 0


if __name__ == "__main__":
    sys.exit(main())
