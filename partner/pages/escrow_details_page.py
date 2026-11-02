"""Escrow details wizard on the partner side: Next -> Next -> verify id."""
from __future__ import annotations

import re

from core.base_page import BasePage


class PartnerEscrowDetailsPage(BasePage):
    @property
    def next_button(self):
        return self.page.get_by_role(
            "button", name=re.compile(r"next|continue|proceed", re.I)
        ).first

    def next(self) -> None:
        self.wait_visible(self.next_button)
        self.click(self.next_button)

    def contains_escrow_id(self, escrow_id: str) -> bool:
        """Verify the added Escrow ID is present on the details view."""
        if self.is_visible(self.page.get_by_text(escrow_id, exact=False), timeout=8_000):
            return True
        return escrow_id.lower() in self.body_text().lower()
