"""New-partner onboarding: email -> first login -> forced password change.

These are the `TC-N-*` cases from
`docs/post-creation-email-and-partner-login.md`, as pytest.

**How this suite is fed.** The chain starts in the *admin* portal and the
temporary password only exists in an email, so nothing here can invent its own
fixture data. The session-scoped `onboarding` fixture consumes what
`scripts/run_onboarding_e2e.py` recorded in `reports/onboarding_e2e.json` and
`shared/artifacts.json`; without it every test **skips with a message naming the
script**, rather than failing for want of data it was never given.

That split is deliberate. The runner is the thing that *performs* the flow -
one long, ordered, side-effecting chain that creates a real UAT escrow and burns
a real temporary password, and cannot be re-run per test. This suite is the
thing that *re-asserts* the outcome, plus the parts that stand on their own
(mail parsing, route guarding, the set-password screen's own validation). Trying
to make each `TC-N-*` an independent pytest would mean creating one escrow per
test - fifteen records per run - for no extra coverage.

Run:

    python scripts/run_onboarding_e2e.py      # perform the flow, write results
    pytest partner/tests/test_new_partner_onboarding.py -v
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.artifact_store import ArtifactStore
from core.mailbox import parse_onboarding_mail
from partner.pages.set_password_page import SetPasswordPage

RESULTS_PATH = Path("reports/onboarding_e2e.json")

RUN_HINT = ("No onboarding run recorded. Perform the flow first:\n"
            "    python scripts/run_onboarding_e2e.py\n"
            "(it creates a real UAT escrow and consumes a real temporary "
            "password, so it is a deliberate step, not a fixture.)")

pytestmark = [pytest.mark.partner, pytest.mark.onboarding]


# --- fixtures ------------------------------------------------------------
@pytest.fixture(scope="session")
def onboarding() -> dict:
    """The recorded outcome of the last `run_onboarding_e2e` run."""
    if not RESULTS_PATH.exists():
        pytest.skip(RUN_HINT)
    data = json.loads(RESULTS_PATH.read_text(encoding="utf-8"))
    data["by_id"] = {row["id"]: row for row in data.get("cases", [])}
    return data


def case(onboarding: dict, tc: str) -> dict:
    """One recorded case, or a skip explaining that it never ran."""
    row = onboarding["by_id"].get(tc)
    if row is None:
        pytest.skip(f"{tc} was not exercised by the last run. {RUN_HINT}")
    if row["status"] == "BLOCKED":
        pytest.skip(f"{tc} blocked: {row['evidence']}")
    return row


def assert_passed(onboarding: dict, tc: str) -> None:
    row = case(onboarding, tc)
    assert row["status"] == "PASS", f"{tc} {row['title']}: {row['evidence']}"


# --- Scenario 1: the recorded chain --------------------------------------
@pytest.mark.parametrize("tc", [
    "TC-N-01",    # confirmation email received
    "TC-N-02",    # email carries User ID + temporary password
    "TC-N-02b",   # the User ID is the address that was entered
    "TC-N-03",    # Escrow ID in the mail matches the portal
    "TC-N-04",    # partner link points at the partner portal over HTTPS
    "TC-N-05",    # first sign-in with the emailed credentials
    "TC-N-06",    # the password change is forced
    "TC-N-06b",   # the screen explains why
    "TC-N-06c",   # three password fields
    "TC-N-06d",   # the dashboard cannot be reached around the change
    "TC-N-08",    # the change succeeds
    "TC-N-09",    # the new password authenticates
    "TC-N-09b",   # no second prompt
    "TC-N-09c",   # the second sign-in reaches the dashboard
    "TC-N-10",    # the temporary password is dead
])
def test_recorded_case_passed(onboarding, tc):
    assert_passed(onboarding, tc)


@pytest.mark.parametrize("tc", [
    "TC-N-N01",   # wrong temporary password
    "TC-N-N01b",  # wrong 'password from email' on the change form
    "TC-N-N02",   # unknown email
    "TC-N-N02b",  # the two answer alike (no enumeration)
    "TC-N-N04",   # mismatched confirmation
])
def test_recorded_negative_passed(onboarding, tc):
    assert_passed(onboarding, tc)


def test_password_policy_enforced(onboarding):
    """TC-N-07: every stated rule rejects a password that breaks only it."""
    policy = {tc: row for tc, row in onboarding["by_id"].items()
              if tc.startswith("TC-N-07")}
    if not policy:
        pytest.skip(f"policy cases never ran. {RUN_HINT}")
    failures = {tc: row["evidence"] for tc, row in policy.items()
                if row["status"] == "FAIL"}
    assert not failures, (
        "the portal accepted passwords that violate its own policy: "
        + json.dumps(failures, indent=2))


def test_escrow_filed_under_the_new_company(onboarding):
    """DEF-MAIL-01: the mail must greet the company that was actually typed.

    The failure this guards is silent - the escrow is accepted, Review shows
    the details entered, and only the greeting reveals it was filed under a
    different partner.
    """
    assert_passed(onboarding, "DEF-MAIL-01")


# --- known defects: xfail, so they report the day they are fixed ---------
@pytest.mark.xfail(reason="DEF-PARTNER-01: a rejected login answers HTTP 500",
                   strict=False)
def test_failed_login_uses_a_client_error_status(onboarding):
    assert_passed(onboarding, "DEF-PARTNER-01")


@pytest.mark.xfail(reason="DEF-PARTNER-02: a rejected login shows nothing at all",
                   strict=False)
def test_failed_login_tells_the_user(onboarding):
    assert_passed(onboarding, "DEF-PARTNER-02")


@pytest.mark.xfail(
    reason="DEF-OPS-01/DEF-PARTNER-04: a known-location sign-in returns a token "
           "the UI discards, stranding the user on the login page",
    strict=False)
def test_known_location_sign_in_reaches_the_dashboard(onboarding):
    assert_passed(onboarding, "DEF-OPS-01/DEF-PARTNER-04")


# --- standalone: no run required -----------------------------------------
@pytest.mark.smoke
@pytest.mark.parametrize("route", [
    "#/dashboard", "#/escrow-management", "#/profile", "#/auth/set-password"])
def test_guarded_routes_redirect_to_login(partner_page, route):
    """TC-SEC-01: deep links must not be reachable without a session.

    `#/auth/set-password` is in the list on purpose - it is the one screen that
    can change a credential, so reaching it unauthenticated would matter more
    than the rest.
    """
    from config.settings import settings
    base = settings.partner_base_url.split("#")[0]
    partner_page.goto(base + route, wait_until="domcontentloaded")
    partner_page.wait_for_timeout(2_500)
    assert "/auth/login" in partner_page.url, (
        f"{route} did not redirect to login; landed on {partner_page.url}")


def test_set_password_screen_is_not_reachable_without_first_login(partner_page):
    """The forced-change screen must not render its form to a stranger."""
    page = SetPasswordPage(partner_page)
    page.open_direct()
    partner_page.wait_for_timeout(2_500)
    assert page.field_count() == 0 or "/auth/login" in partner_page.url, (
        "the set-password form rendered without a session")


# --- the parser, unit-tested against a captured mail ---------------------
SAMPLE_MAIL = """Dear QAQQOCS Ltd,

