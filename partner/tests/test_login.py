"""Partner authentication - everything verifiable without mailbox access.

Partner login is two-step: email + password, then a 4-digit code **emailed** to
the partner. Without a mailbox these tests stop at the OTP challenge, which is
still enough to prove the credential step, the challenge itself and the route
guard behave correctly. Tests needing a real session live behind the
`logged_in_partner_page` fixture and skip until `PARTNER_OTP` is supplied.
"""
import re

import pytest

from config.settings import settings
from partner.pages.login_page import PartnerLoginPage

# Deep links that must not be reachable without a session.
GUARDED_ROUTES = ["#/dashboard", "#/excrow/dashboard", "#/escrow-management",
                  "#/profile"]


@pytest.fixture
def login_page(partner_page) -> PartnerLoginPage:
    page = PartnerLoginPage(partner_page)
    page.open()
    return page


# =========================== positive ====================================

@pytest.mark.partner
@pytest.mark.smoke
def test_login_page_renders(login_page):
    assert login_page.username_input.is_visible()
    assert login_page.password_input.is_visible()
    assert login_page.login_button.is_visible()
    assert login_page.forgot_password_link.is_visible(), \
        "partners need a self-service recovery route"


@pytest.mark.partner
@pytest.mark.smoke
def test_valid_credentials_reach_the_otp_challenge(login_page, partner_monitor):
    """The credential step succeeds and hands off to the second factor.

    This is as far as an unattended run can get: the code is emailed.
    """
    login_page.submit_credentials(settings.partner_username,
                                  settings.partner_password)
    assert login_page.otp_challenge_visible(), \
        "valid credentials should raise the OTP challenge"
    assert login_page.otp_boxes.count() == 4, "expected a 4-digit code entry"
    assert login_page.verify_button.is_visible()
    assert not partner_monitor.page_errors, partner_monitor.summary()


@pytest.mark.partner
@pytest.mark.security
def test_otp_target_address_is_masked(login_page):
    """The challenge must not spell out the full address it mailed."""
    login_page.submit_credentials(settings.partner_username,
                                  settings.partner_password)
    assert login_page.otp_challenge_visible()
    masked = login_page.masked_email()
    assert "*" in masked, f"address was not masked: {masked!r}"
    local_part = settings.partner_username.split("@")[0]
    assert local_part not in masked, \
        f"masked address still exposes the full local part: {masked!r}"


@pytest.mark.partner
@pytest.mark.regression
def test_otp_resend_is_rate_limited(login_page):
    """Resend is behind a countdown, so codes cannot be requested in a loop."""
    login_page.submit_credentials(settings.partner_username,
                                  settings.partner_password)
    assert login_page.otp_challenge_visible()
    resend = login_page.wait_for_resend_countdown()
    assert re.search(r"resend code in \d+\s*s", resend, re.I), \
        f"expected a resend countdown, got {resend!r}"


# =========================== negative ====================================

@pytest.mark.partner
@pytest.mark.negative
def test_login_disabled_until_the_form_is_valid(login_page):
    """Invalid input is signalled by the disabled button, not by a message."""
    assert login_page.login_button.is_disabled(), "empty form should not submit"

    login_page.fill(login_page.username_input, "not-an-email")
    login_page.fill(login_page.password_input, "something")
    login_page.password_input.press("Tab")
    assert login_page.login_button.is_disabled(), \
        "a malformed address should keep the form unsubmittable"
    assert "ng-invalid" in (login_page.username_input.get_attribute("class") or "")


@pytest.mark.partner
@pytest.mark.negative
@pytest.mark.security
def test_guarded_routes_redirect_to_login(partner_page):
    """No deep link may render partner data without a session."""
    base = settings.partner_base_url.split("#")[0]
    login = PartnerLoginPage(partner_page)
    for route in GUARDED_ROUTES:
        partner_page.goto(base + route, wait_until="domcontentloaded")
        partner_page.wait_for_timeout(3_000)
        assert "/auth/login" in partner_page.url, \
            f"{route} did not redirect to login (landed on {partner_page.url})"
        assert login.username_input.is_visible()


@pytest.mark.partner
@pytest.mark.negative
@pytest.mark.xfail(reason="UAT returns HTTP 500 for a wrong password instead of "
                          "401/400, and the UI surfaces nothing at all - the "
                          "user sees the form sit there. See DEF-PARTNER-01/02.",
                  strict=False)
def test_wrong_password_is_rejected_with_a_message(login_page):
    """A rejected sign-in must tell the user, and must not raise the OTP step."""
    responses = []
    login_page.page.on("response", lambda r: responses.append(r.status)
                       if r.url.endswith("/auth/login")
                       and r.request.method == "POST" else None)

    login_page.submit_credentials(settings.partner_username, "WrongPassword@123")
    login_page.page.wait_for_timeout(8_000)

    # It must not let a bad password through to the second factor.
    assert not login_page.otp_challenge_visible(timeout=3_000), \
        "a wrong password reached the OTP challenge"

    assert responses and responses[0] in (400, 401, 403), \
        f"expected a client-error status, got {responses}"
    feedback = login_page.page.locator(
        ".swal2-popup, .toast, .alert, .text-danger, .invalid-feedback"
    )
    assert feedback.count() and feedback.first.is_visible(), \
        "no failure message was shown to the user"
