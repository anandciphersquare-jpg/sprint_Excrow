"""Wizard stage 1 (Project Information) - positive and negative coverage.

Calibrated against the live UAT DOM. Two behaviours drive most of the
assertions here:

* the wizard reports incompleteness by keeping **Next disabled** (title
  "Please complete all required fields") rather than by showing a form-level
  error, and
* per-field problems *do* render inline messages, with wording verified below.
"""
import re

import pytest

from admin.data.escrow_data import (INVALID_ACCOUNT_NUMBER, INVALID_IFSC,
                                    SQLI_PAYLOAD, XSS_PAYLOAD, EscrowData)
from admin.pages.company_rep_page import CompanyRepPage
from admin.pages.escrow_details_page import EscrowDetailsPage
from admin.pages.trustee_page import TrusteePage

ESCROW_ID_RE = re.compile(r"^EXC-\d{4}-\d{4,}$", re.I)

# Options the UAT portal offers today. Kept explicit so a silent catalogue
# change fails the suite instead of passing unnoticed.
EXPECTED_INDUSTRIES = ["Real Estate", "E-commerce", "Marketplace", "Lending",
                       "Capital Markets", "Payroll"]
EXPECTED_BANKS = ["IDFC", "NSDL"]
EXPECTED_ACCOUNT_TYPES = ["Payout", "UPI Collection", "VID Collection"]

# Every control the portal requires before stage 1 can be left. mobileNumber /
# fname / lname / companyName are satisfied by the representative lookup, not
# by typing, and they sit in a `representatives` form group that is reported
# alongside them. `industry` is deliberately absent - the portal does not
# enforce it.
MANDATORY_CONTROLS = {"excrowName", "bank_id", "account_type", "accountNumber",
                      "ifscCode", "email", "mobileNumber", "fname", "lname",
                      "companyName", "group:representatives"}


@pytest.fixture
def details(project_info_stage) -> EscrowDetailsPage:
    return project_info_stage.details


# =========================== positive ====================================

@pytest.mark.admin
@pytest.mark.smoke
def test_escrow_id_is_generated_and_readonly(details):
    """The portal issues the Escrow ID itself, before anything is submitted."""
    escrow_id = details.generated_escrow_id()
    assert ESCROW_ID_RE.match(escrow_id), f"unexpected Escrow ID format: {escrow_id!r}"
    assert details.escrow_id_input.get_attribute("readonly") is not None, \
        "Escrow ID must not be editable"


@pytest.mark.admin
@pytest.mark.regression
def test_industry_options_match_expected(details):
    options = [o for o in details.industry_options() if o != "Select Industry"]
    assert options == EXPECTED_INDUSTRIES


@pytest.mark.admin
@pytest.mark.regression
def test_bank_options_match_expected(details):
    options = [o for o in details.bank_options() if o != "Select Bank"]
    assert options == EXPECTED_BANKS


@pytest.mark.admin
@pytest.mark.regression
def test_account_types_load_for_selected_bank(details):
    """Account Type is populated from the bank, so it must follow the bank."""
    details.choose_bank("IDFC")
    details.page.wait_for_timeout(2_500)
    assert details.available_account_types() == EXPECTED_ACCOUNT_TYPES


@pytest.mark.admin
@pytest.mark.regression
def test_account_types_can_be_combined(details):
    """Account Type is a multiselect - a combination must stick.

    The combination matters downstream: it decides which Service Pricing
    sections appear.
    """
    details.choose_bank("IDFC")
    details.page.wait_for_timeout(2_500)
    details.select_account_types("Payout", "VID Collection")
    assert details.selected_account_types() == ["Payout", "VID Collection"]
    assert "account_type" not in details.invalid_controls()


@pytest.mark.admin
@pytest.mark.regression
@pytest.mark.parametrize("account_type", EXPECTED_ACCOUNT_TYPES)
def test_each_account_type_can_be_selected_alone(details, account_type):
    details.choose_bank("IDFC")
    details.page.wait_for_timeout(2_500)
    details.select_account_types(account_type)
    assert details.selected_account_types() == [account_type]


@pytest.mark.admin
@pytest.mark.regression
def test_ifsc_is_upper_cased(details):
    """Lowercase input is accepted - the field upper-cases it for you."""
    details.fill(details.ifsc_input, "idfb0021234")
    details.ifsc_input.press("Tab")
    assert details.ifsc_input.input_value() == "IDFB0021234"
    assert "valid IFSC" not in details.form_group_text(details.ifsc_input)
    assert "ifscCode" not in details.invalid_controls()


@pytest.mark.admin
@pytest.mark.regression
def test_ifsc_is_capped_at_11_characters(details):
    details.fill(details.ifsc_input, "IDFB00212345678")
    assert details.ifsc_input.input_value() == "IDFB0021234"


