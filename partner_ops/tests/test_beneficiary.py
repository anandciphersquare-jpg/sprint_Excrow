"""PARTS 4 and 5 - beneficiary creation, validation, verification.

The negative cases assert the portal's **exact** messages, captured live:

| Field | Input | Message |
| --- | --- | --- |
| mobile | `abcde12345`, `123` | Enter a valid 10-digit mobile number |
| ifsc | `BADIFSC` | Enter a valid 11-character IFSC |
| accountNumber | `12345` | Enter 9 to 18 digits |
| email | `not-an-email` | Enter a valid email |
| pincode | `11` | Enter a valid 6-digit pincode |
| displayName / name | empty | Required, up to 100 characters |
| bankName | typed, not picked | Pick a bank from the list |
| branchName | empty | Required |

Asserting the wording rather than merely "some error appeared" is the point: a
validator that fires the wrong message is a real defect, and a test that only
checks `ng-invalid` would pass straight through it.

**This module does not create a beneficiary by default.** Creating one leaves a
permanent record in UAT that later payout runs will see, and beneficiary records
cannot be cleaned up from the partner UI. The creation test is marked `create`
and skipped unless `PARTNER_OPS_CREATE_DATA=1`, matching how the admin suite
fences its record-creating tests.
"""
from __future__ import annotations

import os
import re

import pytest

from partner_ops.pages.beneficiary_page import (
    MANDATORY, BeneficiaryPage, STATUS_DEACTIVE)
from partner_ops.pages.escrow_page import TAB_BENEFICIARY

CREATE_ALLOWED = os.getenv("PARTNER_OPS_CREATE_DATA", "").strip() in ("1", "true", "yes")

# (control, value, expected message fragment)
INVALID_FIELD_CASES = [
    ("mobile", "abcde12345", "valid 10-digit mobile"),
    ("mobile", "123", "valid 10-digit mobile"),
    ("mobile", "12345678901", "valid 10-digit mobile"),
    ("ifsc", "BADIFSC", "valid 11-character IFSC"),
    ("ifsc", "1234567890X", "valid 11-character IFSC"),
    ("accountNumber", "12345", "9 to 18 digits"),
    ("accountNumber", "abcdefghij", "9 to 18 digits"),
    ("email", "not-an-email", "valid email"),
    # 'a@b' is deliberately absent: a bare hostname domain is valid per RFC 5322
    # and accepted by HTML5 `type=email`, so the portal accepting it is a policy
    # choice, not a defect. 'abc@' has no domain at all and is unambiguous.
    ("email", "abc@", "valid email"),
    ("pincode", "11", "valid 6-digit pincode"),
    ("pincode", "abcdef", "valid 6-digit pincode"),
]

VALID_BENEFICIARY = {
    "displayName": "QA Ops Bene",
    "name": "QA Ops Beneficiary",
    "mobile": "9876543210",
    "email": "anand.kumar+qabene@paysprint.in",
    "ifsc": "UBIN0438583",
    "branchName": "QA Branch",
    "accountNumber": "9439543999123",
    "pincode": "110091",
}


@pytest.fixture
def beneficiary(escrow_detail, ops_page) -> BeneficiaryPage:
    escrow_detail.open_tab(TAB_BENEFICIARY)
    return BeneficiaryPage(ops_page)


# ============================== PART 5: list =============================

@pytest.mark.partner_ops
@pytest.mark.smoke
def test_beneficiary_list_and_controls_render(beneficiary):
    headings = [h.strip().lower() for h in
                beneficiary.page.locator("table thead th").all_inner_texts()]
    for expected in ("bene name", "bank name", "account number", "ifsc", "status"):
        assert any(expected in h for h in headings), f"{expected} missing: {headings}"
    assert beneficiary.add_button.is_visible()
    assert beneficiary.bulk_button.is_visible(), "Bulk Beneficiary entry point missing"
    assert beneficiary.batches_button.is_visible(), "Beneficiary Batches missing"


@pytest.mark.partner_ops
@pytest.mark.regression
def test_beneficiary_records_are_well_formed(beneficiary):
    """Every listed beneficiary must carry a usable account/IFSC pair."""
    rows = beneficiary.rows()
    if not rows:
        pytest.skip("This escrow has no beneficiaries to inspect.")

    problems = []
    for row in rows:
        if not re.fullmatch(r"\d{9,18}", row.account.strip()):
            problems.append(f"{row.name!r}: account {row.account!r} is not 9-18 digits")
        if not re.fullmatch(r"[A-Za-z]{4}0[A-Za-z0-9]{6}", row.ifsc.strip()):
            problems.append(f"{row.name!r}: IFSC {row.ifsc!r} is malformed")
        if row.status.strip().lower() not in ("active", "deactive"):
            problems.append(f"{row.name!r}: unexpected status {row.status!r}")
    assert not problems, "malformed beneficiary records:\n  " + "\n  ".join(problems)


