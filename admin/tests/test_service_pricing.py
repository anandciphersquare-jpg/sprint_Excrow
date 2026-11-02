"""Wizard stage 2 (Service Pricing) - positive and negative coverage.

Every control on this stage is required before `Next` enables. Which sections
exist depends on the Account Types chosen on stage 1 (the default payload picks
Payout + VID Collection, so IMPS / NEFT / RTGS / Collection via VID / VID
Creation all appear), and each transfer mode can be charged either Fixed (flat
per-slab amounts) or Percentage (a rate plus min and max charge bounds).
"""
import pytest

from admin.pages.service_pricing_page import (CHARGE_BLOCKS, FIXED, FREQUENCIES,
                                              PERCENTAGE, ServicePricingPage)

# With Payout + VID Collection selected.
EXPECTED_SECTIONS = ["IMPS", "NEFT", "RTGS", "Collection via VID", "VID Creation",
                     "Bank Charges", "TSP Charges"]
EXPECTED_MODES = ["imps", "neft", "rtgs", "vid", "vidCreation"]
FIXED_ONLY_MODES = ["vidCreation"]
EXPECTED_CHARGE_BLOCKS = 2      # Bank Charges + TSP Charges
# Fixed-rate charge inputs: 3 IMPS slabs + NEFT + RTGS + Collection via VID.
EXPECTED_FLAT_INPUTS = 6
# VID Creation uses its own control name and is Fixed-only.
EXPECTED_FLAT_CHARGE_INPUTS = 1


@pytest.fixture
def pricing(pricing_stage) -> ServicePricingPage:
    return pricing_stage.pricing


# =========================== positive ====================================

@pytest.mark.admin
@pytest.mark.regression
def test_sections_follow_the_chosen_account_types(pricing):
    """Payout brings IMPS/NEFT/RTGS; VID Collection adds the two VID sections."""
    assert pricing.rate_modes() == EXPECTED_MODES
    for heading in EXPECTED_SECTIONS:
        assert heading in pricing.section_headings(), \
            f"{heading!r} missing from {pricing.section_headings()}"


@pytest.mark.admin
@pytest.mark.regression
def test_only_vid_creation_is_fixed_only(pricing):
    for mode in pricing.rate_modes():
        expected = mode not in FIXED_ONLY_MODES
        assert pricing.supports_percentage(mode) is expected, \
            f"{mode} percentage support should be {expected}"


@pytest.mark.admin
@pytest.mark.regression
def test_fixed_mode_shows_flat_charges(pricing, escrow_data):
    """Fixed keeps the per-slab flat inputs: 3 IMPS slabs + one per section."""
    pricing.set_all_rate_types(FIXED)
    assert pricing.percent_inputs().count() == 0
    assert pricing.flat_inputs().count() == EXPECTED_FLAT_INPUTS
    assert pricing.charge_inputs("flatCharge").count() == EXPECTED_FLAT_CHARGE_INPUTS
    assert pricing.set_all_prices(escrow_data.price) == \
        EXPECTED_FLAT_INPUTS + EXPECTED_FLAT_CHARGE_INPUTS


@pytest.mark.admin
@pytest.mark.regression
def test_percentage_mode_reveals_rate_with_min_and_max(pricing):
    """Percentage swaps flat charges for a rate plus min/max bounds."""
    applied = pricing.set_all_rate_types(PERCENTAGE)
    assert applied["imps"] == PERCENTAGE
    assert applied["vidCreation"] == FIXED, "VID Creation offers no Percentage"

    percentage_modes = [m for m in pricing.rate_modes()
                        if pricing.supports_percentage(m)]
    expected = len(percentage_modes)
    assert pricing.percent_inputs().count() == expected
    assert pricing.min_charge_inputs().count() == expected
    assert pricing.max_charge_inputs().count() == expected
    # Every percentage-capable section swaps its flat charges out, so the only
    # fixed input left is VID Creation's (which uses `flatCharge`).
    assert pricing.flat_inputs().count() == 0
    assert pricing.charge_inputs("flatCharge").count() == EXPECTED_FLAT_CHARGE_INPUTS

    body = pricing.body_text()
    for label in ("Percent", "Min. Charges", "Max. Charges"):
        assert label in body


@pytest.mark.admin
@pytest.mark.regression
def test_percentage_charges_are_accepted(pricing, escrow_data):
    pricing.set_all_rate_types(PERCENTAGE)
    filled = pricing.set_percentage_charges(escrow_data.percent,
                                            escrow_data.min_charge,
                                            escrow_data.max_charge)
    assert all(filled.values()), f"a percentage field went unfilled: {filled}"
    assert pricing.percent_inputs().first.input_value() == escrow_data.percent
    assert pricing.min_charge_inputs().first.input_value() == escrow_data.min_charge
    assert pricing.max_charge_inputs().first.input_value() == escrow_data.max_charge


