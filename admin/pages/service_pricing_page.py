"""Wizard stage 2 - Service Pricing.

**Which sections appear depends on the Account Types picked on stage 1.**
Payout brings IMPS / NEFT / RTGS; VID Collection adds "Collection via VID" and
"VID Creation". So the page is driven off the radios actually rendered
(`rate_modes`) rather than a hard-coded list.

Each transfer-mode section is Fixed or Percentage, and that choice changes the
inputs underneath it:

    Fixed       `flat`     - a charge per slab (IMPS has three) or one flat charge
    Percentage  `percent`  - the rate, plus `min` / `max` charge floors and caps

"VID Creation" is Fixed-only (`vidCreationFixed`, control `flatCharge`).

Bank Charges and TSP Charges each take a frequency (Monthly / Quarterly /
Annually), an Amount, a Date Of Deduction and an Expiry Date. Changing the
frequency **clears both dates** ("Start date is required"), so frequency must be
set before the dates are picked. Quarterly additionally renders read-only Q1-Q4
tiles showing the computed deduction dates.

Controls are shared by name (`flat`, `percent`, `amount`, `Deductiondate`,
`date` …) across sections, so they are addressed positionally in DOM order.
"""
from __future__ import annotations

from core.base_page import BasePage

FIXED = "Fixed"
PERCENTAGE = "Percentage"
CHARGE_BLOCKS = ("bank", "tsp")
FREQUENCIES = ("Monthly", "Quarterly", "Annually")

# How far out to put the Expiry Date per frequency. The portal requires the
# expiry to outlast at least one charge cycle, so a two-month expiry that
# satisfies Monthly leaves Quarterly and Annually invalid - with nothing but the
# `maintenance_charges` group flagged to say so.
EXPIRY_MONTHS_AHEAD = {"Monthly": 2, "Quarterly": 6, "Annually": 14}


