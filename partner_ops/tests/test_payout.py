"""PARTS 6, 7, 8 - the Make Payout wizard: modes, limits, OTP, negatives.

Read this before adding a test that spends money.

**Every payout attempt costs the wallet its charge, whether or not the money
moves.** That is not an assumption - it was measured: a ₹10 IMPS payout that the
UI reported as *"Your request has been failed."* still debited ₹14.75
(₹12.50 + GST ₹2.25) with no refund. So a naive "test every mode at min, normal
and max" sweep would drain the wallet and produce a pile of unrefunded charges.

The module is therefore split by cost:

* **Free** - structural and pre-authorisation checks (modes offered, limits,
  validation, absent controls, charge disclosure). These never reach
  `excrow-payout-request`, so they cost nothing. Most of the coverage lives here.
* **Cheap** - one OTP-rejection test. `Invalid OTP!` is refused before any
  money or charge moves, which makes it the one negative case that is genuinely
  free to run repeatedly.
* **Chargeable** - anything that submits a valid OTP. Marked `create` and
  fenced behind `PARTNER_OPS_SPEND=1`, with a `PARTNER_OPS_MAX_SPEND` ceiling
  checked against the live wallet before the attempt, so an unattended run
  cannot empty the wallet.

`test_failed_payout_charge_is_refunded` in `test_reconciliation.py` is where the
money-moving assertion lives; this module's job is to get the attempt made and
its identifiers recorded.
"""
from __future__ import annotations

import os
import re
from decimal import Decimal

import pytest

from config.settings import settings
from partner_ops.data.pricing import money
from partner_ops.pages.escrow_page import TAB_PAYOUT
from partner_ops.pages.payout_page import (
    ALL_MODES, MODE_IMPS, MODE_LIMITS, OTP_LENGTH,
    PAYMENT_TYPE_BANK_TRANSFER, PayoutPage)

SPEND_ALLOWED = os.getenv("PARTNER_OPS_SPEND", "").strip() in ("1", "true", "yes")
MAX_SPEND = money(os.getenv("PARTNER_OPS_MAX_SPEND", "50"))


@pytest.fixture
def payout(escrow_detail, ops_page) -> PayoutPage:
    escrow_detail.open_tab(TAB_PAYOUT)
    return PayoutPage(ops_page)


@pytest.fixture
def payable_beneficiary(payout) -> str:
    names = payout.payable_names()
    if not names:
        pytest.skip("This escrow has no beneficiary with a Payout action.")
    return names[0]


def _require_budget(needed: Decimal) -> None:
    """Refuse to spend beyond the configured ceiling, and log what it costs."""
    if not SPEND_ALLOWED:
        pytest.skip(
            "This test submits a real payout OTP, which debits the wallet its "
            "charge + GST even when the transfer fails (measured). Re-run with "
            "PARTNER_OPS_SPEND=1 to allow it.")
    if needed > MAX_SPEND:
        pytest.skip(f"Estimated cost ₹{needed} exceeds PARTNER_OPS_MAX_SPEND "
                    f"₹{MAX_SPEND}.")


# ======================= PART 6: structure (free) ========================

@pytest.mark.partner_ops
@pytest.mark.smoke
def test_payout_tab_lists_beneficiaries_with_gated_actions(payout):
    payable = payout.payable_names()
    blocked = payout.blocked_names()
    assert payable or blocked, "the Payout tab rendered no beneficiary rows"
    assert payout.bulk_payout_button.is_visible(), "Bulk Payout entry point missing"
    assert payout.payout_batches_button.is_visible(), "Payout Batches missing"
    overlap = set(payable) & set(blocked)
    assert not overlap, f"rows both payable and blocked: {overlap}"


@pytest.mark.partner_ops
@pytest.mark.regression
def test_payout_form_offers_every_documented_transfer_mode(payout,
                                                           payable_beneficiary):
    """PART 6 - test *every* available transfer mode, so enumerate them."""
    modal = payout.open_payout(payable_beneficiary)
    try:
        modes = modal.available_modes()
        assert modes, "the Transfer Mode select offered no options"
        for expected in ALL_MODES:
            assert expected in modes, f"{expected} missing from {modes}"
        types = modal.available_payment_types()
        assert PAYMENT_TYPE_BANK_TRANSFER in types, types
        assert modal.amount_input.count(), "no Amount control"
        assert modal.remarks_input.count(), "no Remarks control"
    finally:
        modal.close()


