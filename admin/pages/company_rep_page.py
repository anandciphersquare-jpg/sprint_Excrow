"""Company Representative block (wizard stage 1, below Project Information).

The email drives everything, and there are **two paths**:

*Existing partner* - the portal looks the address up on blur and fills
First/Last name, Company Name and Mobile Number, all **readonly**. Never type
them.

*New address* - the lookup pops an informational **"User not found!"** alert.
Dismissing it leaves those same four fields empty but **editable**, so they are
typed in by hand and the partner is created as part of the escrow. This is the
onboarding path: it is what makes the portal email a welcome message with a
User ID and a temporary password.
"""
from __future__ import annotations

import re

from playwright.sync_api import Locator, expect

from core.base_page import BasePage

# formcontrolname -> what the portal fills in from the partner record.
AUTOFILLED = ("fname", "lname", "companyName", "mobileNumber")


class CompanyRepPage(BasePage):
    @property
    def email_input(self):
        return self.page.locator("input[formcontrolname='email']").first

    @property
    def first_name_input(self):
        return self.page.locator("input[formcontrolname='fname']").first

    @property
    def last_name_input(self):
        return self.page.locator("input[formcontrolname='lname']").first

    @property
    def company_name_input(self):
        return self.page.locator("input[formcontrolname='companyName']").first

    @property
    def mobile_input(self):
        return self.page.locator("input[formcontrolname='mobileNumber']").first

    @property
    def loader(self):
        return self.page.locator(
            ".loader, .spinner, mat-progress-spinner, [role='progressbar']"
        ).first

    @property
    def user_not_found_alert(self):
        return self.page.locator(".swal2-popup").filter(
            has_text=re.compile(r"user not found", re.I)
        ).first

    # ---- actions --------------------------------------------------------
    def dismiss_user_not_found(self, *, timeout: int = 25_000) -> bool:
        """Clear the "User not found!" alert; True if one appeared.

        It is informational, not a rejection - the address is still usable, it
        just has to be filled in manually. The alert blocks pointer events
        until dismissed, so nothing else on the form can be touched first.
        """
        alert = self.user_not_found_alert
        if not self.is_visible(alert, timeout=timeout):
            return False
        alert.get_by_role("button", name=re.compile(r"^\s*ok(ay)?\s*$", re.I)
                          ).first.click()
        alert.wait_for(state="hidden", timeout=15_000)
        return True

    def enter_new_representative(self, rep) -> bool:
        """Onboard an address with no partner record, typing the details in.

        Returns whether the "User not found!" alert appeared - False means the
        address already existed, so the caller asked for the wrong path.
        """
        self.fill(self.email_input, rep.email)
        self.email_input.press("Tab")
        appeared = self.dismiss_user_not_found()
        self.fill(self.first_name_input, rep.first_name)
        self.fill(self.last_name_input, rep.last_name)
        self.fill(self.company_name_input, rep.company_name)
        self.fill(self.mobile_input, rep.mobile)
        return appeared

    def enter_email_and_wait_autofill(self, email: str) -> dict:
        """Type the partner email and wait for the lookup to populate the rest."""
        self.fill(self.email_input, email)
        self.email_input.press("Tab")          # blur triggers the lookup
        if self.is_visible(self.loader, timeout=1_500):
            self.loader.wait_for(state="hidden", timeout=20_000)
        # First name is the earliest field to come back; wait on it, then read all.
        expect(self.first_name_input).not_to_have_value("", timeout=25_000)
        return self.autofilled_values()

    def autofilled_values(self) -> dict:
        return {
            name: self.page.locator(f"input[formcontrolname='{name}']").first.input_value()
            for name in AUTOFILLED
        }

    def autofilled_fields_readonly(self) -> bool:
        """The looked-up partner details must not be editable."""
        return all(
            self.is_readonly(self.page.locator(f"input[formcontrolname='{name}']").first)
            for name in AUTOFILLED
        )

    @staticmethod
    def is_readonly(locator: Locator) -> bool:
        return (locator.get_attribute("readonly") is not None
                or locator.get_attribute("disabled") is not None
                or locator.is_disabled())
