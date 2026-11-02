"""Wizard stage 1 (Project Information) - the escrow's own details.

Calibrated against the live UAT DOM. Notes that cost time to discover:

* `excrow_id` is generated server-side and rendered **readonly** here, so the
  Escrow ID is known before submit - no need to scrape a success screen.
* Industry and Bank are plain `<select>` elements (not custom dropdowns).
* Account Type is an `ng-multiselect-dropdown` whose options are **loaded from
  the selected bank** - it reads "No data available" until Bank is set, so
  Bank must always be selected first.
* The portal spells the module "Excrow", hence the `excrowName` control name.
"""
from __future__ import annotations

import re
from typing import List

from playwright.sync_api import expect

from core.base_page import BasePage

ACCOUNT_TYPE = "ng-multiselect-dropdown[formcontrolname='account_type']"


class EscrowDetailsPage(BasePage):
    # ---- locators -------------------------------------------------------
    @property
    def escrow_id_input(self):
        return self.page.locator("input[formcontrolname='excrow_id']").first

    @property
    def escrow_name_input(self):
        return self.page.locator("input[formcontrolname='excrowName']").first

    @property
    def industry_select(self):
        return self.page.locator("select[formcontrolname='industry']").first

    @property
    def bank_select(self):
        return self.page.locator("select[formcontrolname='bank_id']").first

    @property
    def account_type_trigger(self):
        return self.page.locator(f"{ACCOUNT_TYPE} .dropdown-btn").first

    @property
    def account_type_options(self):
        return self.page.locator(f"{ACCOUNT_TYPE} ul.item2 li")

    @property
    def account_number_input(self):
        return self.page.locator("input[formcontrolname='accountNumber']").first

    @property
    def ifsc_input(self):
        return self.page.locator("input[formcontrolname='ifscCode']").first

    # Inline validation messages have no error class of their own - they are
    # plain text inside the field's `.form-group`. Read them with
    # `BasePage.form_group_text`.

    # ---- reads ----------------------------------------------------------
    def wait_ready(self) -> None:
        """Wait for the Project Information form to render."""
        self.wait_visible(self.escrow_id_input)

    def generated_escrow_id(self) -> str:
        """The auto-populated, readonly Escrow ID (e.g. EXC-0826-427439)."""
        self.wait_visible(self.escrow_id_input)
        return self.escrow_id_input.input_value().strip()

    def industry_options(self) -> List[str]:
        return self._option_labels(self.industry_select)

    def bank_options(self) -> List[str]:
        return self._option_labels(self.bank_select)

    def wait_for_options(self, select) -> None:
        """Wait for a `<select>` to be populated.

        Industry and Bank render with only their placeholder option and are
        filled in from an API call a moment later, so reading (or selecting)
        immediately sees an empty list.
        """
        self.wait_visible(select)
        expect(select.locator("option").nth(1)).to_be_attached(timeout=20_000)

    def _option_labels(self, select) -> List[str]:
        self.wait_for_options(select)
        return [t.strip() for t in select.locator("option").all_text_contents()
                if t.strip()]

    def available_account_types(self) -> List[str]:
        """Account types offered for the currently selected bank."""
        self.click(self.account_type_trigger)
        options = self.account_type_options
        options.first.wait_for(state="visible", timeout=10_000)
        labels = [t.strip() for t in options.all_text_contents() if t.strip()]
        self.account_type_trigger.click()          # close the panel
        return labels

    # ---- actions --------------------------------------------------------
    def choose_industry(self, label: str) -> None:
        self.wait_for_options(self.industry_select)
        self.select_native(self.industry_select, label=label)

    def choose_bank(self, label: str) -> None:
        """Selecting the bank is what loads the Account Type options."""
        self.wait_for_options(self.bank_select)
        self.select_native(self.bank_select, label=label)

    def select_account_types(self, *labels: str) -> None:
        self.click(self.account_type_trigger)
        options = self.account_type_options
        options.first.wait_for(state="visible", timeout=10_000)
        available = [t.strip() for t in options.all_text_contents()]
        if available == ["No data available"]:
            raise AssertionError(
                "Account Type has no options - select a Bank first "
                "(its options are loaded per bank)."
            )
        for label in labels:
            match = options.filter(has_text=re.compile(re.escape(label), re.I)).first
            match.wait_for(state="visible", timeout=5_000)
            match.click()
            self.page.wait_for_timeout(400)
        self.account_type_trigger.click()          # close the panel
        chosen = self.account_type_trigger.inner_text().lower()
        missing = [l for l in labels if l.lower() not in chosen]
        assert not missing, (
            f"Account Type reads {self.account_type_trigger.inner_text()!r} - "
            f"missing {missing}"
        )

    def selected_account_types(self) -> List[str]:
        """Labels currently chosen, read off the multiselect's trigger.

        The trigger renders each selection as "<label> x" (the x removes it).
        """
        text = self.account_type_trigger.inner_text()
        return [line.replace(" x", "").strip()
                for line in text.splitlines() if line.strip()
                and "select account type" not in line.lower()]

    def fill_details(self, *, name: str, industry: str, bank: str,
                     account_types: List[str] | str, account_number: str,
                     ifsc: str) -> None:
        if isinstance(account_types, str):
            account_types = [account_types]
        self.fill(self.escrow_name_input, name)
        self.choose_industry(industry)
        # Bank before Account Type: the account-type options depend on it.
        self.choose_bank(bank)
        self.select_account_types(*account_types)
        self.fill(self.account_number_input, account_number)
        self.fill(self.ifsc_input, ifsc)
