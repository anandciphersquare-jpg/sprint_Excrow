"""Trustee block (wizard stage 1, below Company Representative).

Three radios in a bootstrap btn-group. The inputs themselves are visually
hidden (`.btn-check`), so they must be driven through their `<label for=...>`.
"""
from __future__ import annotations

from core.base_page import BasePage

# option key -> (input id, visible label)
OPTIONS = {
    "not_required": ("trusteeNotRequired0", "Not Required"),
    "view_only": ("trusteeViewOnly0", "View Only"),
    "transaction_approval": ("trusteePayoutApproval0", "Transaction Approval"),
}


class TrusteePage(BasePage):
    def radio(self, key: str):
        return self.page.locator(f"#{OPTIONS[key][0]}")

    def label(self, key: str):
        return self.page.locator(f"label[for='{OPTIONS[key][0]}']")

    @property
    def trustee_name_input(self):
        return self.page.locator(
            "input[formcontrolname='trusteeName'], input[name='trusteeName']"
        ).first

    # ---- actions --------------------------------------------------------
    def set_option(self, key: str) -> None:
        self.click(self.label(key))
        assert self.radio(key).is_checked(), f"trustee option {key!r} did not take"

    def set_not_required(self) -> None:
        self.set_option("not_required")

    def selected_option(self) -> str | None:
        for key in OPTIONS:
            if self.radio(key).is_checked():
                return key
        return None

    def trustee_fields_hidden(self) -> bool:
        return not self.is_visible(self.trustee_name_input, timeout=1_500)