@pytest.mark.partner_ops
@pytest.mark.regression
def test_deactive_beneficiary_offers_no_payout_route(beneficiary, escrow_detail,
                                                     ops_page):
    """PART 5/8 - an unusable beneficiary must not be payable.

    The product enforces this by rendering no action link at all, so the
    assertion is about the absence of the control.
    """
    inactive = beneficiary.deactive_rows()
    if not inactive:
        pytest.skip("No Deactive beneficiary present to verify the gate.")

    from partner_ops.pages.escrow_page import TAB_PAYOUT
    from partner_ops.pages.payout_page import PayoutPage
    escrow_detail.open_tab(TAB_PAYOUT)
    payout = PayoutPage(ops_page)

    for row in inactive:
        assert not payout.has_payout_action(row.name), (
            f"Deactive beneficiary {row.name!r} exposes a Payout action")


# ========================= PART 4: add - negative ========================

@pytest.mark.partner_ops
@pytest.mark.negative
def test_add_beneficiary_modal_exposes_every_documented_field(beneficiary):
    modal = beneficiary.open_add_modal()
    try:
        missing = [c for c in MANDATORY if modal.field(c).count() == 0]
        assert not missing, f"mandatory controls absent from the form: {missing}"
        assert modal.field("mobile").get_attribute("maxlength") == "10"
        assert modal.field("ifsc").get_attribute("maxlength") == "11"
        assert modal.field("accountNumber").get_attribute("maxlength") == "18"
        assert modal.field("pincode").get_attribute("maxlength") == "6"
    finally:
        modal.cancel()


@pytest.mark.partner_ops
@pytest.mark.negative
@pytest.mark.parametrize("control,value,expected", INVALID_FIELD_CASES,
                         ids=[f"{c}-{v[:12]}" for c, v, _ in INVALID_FIELD_CASES])
def test_invalid_beneficiary_field_is_rejected_with_its_own_message(
        beneficiary, control, value, expected):
    modal = beneficiary.open_add_modal()
    try:
        modal.fill_field(control, value)
        modal.field(control).blur()
        modal.page.wait_for_timeout(700)

        message = modal.message_for(control)
        assert expected.lower() in message.lower(), (
            f"{control}={value!r}: expected a message containing {expected!r}, "
            f"got {message!r}")
        assert control in modal.invalid_controls(), \
            f"{control}={value!r} was not marked ng-invalid"
    finally:
        modal.cancel()


@pytest.mark.partner_ops
@pytest.mark.negative
def test_empty_mandatory_submit_is_guarded_and_silent(beneficiary):
    """Submitting an empty form must validate locally, not call the API.

    The button stays enabled by design - this is a guarded submit, so the
    assertion is 'no request fired + every message shown', not 'button disabled'.
    """
    calls = beneficiary.capture_beneficiary_calls()
    modal = beneficiary.open_add_modal()
    try:
        modal.submit()
        assert modal.visible(timeout=3_000), \
            "the modal closed on an empty submit - did it create a record?"
        creates = [c for c in calls if "create" in c.lower()]
        assert not creates, \
            f"an invalid form reached the API: {creates}"

        text = modal.panel_text().lower()
        for expected in ("required, up to 100 characters", "pick a bank from the list",
                         "valid 10-digit mobile", "9 to 18 digits",
                         "valid 6-digit pincode", "valid email"):
            assert expected in text, f"missing validation message: {expected!r}"
    finally:
        modal.cancel()


@pytest.mark.partner_ops
@pytest.mark.negative
def test_typed_bank_name_without_picking_is_rejected(beneficiary):
    """The bank type-ahead must require a real selection."""
    modal = beneficiary.open_add_modal()
    try:
        modal.fill_field("bankName", "NOT A REAL BANK XYZ")
        modal.field("bankName").blur()
        modal.page.wait_for_timeout(1_200)
        modal.submit()
        assert modal.visible(timeout=3_000)
        assert "pick a bank from the list" in modal.panel_text().lower(), \
            "a free-typed bank name was not rejected"
    finally:
        modal.cancel()


@pytest.mark.partner_ops
@pytest.mark.negative
def test_leading_and_trailing_spaces_do_not_defeat_validation(beneficiary):
    """' 123 ' must not pass as a mobile number."""
    modal = beneficiary.open_add_modal()
    try:
        modal.fill_field("mobile", "   123   ")
        modal.field("mobile").blur()
        modal.page.wait_for_timeout(700)
        assert "mobile" in modal.invalid_controls(), \
            "a padded short mobile number was accepted as valid"
    finally:
        modal.cancel()


