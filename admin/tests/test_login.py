"""Step 1 - login smoke test."""
import pytest

from admin.pages.login_page import AdminLoginPage
from config.settings import settings


@pytest.mark.admin
@pytest.mark.smoke
def test_admin_login_and_pin(admin_page, admin_monitor):
    login = AdminLoginPage(admin_page)
    login.open()
    login.login(settings.admin_username, settings.admin_password, settings.admin_pin)
    login.assert_dashboard_loaded()
    assert not admin_monitor.page_errors, admin_monitor.summary()
