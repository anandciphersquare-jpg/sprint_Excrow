"""Escrow Management: navigate, search by Escrow ID, open View Details."""
from __future__ import annotations

import re

from core.base_page import BasePage


class PartnerEscrowManagementPage(BasePage):
    @property
    def escrow_management_menu(self):
        return self.page.get_by_role(
            "link", name=re.compile(r"escrow\s*management", re.I)
        ).or_(self.page.get_by_text(re.compile(r"escrow\s*management", re.I))).first

    @property
    def search_input(self):
        return self.page.get_by_placeholder(re.compile(r"search", re.I)).or_(
            self.page.locator("input[type='search'], input[name*='search' i]")
        ).first

    def result_row(self, escrow_id: str):
        return self.page.get_by_role("row", name=re.compile(re.escape(escrow_id), re.I)).or_(
            self.page.locator("tr, .list-item, .card").filter(has_text=escrow_id)
        ).first

    def open(self) -> None:
        self.click(self.escrow_management_menu)

    def search(self, escrow_id: str) -> None:
        self.fill(self.search_input, escrow_id)
        self.search_input.press("Enter")
        self.wait_visible(self.result_row(escrow_id))

    def open_view_details(self, escrow_id: str) -> None:
        row = self.result_row(escrow_id)
        view = row.get_by_role("button", name=re.compile(r"view|details|eye", re.I)).or_(
            row.get_by_role("link", name=re.compile(r"view|details", re.I))
        ).or_(row.locator("a, button").filter(has_text=re.compile(r"view|details", re.I))).first
        self.click(view)