@pytest.mark.partner_ops
@pytest.mark.regression
def test_payout_details_step_never_states_the_charge(payout, payable_beneficiary,
                                                     ops_pricing):
    """DEF-OPS-03 - the partner authorises a debit the portal never quotes.

    Recorded as an assertion so the day a fee summary is added, this test fails
    and the defect can be closed on evidence.
    """
    modal = payout.open_payout(payable_beneficiary)
    try:
        modal.fill_details(mode=MODE_IMPS, amount="100",
                           remarks="charge disclosure probe")
        assert not modal.charge_disclosed(), (
            "A charge summary now appears on the Details step - DEF-OPS-03 may "
            "be fixed. Verify the figure against the Charges tab and update the "
            "defect log.")
        expected = ops_pricing.get(MODE_IMPS).gross_charge(Decimal("100")) \
            if ops_pricing.has(MODE_IMPS) else None
        assert expected is not None, "IMPS is unpriced on this escrow"
    finally:
        modal.close()


@pytest.mark.partner_ops
@pytest.mark.regression
def test_verify_step_shows_amount_mode_and_a_six_digit_challenge(
        payout, payable_beneficiary):
    """PART 6 - reaching the OTP step. Sends an OTP but moves no money."""
    modal = payout.open_payout(payable_beneficiary)
    try:
        modal.fill_details(mode=MODE_IMPS, amount="100", remarks="otp step probe")
        modal.request_otp()
        assert modal.on_verify_step(), \
            "Make Payout did not advance to the OTP step"
        assert modal.otp_boxes.count() == OTP_LENGTH, (
            f"payout OTP should be {OTP_LENGTH} digits, found "
            f"{modal.otp_boxes.count()} boxes (the *login* OTP is 4)")
        assert modal.otp_was_sent(), "send-payout-otp did not report success"

        text = modal.text()
        assert "₹100" in text.replace(",", ""), f"amount not restated: {text}"
        assert re.search(MODE_IMPS, text, re.I), f"mode not restated: {text}"
        assert re.search(r"resend code", text, re.I), "no resend control"
        assert not modal.charge_disclosed(), \
            "DEF-OPS-03: a charge is now disclosed at the Verify step"
    finally:
        modal.close()


@pytest.mark.partner_ops
@pytest.mark.negative
def test_payout_rejects_an_invalid_otp_without_charging(payout,
                                                       payable_beneficiary):
    """PART 8 - a wrong OTP must be refused. Free: nothing is debited."""
    modal = payout.open_payout(payable_beneficiary)
    try:
        modal.fill_details(mode=MODE_IMPS, amount="100", remarks="invalid otp probe")
        modal.request_otp()
        assert modal.on_verify_step()

        modal.enter_otp("000000")
        result = modal.confirm()

        assert not result.ok, f"an invalid OTP was accepted: {result}"
        assert re.search(r"invalid\s*otp", result.message, re.I), (
            f"expected an 'Invalid OTP' rejection, got {result.message!r}")
        # Documented API hygiene issue: a rejection arrives as HTTP 201.
        assert result.status in (200, 201, 400, 401, 422), result.status
        assert result.response_code == 3, (
            f"unexpected responsecode {result.response_code!r} for a rejection")
    finally:
        modal.close()


@pytest.mark.partner_ops
@pytest.mark.negative
@pytest.mark.parametrize("amount", ["0", "-5", "abc", ""])
def test_payout_refuses_non_positive_or_non_numeric_amounts(
        payout, payable_beneficiary, amount):
    """PART 8 - zero, negative and junk amounts must not reach an OTP."""
    modal = payout.open_payout(payable_beneficiary)
    try:
        modal.fill_details(mode=MODE_IMPS, amount=amount, remarks="amount probe")
        modal.request_otp()
        reached_otp = modal.on_verify_step()

        from partner_ops.pages.escrow_page import AlertDialog
        alert = AlertDialog(modal.page)
        message = alert.text()
        alert.dismiss()

        assert not reached_otp, (
            f"amount {amount!r} was allowed through to the OTP step "
            f"(alert: {message!r}). An OTP must not be spent on an amount the "
            "form could have rejected up front.")
    finally:
        modal.close()


@pytest.mark.partner_ops
@pytest.mark.negative
def test_below_minimum_amount_is_only_caught_after_the_otp(
        payout, payable_beneficiary):
    """DEF-OPS-04 - the per-mode floor is enforced too late.

    A ₹1 IMPS payout passes Details, consumes an OTP round, and only then is
    rejected with 'For imps, enter an amount between ₹10 and ₹500000'. The
    amount was knowable before the OTP was ever sent.

    Free to run: the request is rejected on the amount, so no charge is taken.
    """
    below = Decimal("1")
    floor, _ceiling = MODE_LIMITS[MODE_IMPS]
    assert below < floor

    modal = payout.open_payout(payable_beneficiary)
    try:
        modal.fill_details(mode=MODE_IMPS, amount=str(below),
                           remarks="minimum boundary probe")
        modal.request_otp()
        assert modal.on_verify_step(), (
            "Details rejected the sub-minimum amount up front - DEF-OPS-04 may "
            "be fixed; confirm and update the defect log.")

        modal.enter_otp(settings.partner_payout_otp)
        result = modal.confirm()

        assert not result.ok, f"a ₹{below} IMPS payout was accepted: {result}"
        hint = result.limit_hint()
        assert hint, (f"expected the per-mode limit message, got {result.message!r}")
        mode, low, high = hint
        assert mode == MODE_IMPS
        assert low == floor and high == _ceiling, (
            f"limits moved: the API now says ₹{low}-₹{high}, MODE_LIMITS has "
            f"₹{floor}-₹{_ceiling}. Update partner_ops/pages/payout_page.py.")
    finally:
        modal.close()


