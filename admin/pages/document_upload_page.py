"""Wizard stage 3 - Documents.

There is no inline file input: the stage starts empty ("No documents uploaded
yet.") behind an "Upload Your First Document" button that opens an "Add New
Document" modal containing

    #documentFile   file, accept=.pdf,.jpg,.jpeg,.png,.doc,.docx   (required)
    #documentName   text                                          (required)
    #signingDate / #effectiveDate / #expiryDate   bsDatepicker    (optional)

and Cancel / Save Document. At least one saved document is required before
'Next' enables.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable

from playwright.sync_api import expect

from core.base_page import BasePage


class DocumentUploadPage(BasePage):
    # ---- locators -------------------------------------------------------
    @property
    def add_document_button(self):
        """Empty-state button, or the equivalent once documents exist."""
        return self.page.get_by_role(
            "button", name=re.compile(r"(upload|add)[^.]*document", re.I)
        ).first

    @property
    def modal(self):
        return self.page.locator(".modal-content, .modal, [role='dialog']").first

    @property
    def file_input(self):
        return self.page.locator("#documentFile, input[type='file']").first

    @property
    def document_name_input(self):
        return self.page.locator("#documentName, input[formcontrolname='name']").first

    @property
    def signing_date_input(self):
        return self.page.locator("#signingDate").first

    @property
    def effective_date_input(self):
        return self.page.locator("#effectiveDate").first

    @property
    def expiry_date_input(self):
        return self.page.locator("#expiryDate").first

    @property
    def save_document_button(self):
        return self.page.get_by_role(
            "button", name=re.compile(r"save\s*document", re.I)
        ).first

    @property
    def cancel_button(self):
        return self.page.get_by_role("button", name=re.compile(r"^cancel$", re.I)).first

    @property
    def uploaded_items(self):
        """Saved documents render as bootstrap cards, not table rows."""
        return self.page.locator(".card:has(.card-title h5)")

    @property
    def document_titles(self):
        return self.page.locator(".card .card-title h5")

    def document_card(self, name: str):
        return self.uploaded_items.filter(
            has_text=re.compile(re.escape(name), re.I)
        ).first

    def delete_links(self):
        return self.page.locator("a[title='Delete Document']")

    def view_links(self):
        return self.page.locator("a[title='View Document']")

    # The modal shows no validation text of its own: an incomplete or (as of
    # today) an unsupported/oversize file is signalled only by Save Document
    # staying disabled.

    # ---- actions --------------------------------------------------------
    def wait_ready(self) -> None:
        """Wait for the Documents stage to render."""
        self.wait_visible(self.add_document_button)

    def open_modal(self) -> None:
        self.click(self.add_document_button)
        self.wait_visible(self.file_input)

    def set_dates(self) -> dict:
        """Fill the three optional dates.

        Signing/Effective disallow past days (today is the earliest selectable),
        so both take the first enabled day; Expiry is pushed a few months out.
        """
        return {
            "signing": self.pick_bs_date(self.signing_date_input, day_index=0),
            "effective": self.pick_bs_date(self.effective_date_input, day_index=0),
            "expiry": self.pick_bs_date(self.expiry_date_input, months_ahead=3,
                                        day_index=5),
        }

    def add_document(self, path: str, *, name: str | None = None,
                     with_dates: bool = True) -> dict:
        """Open the modal, attach one file, name it, optionally date it, save.

        Returns the dates that were chosen (empty when `with_dates` is False).
        """
        self.open_modal()
        self.file_input.set_input_files(path)
        self.fill(self.document_name_input, name or Path(path).stem)
        dates = self.set_dates() if with_dates else {}
        self.click(self.save_document_button)
        self.save_document_button.wait_for(state="hidden", timeout=60_000)
        return dates

    def upload_files(self, paths: str | Iterable[str], *,
                     with_dates: bool = True) -> int:
        """Add each file as its own document; returns the number added."""
        items = [paths] if isinstance(paths, str) else list(paths)
        for index, path in enumerate(items, start=1):
            self.add_document(path, name=f"{Path(path).stem}_{index}",
                              with_dates=with_dates)
        return len(items)

    def uploaded_count(self) -> int:
        return self.document_titles.count()

    def uploaded_names(self) -> list[str]:
        return [t.strip() for t in self.document_titles.all_text_contents()]

    def is_empty(self) -> bool:
        return "no documents uploaded" in self.body_text().lower()

    @property
    def delete_confirm_dialog(self):
        """Deleting asks "Are You Sure? ... cannot be undone" with Submit/Cancel.

        Matched on the delete wording specifically - the submit-the-escrow
        confirmation opens an otherwise identical "Are You Sure?" dialog.
        """
        return self.page.locator(".modal-content, .modal, [role='dialog']").filter(
            has_text=re.compile(r"delete this document", re.I)
        ).first

    def _open_delete_confirmation(self, index: int = 0):
        self.click(self.delete_links().nth(index))
        dialog = self.delete_confirm_dialog
        dialog.wait_for(state="visible", timeout=15_000)
        return dialog

    def remove_first(self) -> None:
        before = self.uploaded_count()
        dialog = self._open_delete_confirmation()
        self.click(dialog.get_by_role(
            "button", name=re.compile(r"submit|^\s*yes|delete|confirm", re.I)).first)
        expect(self.document_titles).to_have_count(before - 1, timeout=30_000)

    def cancel_remove_first(self) -> None:
        dialog = self._open_delete_confirmation()
        self.click(dialog.get_by_role(
            "button", name=re.compile(r"cancel|^\s*no\s*$", re.I)).first)
        dialog.wait_for(state="hidden", timeout=15_000)
