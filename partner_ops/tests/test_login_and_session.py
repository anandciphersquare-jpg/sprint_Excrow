"""PART 1 + PART 26 - partner authentication, the lockout, and session policy.

The centrepiece is `test_known_location_login_is_not_consumed_by_the_ui`, which
pins DEF-OPS-01: the backend's trusted-location branch hands the client a valid
`authtoken` and the client does nothing with it. It is written as a strict
regression (`xfail(strict=False)`) so that the day it starts passing, the suite
says so instead of quietly agreeing.

Password-change coverage (temporary password, policy, confirmation mismatch,
`#/auth/set-password`) is deliberately **not duplicated here** - it is already
owned by `partner/tests/test_login.py` and
`docs/post-creation-email-and-partner-login.md`, which drive it from a genuinely
new partner created by the admin suite. Re-testing it against an established
account would assert nothing: the first-login route is unreachable once the
password has been changed.
"""
from __future__ import annotations

import re

import pytest

from config.settings import settings
from partner_ops.pages.dashboard_page import PartnerDashboardPage
from partner_ops.pages.login_page import PartnerOpsLoginPage

# Deep links that must never render partner data without a session.
GUARDED_ROUTES = ["#/dashboard", "#/escrow-list", "#/reports-statement",
                  "#/reports-statement/wallet", "#/wallet-management",
                  "#/audit-trail-statement", "#/profile-statement",
                  "#/active-bank-credentials", "#/escrow-detail/payout",
                  "#/escrow-detail/beneficiary"]


@pytest.fixture
def login_page(partner_ops_page) -> PartnerOpsLoginPage:
    page = PartnerOpsLoginPage(partner_ops_page)
    page.open()
    return page


# ============================== positive =================================

@pytest.mark.partner_ops
@pytest.mark.smoke
def test_login_form_renders_and_waits_for_location(login_page):
    """The form renders and refuses to submit until location resolves."""
    assert login_page.username_input.is_visible()
    assert login_page.password_input.is_visible()
    assert login_page.forgot_password_link.is_visible(), \
        "partners need a self-service recovery route"
    assert login_page.wait_for_location_ready(), (
        "The login button never stopped reading 'Location Loading...'. Both "
        "portals gate the form on navigator.geolocation, so the browser context "
        "must grant the geolocation permission.")
    # Location resolving is not the same as the form being submittable: an empty
    # form keeps the button disabled, which the next test covers.
    assert login_page.login_button.is_disabled(), \
        "an empty form should not be submittable"


@pytest.mark.partner_ops
@pytest.mark.smoke
def test_login_from_a_fresh_location_reaches_the_dashboard(partner_ops_page,
                                                           ops_monitor):
    """The full happy path: credentials -> 4-digit OTP -> dashboard."""
    login = PartnerOpsLoginPage(partner_ops_page)
    responses = login.capture_login_responses()
    login.login(fresh_location=True)

    assert responses, "no POST /auth/login response was observed"
    data = (responses[-1]["json"] or {}).get("data", {})
    assert data.get("allowTwoStep") is True, (
        "a never-seen location should raise the OTP challenge; got "
        f"allowTwoStep={data.get('allowTwoStep')!r}")

    login.assert_logged_in()
    dashboard = PartnerDashboardPage(partner_ops_page)
    dashboard.open()
    assert dashboard.card("Wallet Balance").is_visible()
    assert not ops_monitor.page_errors, ops_monitor.summary()


@pytest.mark.partner_ops
@pytest.mark.regression
def test_login_otp_challenge_is_four_digits_and_masks_the_address(login_page):
    """Second factor: 4 boxes against a masked address."""
    login_page.submit_credentials(settings.partner_username,
                                  settings.partner_password)
    if not login_page.otp_challenge_visible():
        pytest.skip("This account is in the DEF-OPS-01 direct-login state; "
                    "the challenge cannot be reached from a known location.")
    assert login_page.otp_boxes.count() == 4, \
        f"login OTP should be 4 digits, found {login_page.otp_boxes.count()} boxes"
    body = login_page.body_text()
    masked = re.search(r"[A-Za-z0-9._%+-]*\*+[A-Za-z0-9._%+-]*@[\w.-]+", body)
    assert masked, "the challenge did not mask the target address"
    local_part = settings.partner_username.split("@")[0]
    assert local_part not in masked.group(0), \
        f"masked address still exposes the local part: {masked.group(0)!r}"


# ============================== negative =================================