@pytest.mark.admin
@pytest.mark.regression
def test_switching_rate_type_swaps_the_inputs_both_ways(pricing):
    pricing.set_all_rate_types(PERCENTAGE)
    assert pricing.percent_inputs().count() > 0
    pricing.set_all_rate_types(FIXED)
    assert pricing.percent_inputs().count() == 0
    assert pricing.flat_inputs().count() == EXPECTED_FLAT_INPUTS


@pytest.mark.admin
@pytest.mark.regression
@pytest.mark.parametrize("rate_type", [FIXED, PERCENTAGE])
def test_completing_pricing_enables_next(pricing, escrow_data, rate_type):
    """Both charging models must be able to complete the stage."""
    assert pricing.next_button.is_disabled(), "Next should start disabled"
    filled = pricing.fill_pricing(escrow_data.price, escrow_data.charge_amount,
                                  rate_type=rate_type,
                                  frequency=escrow_data.charge_frequency,
                                  percent=escrow_data.percent,
                                  min_charge=escrow_data.min_charge,
                                  max_charge=escrow_data.max_charge)
    assert all(filled["dates"]["deduction"]) and all(filled["dates"]["expiry"]), \
        f"a date did not register: {filled['dates']}"
    assert pricing.next_is_enabled(), \
        f"Next still disabled; invalid: {pricing.invalid_controls()}"


@pytest.mark.admin
@pytest.mark.regression
@pytest.mark.parametrize("frequency", FREQUENCIES)
def test_each_charge_frequency_completes_the_stage(pricing, escrow_data, frequency):
    """Monthly, Quarterly and Annually must each be selectable and completable."""
    filled = pricing.fill_pricing(escrow_data.price, escrow_data.charge_amount,
                                  rate_type=escrow_data.rate_type,
                                  frequency=frequency,
                                  percent=escrow_data.percent,
                                  min_charge=escrow_data.min_charge,
                                  max_charge=escrow_data.max_charge)
    for block in CHARGE_BLOCKS:
        assert pricing.page.locator(f"#{block}{frequency}").is_checked()
    assert all(filled["dates"]["deduction"])
    assert pricing.next_is_enabled(), \
        f"Next disabled for {frequency}; invalid: {pricing.invalid_controls()}"


@pytest.mark.admin
@pytest.mark.regression
def test_quarterly_shows_four_computed_quarters(pricing, escrow_data):
    """Quarterly adds read-only Q1-Q4 tiles filled in from the deduction date."""
    pricing.fill_pricing(escrow_data.price, escrow_data.charge_amount,
                         rate_type=escrow_data.rate_type, frequency="Quarterly",
                         percent=escrow_data.percent,
                         min_charge=escrow_data.min_charge,
                         max_charge=escrow_data.max_charge)
    labels = pricing.quarter_labels("bank")
    for quarter in ("Q 1", "Q 2", "Q 3", "Q 4"):
        assert quarter in labels, f"{quarter} tile missing from {labels}"
    # Each tile carries a computed date once a deduction date is chosen.
    dated = [t for t in labels if t not in ("Q 1", "Q 2", "Q 3", "Q 4")]
    assert len(dated) >= 4, f"quarter tiles were not populated: {labels}"


@pytest.mark.admin
@pytest.mark.regression
def test_previous_returns_to_project_information_with_data_intact(pricing_stage,
                                                                 escrow_data):
    """Going back must not lose what stage 1 already accepted."""
    page = pricing_stage.page
    page.get_by_role("button", name="Previous").first.click()
    page.wait_for_timeout(3_000)
    details = pricing_stage.details
    assert details.escrow_name_input.input_value() == escrow_data.name
    assert details.account_number_input.input_value() == escrow_data.account_number
    assert details.generated_escrow_id() == pricing_stage.escrow_id
    assert details.selected_account_types() == escrow_data.account_types


# =========================== negative ====================================

@pytest.mark.admin
@pytest.mark.negative
def test_pricing_starts_incomplete(pricing):
    assert pricing.next_button.is_disabled()
    assert pricing.next_button.get_attribute("title") == \
        "Please complete all required fields"


@pytest.mark.admin
@pytest.mark.negative
def test_amounts_without_dates_still_block_next(pricing, escrow_data):
    """Charges alone are not enough - the deduction/expiry dates are required."""
    pricing.set_all_rate_types(FIXED)
    pricing.set_frequencies("Monthly")
    pricing.set_all_prices(escrow_data.price)
    pricing.set_charge_amounts(escrow_data.charge_amount)
    assert pricing.next_button.is_disabled()
    assert "Deductiondate" in pricing.invalid_controls()