class ServicePricingPage(BasePage):
    # ---- what this escrow's pricing form contains ------------------------
    def rate_modes(self) -> list[str]:
        """Transfer-mode section keys, read off the rendered `<mode>Fixed` ids.

        e.g. ['imps', 'neft', 'rtgs'] for Payout alone, plus ['vid',
        'vidCreation'] once VID Collection is also selected.
        """
        ids = self.page.eval_on_selector_all(
            "input[type='radio'][id$='Fixed']", "els => els.map(e => e.id)"
        )
        return [i[: -len(FIXED)] for i in ids]

    def supports_percentage(self, mode: str) -> bool:
        return self.page.locator(f"#{mode}{PERCENTAGE}").count() > 0

    def section_headings(self) -> list[str]:
        return [h.strip() for h in
                self.page.locator(".card h6, .card h5").all_text_contents()
                if h.strip()]

    # ---- locators -------------------------------------------------------
    def charge_inputs(self, control: str):
        return self.page.locator(f"input[formcontrolname='{control}']")

    def flat_inputs(self):
        """Fixed-rate charge inputs (3 IMPS slabs + one per other section)."""
        return self.charge_inputs("flat")

    def percent_inputs(self):
        return self.charge_inputs("percent")

    def min_charge_inputs(self):
        return self.charge_inputs("min")

    def max_charge_inputs(self):
        return self.charge_inputs("max")

    def amount_inputs(self):
        """Bank Charges amount, then TSP Charges amount."""
        return self.charge_inputs("amount")

    def deduction_date_inputs(self):
        return self.charge_inputs("Deductiondate")

    def expiry_date_inputs(self):
        return self.charge_inputs("date")

    def rate_type_label(self, mode: str, kind: str = FIXED):
        """e.g. mode='imps', kind='Fixed' -> label[for='impsFixed']."""
        return self.page.locator(f"label[for='{mode}{kind}']")

    def frequency_label(self, block: str, frequency: str = "Monthly"):
        """e.g. block='bank', frequency='Monthly' -> label[for='bankMonthly']."""
        return self.page.locator(f"label[for='{block}{frequency}']")

    def charge_section(self, block: str):
        """The card wrapping a Bank/TSP charge block."""
        return self.page.locator(f"#{block}Monthly").locator(
            "xpath=ancestor::*[contains(@class,'card')][1]"
        )

    def quarter_tiles(self, block: str):
        """Read-only Q1-Q4 tiles Quarterly reveals (displays, not inputs)."""
        return self.charge_section(block).locator("div.col-3")

    def quarter_labels(self, block: str) -> list[str]:
        return [t.strip() for t in
                self.quarter_tiles(block).locator("small").all_text_contents()
                if t.strip()]

    # ---- actions --------------------------------------------------------
    def wait_ready(self) -> None:
        """Wait for the Service Pricing stage to render.

        Keyed off any rate-type radio, since which sections exist depends on
        the account types chosen.
        """
        self.wait_visible(self.page.locator("label[for$='Fixed']").first)

    def set_rate_type(self, mode: str, kind: str = FIXED) -> None:
        """Choose Fixed or Percentage for one section.

        A section with a single option renders it pre-selected *and* disabled
        (VID Creation is Fixed-only), so an already-correct or locked radio is
        left alone rather than clicked at.
        """
        radio = self.page.locator(f"#{mode}{kind}")
        assert radio.count(), f"no {kind} option for section {mode!r}"
        if radio.is_disabled() or radio.is_checked():
            return
        self.click(self.rate_type_label(mode, kind))

    def set_all_rate_types(self, kind: str = FIXED) -> dict:
        """Apply a rate type to every section that offers it.

        Fixed-only sections (VID Creation) stay Fixed, so asking for
        Percentage across the board does not fail on them.
        """
        applied = {}
        for mode in self.rate_modes():
            target = kind if (kind == FIXED or self.supports_percentage(mode)) else FIXED
            self.set_rate_type(mode, target)
            applied[mode] = target
        return applied

    def set_frequencies(self, frequency: str = "Monthly") -> None:
        for block in CHARGE_BLOCKS:
            self.click(self.frequency_label(block, frequency))

    def fill_control(self, control: str, value: str) -> int:
        """Fill every visible input with this formcontrolname; returns how many."""
        inputs = self.charge_inputs(control)
        filled = 0
        for i in range(inputs.count()):
            field = inputs.nth(i)
            if field.is_visible():
                self.fill(field, value)
                filled += 1
        return filled

    def set_all_prices(self, price: str = "1") -> int:
        """Fill every Fixed-rate charge input."""
        return self.fill_control("flat", price) + self.fill_control("flatCharge", price)

    def set_percentage_charges(self, percent: str = "1.5", min_charge: str = "5",
                               max_charge: str = "50") -> dict:
        """Fill the rate, floor and cap revealed by Percentage sections."""
        return {
            "percent": self.fill_control("percent", percent),
            "min": self.fill_control("min", min_charge),
            "max": self.fill_control("max", max_charge),
        }

    def set_charge_amounts(self, amount: str = "100") -> int:
        return self.fill_control("amount", amount)

    def set_dates(self, frequency: str = "Monthly") -> dict:
        """Pick a deduction date this month and an expiry that outlasts a cycle.

        The expiry must sit at least one full charge cycle after the deduction
        date, so it scales with the frequency - an expiry two months out is
        accepted for Monthly but leaves Quarterly and Annually invalid.

        Must run *after* the frequency is chosen: changing the frequency
        invalidates these dates (see `set_frequencies`).
        """
        expiry_months = EXPIRY_MONTHS_AHEAD[frequency]
        picked: dict = {"deduction": [], "expiry": []}
        deductions = self.deduction_date_inputs()
        for i in range(deductions.count()):
            picked["deduction"].append(
                self.pick_bs_date(deductions.nth(i), months_ahead=0, day_index=3)
            )
        expiries = self.expiry_date_inputs()
        for i in range(expiries.count()):
            picked["expiry"].append(
                self.pick_bs_date(expiries.nth(i), months_ahead=expiry_months,
                                  day_index=5)
            )
        return picked

    def fill_pricing(self, price: str = "1", amount: str = "100", *,
                     rate_type: str = FIXED, frequency: str = "Monthly",
                     percent: str = "1.5", min_charge: str = "5",
                     max_charge: str = "50") -> dict:
        """Complete the stage for whichever sections and rate type apply."""
        applied = self.set_all_rate_types(rate_type)

        # Fixed sections always exist (VID Creation is Fixed-only), so fill
        # both shapes and require that something was filled.
        fixed_filled = self.set_all_prices(price)
        percentage_filled = (
            self.set_percentage_charges(percent, min_charge, max_charge)
            if rate_type == PERCENTAGE else {}
        )
        assert fixed_filled or any(percentage_filled.values()), \
            "no charge inputs were filled on Service Pricing"

        # Frequency before dates: changing it invalidates them, and it decides
        # how far out the expiry has to be.
        self.set_frequencies(frequency)
        self.set_charge_amounts(amount)
        dates = self.set_dates(frequency)
        return {"rate_types": applied, "fixed_inputs": fixed_filled,
                "percentage_inputs": percentage_filled, "dates": dates}
