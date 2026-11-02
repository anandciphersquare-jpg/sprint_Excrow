"""Partner-suite fixtures: isolated context, error monitor, logged-in page."""
from __future__ import annotations

import pytest
from playwright.sync_api import Browser

from config.settings import settings
from core.errors import PageErrorMonitor
from partner.pages.login_page import PartnerLoginPage


@pytest.fixture
def partner_monitor() -> PageErrorMonitor:
    return PageErrorMonitor()


@pytest.fixture
def partner_context(browser: Browser, browser_context_args):
    context = browser.new_context(**browser_context_args)
    context.set_default_timeout(settings.default_timeout_ms)
    yield context
    context.close()


@pytest.fixture
def partner_page(partner_context, partner_monitor):
    page = partner_context.new_page()
    partner_monitor.attach(page)
    yield page


@pytest.fixture
def logged_in_partner_page(partner_page):
    """A fully authenticated partner session.

    Partner login is two-step and the second factor is a 4-digit code sent by
    email, so this cannot run unattended without mailbox access. Rather than
    hang or fail obscurely at the OTP screen, it skips with an actionable
    message unless a code is supplied for the run.
    """
    if not settings.partner_otp:
        pytest.skip(
            "Partner login needs an emailed OTP. Re-run with the code from the "
            "mailbox, e.g. PARTNER_OTP=1234 pytest -m partner "
            "(codes expire, so fetch it immediately before the run)."
        )
    login = PartnerLoginPage(partner_page)
    login.open()
    login.login(settings.partner_username, settings.partner_password,
                otp=settings.partner_otp)
    login.assert_logged_in()
    return partner_page