@pytest.mark.admin
@pytest.mark.negative
def test_percentage_without_min_and_max_blocks_next(pricing, escrow_data):
    """A rate on its own is incomplete: the bounds are required too."""
    pricing.set_all_rate_types(PERCENTAGE)
    pricing.fill_control("percent", escrow_data.percent)
    pricing.set_frequencies("Monthly")
    pricing.set_charge_amounts(escrow_data.charge_amount)
    pricing.set_dates()
    assert pricing.next_button.is_disabled()
    invalid = pricing.invalid_controls()
    assert "min" in invalid and "max" in invalid, \
        f"expected min/max to be invalid, got {invalid}"


@pytest.mark.admin
@pytest.mark.negative
def test_changing_frequency_invalidates_the_dates(pricing, escrow_data):
    """Switching frequency re-invalidates the dates while their text remains.

    A nasty trap: the boxes still show dates and no field is flagged, yet the
    stage will not complete - only the `maintenance_charges` group is marked
    invalid. The dates have to be picked again for the new frequency.
    """
    pricing.set_all_rate_types(FIXED)
    pricing.set_frequencies("Monthly")
    pricing.set_all_prices(escrow_data.price)
    pricing.set_charge_amounts(escrow_data.charge_amount)
    pricing.set_dates("Monthly")
    assert pricing.next_is_enabled()
    was_showing = pricing.deduction_date_inputs().first.input_value()

    pricing.set_frequencies("Quarterly")
    pricing.page.wait_for_timeout(1_500)

    assert pricing.deduction_date_inputs().first.input_value() == was_showing, \
        "the date text stays put, which is what makes this easy to miss"
    assert pricing.next_button.is_disabled()
    assert "group:maintenance_charges" in pricing.invalid_controls(), \
        "expected the charge group to be flagged, since no single input is"

    # Re-picking for the new frequency clears it.
    pricing.set_dates("Quarterly")
    assert pricing.next_is_enabled(), \
        f"still invalid after re-picking: {pricing.invalid_controls()}"


@pytest.mark.admin
@pytest.mark.negative
def test_expiry_must_outlast_one_charge_cycle(pricing, escrow_data):
    """An expiry inside the first cycle is rejected for a quarterly charge."""
    pricing.set_all_rate_types(FIXED)
    pricing.set_all_prices(escrow_data.price)
    pricing.set_frequencies("Quarterly")
    pricing.set_charge_amounts(escrow_data.charge_amount)
    for i in range(pricing.deduction_date_inputs().count()):
        pricing.pick_bs_date(pricing.deduction_date_inputs().nth(i), day_index=3)
    # Two months out is less than one quarter.
    for i in range(pricing.expiry_date_inputs().count()):
        pricing.pick_bs_date(pricing.expiry_date_inputs().nth(i),
                             months_ahead=2, day_index=5)
    assert pricing.next_button.is_disabled(), \
        "an expiry inside the first quarter should not satisfy the stage"
    assert "group:maintenance_charges" in pricing.invalid_controls()

    # Pushing it past a full quarter fixes it, with nothing else changed.
    for i in range(pricing.expiry_date_inputs().count()):
        pricing.pick_bs_date(pricing.expiry_date_inputs().nth(i),
                             months_ahead=6, day_index=5)
    assert pricing.next_is_enabled(), \
        f"still invalid: {pricing.invalid_controls()}"


@pytest.mark.admin
@pytest.mark.negative
def test_typed_date_does_not_satisfy_the_datepicker(pricing, escrow_data):
    """A typed date shows in the box but leaves the control invalid.

    Worth pinning down: it is the trap that makes this stage look filled in
    while 'Next' stays stubbornly disabled.
    """
    pricing.set_all_rate_types(FIXED)
    pricing.set_frequencies("Monthly")
    pricing.set_all_prices(escrow_data.price)
    pricing.set_charge_amounts(escrow_data.charge_amount)
    field = pricing.deduction_date_inputs().first
    field.fill("15-09-2026")
    field.press("Tab")
    assert field.input_value() == "15-09-2026", "the text is accepted..."
    assert "Deductiondate" in pricing.invalid_controls(), \
        "...but the control should remain invalid until the calendar is used"


@pytest.mark.admin
@pytest.mark.negative
def test_clearing_a_charge_reblocks_next(pricing, escrow_data):
    pricing.fill_pricing(escrow_data.price, escrow_data.charge_amount,
                         rate_type=FIXED, frequency="Monthly")
    assert pricing.next_is_enabled()
    pricing.flat_inputs().first.fill("")
    pricing.flat_inputs().first.press("Tab")
    assert pricing.next_button.is_disabled()
    assert "flat" in pricing.invalid_controls()
