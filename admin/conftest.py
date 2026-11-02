"""Admin-suite fixtures: isolated context, error monitor, and a ready
logged-in page so escrow tests start past login+PIN."""
from __future__ import annotations

import pytest
from playwright.sync_api import Browser

from admin.data.escrow_data import EscrowData
from admin.pages.escrow_creation_flow import EscrowCreationFlow
from admin.pages.login_page import AdminLoginPage
from config.settings import settings
from core.errors import PageErrorMonitor


@pytest.fixture
def admin_monitor() -> PageErrorMonitor:
    return PageErrorMonitor()


@pytest.fixture
def admin_context(browser: Browser, browser_context_args):
    context = browser.new_context(**browser_context_args)
    context.set_default_timeout(settings.default_timeout_ms)
    yield context
    context.close()


@pytest.fixture
def admin_page(admin_context, admin_monitor):
    page = admin_context.new_page()
    admin_monitor.attach(page)
    yield page


@pytest.fixture
def logged_in_admin_page(admin_page):
    login = AdminLoginPage(admin_page)
    login.open()
    login.login(settings.admin_username, settings.admin_password, settings.admin_pin)
    login.assert_dashboard_loaded()
    return admin_page


# --- wizard-stage fixtures ------------------------------------------------
# Each stage of the create-escrow wizard is only reachable by completing the
# ones before it, so these compose: a test starts at the stage it is about,
# without repeating the setup in every test body.

@pytest.fixture
def escrow_data() -> EscrowData:
    """Fresh randomised payload (new name/account/IFSC per test)."""
    return EscrowData()


@pytest.fixture
def flow(logged_in_admin_page) -> EscrowCreationFlow:
    return EscrowCreationFlow(logged_in_admin_page)


@pytest.fixture
def project_info_stage(flow):
    """A blank Project Information form (wizard stage 1)."""
    flow.open_new_escrow_form()
    return flow


@pytest.fixture
def pricing_stage(project_info_stage, escrow_data):
    """Service Pricing (stage 2), with stage 1 completed."""
    project_info_stage.complete_project_information(escrow_data)
    return project_info_stage


@pytest.fixture
def documents_stage(pricing_stage, escrow_data):
    """Documents (stage 3), with stages 1-2 completed."""
    pricing_stage.complete_service_pricing(escrow_data)
    return pricing_stage


@pytest.fixture
def review_stage(documents_stage, escrow_data):
    """Review (stage 4), with stages 1-3 completed. Nothing is submitted."""
    documents_stage.complete_documents(escrow_data)
    return documents_stage