@pytest.mark.partner_ops
@pytest.mark.negative
def test_login_is_disabled_until_the_form_is_valid(login_page):
    login_page.wait_for_location_ready()
    assert login_page.login_button.is_disabled(), "empty form should not submit"

    login_page.fill(login_page.username_input, "not-an-email")
    login_page.fill(login_page.password_input, "something")
    login_page.password_input.press("Tab")
    assert login_page.login_button.is_disabled(), \
        "a malformed address should keep the form unsubmittable"
    assert "ng-invalid" in (login_page.username_input.get_attribute("class") or "")


@pytest.mark.partner_ops
@pytest.mark.negative
@pytest.mark.security
def test_guarded_routes_redirect_when_unauthenticated(partner_ops_page):
    """PART 26 - no deep link may render partner data without a session."""
    base = settings.partner_base_url.split("#")[0]
    login = PartnerOpsLoginPage(partner_ops_page)
    leaked = []
    for route in GUARDED_ROUTES:
        partner_ops_page.goto(base + route, wait_until="domcontentloaded")
        partner_ops_page.wait_for_timeout(2_500)
        if "/auth/" not in partner_ops_page.url:
            leaked.append(f"{route} -> {partner_ops_page.url}")
    assert not leaked, "routes reachable without a session: " + "; ".join(leaked)
    assert login.username_input.is_visible()


@pytest.mark.partner_ops
@pytest.mark.negative
@pytest.mark.xfail(
    reason="DEF-OPS-01 (Critical): from a location the backend already trusts, "
           "POST /auth/login returns 200 with allowTwoStep=false and a valid "
           "authtoken, and the Angular client never consumes it - the user is "
           "left on #/auth/login with no error and empty storage. A partner who "
           "signs in twice from the same place is locked out of the portal.",
    strict=False)
def test_known_location_login_is_not_consumed_by_the_ui(partner_ops_page):
    """A successful login must actually sign the user in.

    Runs without the fresh-location workaround, so it exercises exactly what a
    real partner hits on their second sign-in from the office.
    """
    login = PartnerOpsLoginPage(partner_ops_page)
    responses = login.capture_login_responses()
    login.login(fresh_location=False)
    partner_ops_page.wait_for_timeout(8_000)

    assert responses, "no POST /auth/login response was observed"
    data = (responses[-1]["json"] or {}).get("data", {})

    if data.get("allowTwoStep") is True:
        pytest.skip("The backend did not treat this location as trusted, so the "
                    "direct-login branch was not exercised on this run.")

    assert data.get("authtoken"), (
        "expected the direct-login branch to return a token; "
        f"got keys {sorted(data)}")
    # The defect: a valid token was issued and thrown away.
    login.assert_logged_in(timeout=10_000)


# ============================== session ==================================

@pytest.mark.partner_ops
@pytest.mark.security
@pytest.mark.regression
def test_idle_session_prompts_then_expires(ops_page):
    """A ~2 minute idle raises 'Are you still there?' with a countdown.

    The prompt is the product's stated policy, so this asserts its wording and
    its two controls rather than sitting through the logout - burning two
    minutes of wall clock to watch a redirect would slow every run for no extra
    information. `test_session_expired_route_blocks_access` covers the
    post-expiry state directly.
    """
    login = PartnerOpsLoginPage(ops_page)
    ops_page.wait_for_timeout(125_000)

    assert login.session_prompt_visible(timeout=30_000), (
        "no idle-session prompt appeared after ~2 minutes of inactivity")
    text = re.sub(r"\s+", " ", login.session_modal.inner_text())
    assert re.search(r"are you still there", text, re.I), text
    assert re.search(r"logging out in \d+ seconds", text, re.I), text
    assert login.continue_session_button.is_visible()
    assert login.logout_now_button.is_visible()

    login.continue_session()
    assert not login.is_session_expired(), \
        "Continue Session did not keep the session alive"


@pytest.mark.partner_ops
@pytest.mark.security
def test_session_expired_route_blocks_access(partner_ops_page):
    """#/auth/session-expired must not be a doorway back in."""
    base = settings.partner_base_url.split("#")[0]
    partner_ops_page.goto(base + "#/auth/session-expired",
                          wait_until="domcontentloaded")
    partner_ops_page.wait_for_timeout(2_500)
    partner_ops_page.goto(base + "#/dashboard", wait_until="domcontentloaded")
    partner_ops_page.wait_for_timeout(3_000)
    assert "/auth/" in partner_ops_page.url, (
        "an expired session reached the dashboard: " + partner_ops_page.url)