Your Escrow account has been created successfully.

User ID: anand.kumar+new22@paysprint.in
Temporary Password: @Qunn0bcGq
Your Escrow ID: EXC-0826-838459

Go to Dashboard
"""

SAMPLE_EXISTING_PARTNER_MAIL = """Dear EXCROW432226,

A new Escrow has been created for you.

Your Escrow ID: EXC-0826-147683

Go to Dashboard
"""


def test_parser_reads_the_new_partner_mail():
    mail = parse_onboarding_mail(
        SAMPLE_MAIL,
        ["https://excrow-uat.sprintnxt.in/EXcrowPartnerFrontV2/#/auth/login"])
    assert mail.user_id == "anand.kumar+new22@paysprint.in"
    assert mail.temp_password == "@Qunn0bcGq"
    assert mail.escrow_id == "EXC-0826-838459"
    assert mail.greeting == "QAQQOCS Ltd"
    assert mail.has_credentials


def test_parser_distinguishes_the_existing_partner_mail():
    """TC-E-03: the existing-partner mail must carry no credentials.

    This is the difference that matters between the two scenarios, and it is
    what makes `has_credentials` a usable assertion rather than a convenience.
    """
    mail = parse_onboarding_mail(SAMPLE_EXISTING_PARTNER_MAIL, [])
    assert mail.escrow_id == "EXC-0826-147683"
    assert not mail.has_credentials
    assert not mail.temp_password


def test_artifact_store_carries_the_run_forward():
    """The runner hands the partner identity on; without it nothing downstream
    knows which account the last run created."""
    store = ArtifactStore()
    if not store.get("new_partner_email"):
        pytest.skip(RUN_HINT)
    assert "@" in store.get("new_partner_email")
    assert store.get("escrow_id", "").upper().startswith("EXC-")