@pytest.mark.partner_ops
@pytest.mark.negative
def test_above_maximum_amount_is_refused(payout, payable_beneficiary):
    """PART 8 - an amount over the mode ceiling must be refused.

    Deliberately far above both the ceiling and the escrow balance, so a
    regression that accepted it could not actually move funds.
    """
    _floor, ceiling = MODE_LIMITS[MODE_IMPS]
    over = ceiling + Decimal("1")

    modal = payout.open_payout(payable_beneficiary)
    try:
        modal.fill_details(mode=MODE_IMPS, amount=str(over),
                           remarks="maximum boundary probe")
        modal.request_otp()
        if not modal.on_verify_step():
            return                     # rejected up front: the better behaviour
        modal.enter_otp(settings.partner_payout_otp)
        result = modal.confirm()
        assert not result.ok, f"₹{over} exceeded the IMPS ceiling but was accepted"
    finally:
        modal.close()


@pytest.mark.partner_ops
@pytest.mark.negative
def test_partial_otp_cannot_be_submitted(payout, payable_beneficiary):
    """PART 8 - three digits of a six-digit code must not authorise anything."""
    modal = payout.open_payout(payable_beneficiary)
    try:
        modal.fill_details(mode=MODE_IMPS, amount="100", remarks="partial otp probe")
        modal.request_otp()
        assert modal.on_verify_step()

        modal.enter_otp("123")
        before = len(modal.payout_attempts())
        try:
            if modal.verify_pay_button.is_enabled():
                modal.verify_pay_button.click()
                modal.page.wait_for_timeout(6_000)
        except Exception:
            pass
        attempts = modal.payout_attempts()[before:]
        accepted = [a for a in attempts if a.ok]
        assert not accepted, f"a partial OTP authorised a payout: {accepted}"
    finally:
        modal.close()


# ==================== PART 7: chargeable happy path ======================

@pytest.mark.partner_ops
@pytest.mark.create
@pytest.mark.parametrize("mode", [MODE_IMPS])
def test_payout_at_the_minimum_amount(payable_beneficiary, ops_pricing,
                                      dashboard, goto_escrow,
                                      artifact_store_module, mode):
    """PART 7 - a real payout at the mode's floor, with its cost recorded.

    Parametrised over a single mode on purpose: each additional mode is another
    unrefunded charge. Widen the list only for a deliberate, budgeted run.
    """
    floor = (MODE_LIMITS.get(mode) or (Decimal("10"), Decimal("100000")))[0]
    expected_charge = ops_pricing.get(mode).gross_charge(floor) \
        if ops_pricing.has(mode) else Decimal("0.00")
    _require_budget(expected_charge)

    opening = dashboard.wallet_balance()
    assert opening > expected_charge, (
        f"wallet ₹{opening} cannot cover the ₹{expected_charge} charge")

    # Reading the wallet navigated to the dashboard - go back deliberately.
    goto_escrow(TAB_PAYOUT)
    payout = PayoutPage(dashboard.page)
    modal = payout.open_payout(payable_beneficiary)
    modal.fill_details(mode=mode, amount=str(floor),
                       remarks=f"QA {mode} minimum-amount reconciliation")
    modal.request_otp()
    assert modal.on_verify_step()
    modal.enter_otp(settings.partner_payout_otp)
    result = modal.confirm()
    modal.close()

    # Record the attempt for the reconciliation module regardless of outcome:
    # a failure is the more interesting case and must not be lost.
    artifact_store_module.set("partner_ops_last_payout", {
        "mode": mode, "amount": str(floor), "beneficiary": payable_beneficiary,
        "ok": result.ok, "message": result.message,
        "expected_charge": str(expected_charge), "opening_wallet": str(opening),
    })

    assert result.status, "no payout response was captured"
    if not result.ok:
        pytest.fail(
            f"Payout of ₹{floor} via {mode} was refused: {result.message!r}.\n"
            f"  expected wallet charge ₹{expected_charge} "
            f"(from this escrow's own pricing)\n"
            f"  wallet before          ₹{opening}\n"
            "  Check Reports -> Wallet Statement: if the charge was still taken, "
            "that is DEF-OPS-02 (no refund on a failed payout).")