@pytest.mark.admin
@pytest.mark.regression
def test_complete_stage_one_enables_next(project_info_stage, escrow_data):
    """The whole point of the stage: filled correctly, it lets you continue."""
    details = project_info_stage.details
    assert details.next_button.is_disabled(), "Next should start disabled"
    project_info_stage.fill_project_information(escrow_data)
    assert details.next_is_enabled(), \
        f"Next still disabled; invalid: {details.invalid_controls()}"


# =========================== negative ====================================

@pytest.mark.admin
@pytest.mark.negative
def test_empty_form_blocks_progress(details):
    """No error banner - the wizard simply refuses to enable Next."""
    assert details.next_button.is_disabled()
    assert details.next_button.get_attribute("title") == \
        "Please complete all required fields"
    assert set(details.invalid_controls()) == MANDATORY_CONTROLS


@pytest.mark.admin
@pytest.mark.negative
def test_invalid_ifsc_rejected(details):
    details.fill(details.ifsc_input, INVALID_IFSC)
    details.ifsc_input.press("Tab")
    assert "ifscCode" in details.invalid_controls()
    assert "Please enter a valid IFSC code" in details.form_group_text(details.ifsc_input)
    assert details.next_button.is_disabled()


@pytest.mark.admin
@pytest.mark.negative
@pytest.mark.parametrize("value, message", [
    (INVALID_ACCOUNT_NUMBER, "Must contain only digits"),
    ("12", "Must be 9-18 digits"),
    ("1" * 30, "Must be 9-18 digits"),
])
def test_invalid_account_number_rejected(details, value, message):
    details.fill(details.account_number_input, value)
    details.account_number_input.press("Tab")
    assert "accountNumber" in details.invalid_controls()
    group = details.form_group_text(details.account_number_input)
    assert message in group, f"expected {message!r} in {group!r}"


@pytest.mark.admin
@pytest.mark.negative
def test_account_type_empty_until_bank_chosen(details):
    """Choosing an account type first is a dead end, and says so."""
    details.click(details.account_type_trigger)
    options = details.account_type_options
    options.first.wait_for(state="visible", timeout=10_000)
    assert [t.strip() for t in options.all_text_contents()] == ["No data available"]


@pytest.mark.admin
@pytest.mark.negative
def test_malformed_email_rejected(project_info_stage):
    rep = CompanyRepPage(project_info_stage.page)
    rep.fill(rep.email_input, "not-an-email")
    rep.email_input.press("Tab")
    assert "Please enter a valid email" in rep.form_group_text(rep.email_input)
    assert rep.first_name_input.input_value() == "", \
        "a malformed address must not trigger a partner lookup"


@pytest.mark.admin
@pytest.mark.negative
def test_unknown_email_reports_user_not_found(project_info_stage):
    """A well-formed but unregistered address gets a blocking alert."""
    page = project_info_stage.page
    rep = CompanyRepPage(page)
    rep.fill(rep.email_input, "nobody.unregistered@nowhere-sprintnxt.invalid")
    rep.email_input.press("Tab")
    alert = page.locator(".swal2-popup")
    alert.wait_for(state="visible", timeout=30_000)
    assert re.search(r"user not found", alert.inner_text(), re.I), \
        f"unexpected alert: {alert.inner_text()!r}"
    assert rep.autofilled_values() == {k: "" for k in rep.autofilled_values()}
    page.get_by_role("button", name=re.compile(r"ok(ay)?", re.I)).first.click()


@pytest.mark.admin
@pytest.mark.negative
@pytest.mark.security
@pytest.mark.parametrize("payload", [XSS_PAYLOAD, SQLI_PAYLOAD])
def test_name_field_does_not_execute_payloads(details, admin_monitor, payload):
    """Script/SQL payloads must be inert text, never executed.

    The field stores them verbatim (no client-side sanitising), which is fine
    on its own - what must not happen is execution or a JS error.
    """
    details.fill(details.escrow_name_input, payload)
    details.escrow_name_input.press("Tab")
    assert details.escrow_name_input.input_value() == payload
    assert details.page.locator("script:has-text('alert')").count() == 0
    assert not admin_monitor.page_errors, admin_monitor.summary()


@pytest.mark.admin
@pytest.mark.negative
def test_trustee_options_are_mutually_exclusive(project_info_stage):
    trustee = TrusteePage(project_info_stage.page)
    for key in ("view_only", "transaction_approval", "not_required"):
        trustee.set_option(key)
        assert trustee.selected_option() == key, \
            f"selecting {key} did not clear the other options"


@pytest.mark.admin
@pytest.mark.negative
def test_missing_single_field_still_blocks_next(project_info_stage):
    """Everything valid except the escrow name - Next must stay disabled."""
    details = project_info_stage.details
    data = EscrowData()
    project_info_stage.fill_project_information(data)
    assert details.next_is_enabled()

    details.escrow_name_input.fill("")
    details.escrow_name_input.press("Tab")
    assert "excrowName" in details.invalid_controls()
    assert details.next_button.is_disabled()