@pytest.mark.partner_ops
@pytest.mark.negative
@pytest.mark.security
def test_script_payload_in_a_name_field_is_not_executed(beneficiary):
    """Basic XSS hygiene (PART 26) - the payload must stay inert text."""
    payload = "<script>alert('xss')</script>"
    modal = beneficiary.open_add_modal()
    try:
        modal.fill_field("displayName", payload)
        modal.page.wait_for_timeout(800)
        assert modal.field("displayName").input_value() == payload, \
            "the field mangled the input, so this asserts nothing about escaping"
        # Nothing may have been injected into the DOM as markup.
        injected = modal.page.locator("script:has-text(\"alert('xss')\")").count()
        assert injected == 0, "the payload was materialised as a live <script>"
        assert modal.page.locator(".cdk-overlay-pane").count() >= 1, \
            "the dialog disappeared - possible DOM corruption from the payload"
    finally:
        modal.cancel()


# ========================= PART 4: add - positive ========================

@pytest.mark.partner_ops
@pytest.mark.create
@pytest.mark.skipif(not CREATE_ALLOWED,
                    reason="Creates a permanent beneficiary in UAT that cannot "
                           "be removed from the partner UI. Re-run with "
                           "PARTNER_OPS_CREATE_DATA=1 to include it.")
def test_add_a_valid_beneficiary(beneficiary):
    """The full positive path, including the bank type-ahead."""
    before = len(beneficiary.rows())
    modal = beneficiary.open_add_modal()
    chosen = modal.pick_bank("UNION BANK")
    if not chosen:
        modal.cancel()
        pytest.skip("The bank type-ahead returned no options for 'UNION BANK'.")

    payload = dict(VALID_BENEFICIARY)
    payload["displayName"] = f"QA Ops {os.getpid()}"
    modal.fill_form({k: v for k, v in payload.items() if k != "bankName"})
    modal.page.wait_for_timeout(1_500)

    assert not modal.invalid_controls(), (
        "a form filled with valid data still reports invalid controls: "
        f"{modal.invalid_controls()}")
    modal.submit()
    modal.page.wait_for_timeout(4_000)

    from partner_ops.pages.escrow_page import AlertDialog
    alert = AlertDialog(beneficiary.page).dismiss()
    beneficiary.page.wait_for_timeout(3_000)

    after = beneficiary.rows()
    created = beneficiary.find(payload["displayName"]) or \
        beneficiary.find(payload["accountNumber"])
    assert created or len(after) > before, (
        f"no new beneficiary appeared. Alert said: {alert!r}")
    if created:
        assert created.account == payload["accountNumber"], \
            f"stored account {created.account!r} != submitted {payload['accountNumber']!r}"


@pytest.mark.partner_ops
@pytest.mark.negative
def test_duplicate_account_number_is_refused(beneficiary):
    """Re-adding an existing account must be blocked, not silently duplicated."""
    existing = beneficiary.active_rows()
    if not existing:
        pytest.skip("No existing beneficiary to duplicate.")
    target = existing[0]

    modal = beneficiary.open_add_modal()
    try:
        modal.fill_form({**VALID_BENEFICIARY,
                         "accountNumber": target.account,
                         "ifsc": target.ifsc,
                         "displayName": "QA Duplicate Probe"})
        chosen = modal.pick_bank(target.bank.split()[0])
        modal.page.wait_for_timeout(1_200)
        if not chosen:
            pytest.skip(f"Could not re-select bank {target.bank!r} in the type-ahead.")

        modal.submit()
        modal.page.wait_for_timeout(4_000)

        from partner_ops.pages.escrow_page import AlertDialog
        alert = AlertDialog(beneficiary.page)
        message = alert.text()
        still_open = modal.visible(timeout=2_000)
        alert.dismiss()

        assert (message and re.search(r"duplicate|already|exists", message, re.I)) \
            or still_open, (
            "a duplicate account number was accepted with no warning "
            f"(alert: {message!r})")
    finally:
        modal.cancel()


# ======================= PART 5: verification ============================

@pytest.mark.partner_ops
@pytest.mark.regression
def test_beneficiary_verification_route_exists_for_unverified_records(beneficiary):
    """A Deactive beneficiary should offer 'Verify Beneficiary'."""
    unverified = [r for r in beneficiary.rows()
                  if r.status.strip().lower() == STATUS_DEACTIVE.lower()]
    if not unverified:
        pytest.skip("No unverified beneficiary present.")
    offering = [r for r in unverified if re.search(r"verify", r.action, re.I)]
    assert offering, (
        "Deactive beneficiaries exist but none offers a verification action: "
        f"{[(r.name, r.action) for r in unverified]}")
