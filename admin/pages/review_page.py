"""Wizard stage 4 - Review and submit.

Submitting is two steps: the page's own Submit opens an "Are You Sure? You are
about to create Excrow with these details: EXC-…" dialog carrying a second
Submit. The dialog is the portal's own echo of the Escrow ID, so it doubles as
a cross-check of the readonly `excrow_id` captured back on stage 1.
"""
from __future__ import annotations

import re
import time

from playwright.sync_api import expect

from core.base_page import BasePage

ESCROW_ID_PATTERN = re.compile(r"EXC-\d{4}-\d{4,}", re.I)
DIALOG = ".swal2-popup, .modal-content, .modal, [role='dialog']"
SUCCESS_WORDS = re.compile(r"success|created|submitted|congratulation", re.I)


class ReviewPage(BasePage):
    def wait_ready(self) -> None:
        """Wait for the Review stage to render."""
        self.wait_visible(self.submit_button)

    # ---- section reads --------------------------------------------------
    def section_visible(self, title: str) -> bool:
        return self.is_visible(
            self.page.get_by_text(re.compile(title, re.I)).first, timeout=4_000
        )

    def section_container(self, title: str):
        return self.page.get_by_text(re.compile(title, re.I)).first.locator(
            "xpath=ancestor-or-self::*[self::section or self::div][1]"
        )

    # ---- navigation -----------------------------------------------------
    @property
    def previous_button(self):
        return self.page.get_by_role("button", name=re.compile(r"previous", re.I)).first

    # Each review section carries its own Edit affordance. It is an <a> with no
    # href - so it has no `link` role and `get_by_role("button", …)` will never
    # find it. Match the markup instead.
    def edit_links(self):
        return self.page.locator("a:has(.icon-edit)")

    def sections(self) -> list[str]:
        """Review section titles, in order (each has its own Edit link)."""
        return [t.strip() for t in
                self.page.locator(".card-header").all_text_contents()]

    def edit_section(self, title: str) -> None:
        """Click Edit on the section whose header matches `title`."""
        header = self.page.locator(".card-header").filter(
            has_text=re.compile(title, re.I)
        ).first
        header.wait_for(state="visible", timeout=15_000)
        self.click(header.locator("a:has(.icon-edit)").first)

    def edit_first_section(self) -> None:
        self.click(self.edit_links().first)

    # ---- submit ---------------------------------------------------------
    @property
    def submit_button(self):
        return self.page.get_by_role(
            "button",
            name=re.compile(r"submit(\s*application)?|finish|create\s*e[sx]crow|"
                            r"^\s*create\s*$", re.I),
        ).first

    # ---- the "Are You Sure?" confirmation ------------------------------
    @property
    def confirm_dialog(self):
        return self.page.locator(DIALOG).filter(
            has_text=re.compile(r"are\s*you\s*sure|about\s*to\s*create", re.I)
        ).first

    @property
    def confirm_submit_button(self):
        """The dialog's own Submit - must be scoped, the page has one too."""
        return self.confirm_dialog.get_by_role(
            "button", name=re.compile(r"submit|confirm|^\s*yes", re.I)
        ).or_(self.page.locator(".swal2-confirm")).first

    def submit(self, *, confirm: bool = True) -> str:
        """Submit the wizard; returns the Escrow ID echoed by the dialog.

        Pass confirm=False to stop at the confirmation dialog without creating
        anything.
        """
        btn = self.submit_button
        btn.wait_for(state="visible", timeout=30_000)
        try:
            expect(btn).to_be_enabled(timeout=15_000)
        except AssertionError:
            raise AssertionError(
                f"Submit stayed disabled; invalid controls: {self.invalid_controls()}"
            ) from None
        btn.click()

        self.confirm_dialog.wait_for(state="visible", timeout=30_000)
        match = ESCROW_ID_PATTERN.search(self.confirm_dialog.inner_text())
        confirmed_id = match.group(0).strip() if match else ""
        if confirm:
            self.click(self.confirm_submit_button)
        return confirmed_id

    def cancel_submit(self) -> None:
        self.click(self.confirm_dialog.get_by_role(
            "button", name=re.compile(r"cancel|^\s*no\s*$", re.I)).first)

    # ---- post-submit ----------------------------------------------------
    @property
    def success_banner(self):
        return self.page.locator(DIALOG).filter(has_text=SUCCESS_WORDS).first

    def assert_success(self, *, timeout: int = 45_000) -> str:
        """Wait for the portal to confirm creation.

        Accepts either an explicit success dialog or a redirect back to the
        escrow dashboard - both mean the record was accepted. Matching bare
        page text is not safe here: the dashboard list has a "Created" column
        header that would pass for a success message.
        """
        on_dashboard = re.compile(r"#/e[sx]crow/dashboard", re.I)
        deadline = time.monotonic() + timeout / 1000
        while time.monotonic() < deadline:
            if self.success_banner.count() and self.success_banner.is_visible():
                text = self.success_banner.inner_text()
                self.acknowledge_success()
                return text
            if on_dashboard.search(self.page.url):
                return "redirected to escrow dashboard"
            self.page.wait_for_timeout(500)
        raise AssertionError(
            f"no creation confirmation within {timeout}ms; url={self.page.url!r}, "
            f"body tail={self.body_text()[-400:]!r}"
        )

    def acknowledge_success(self) -> None:
        """Dismiss the success dialog if it has an OK/Okay/Done button."""
        ok = self.success_banner.get_by_role(
            "button", name=re.compile(r"^\s*(ok(ay)?|done|close|continue)\s*$", re.I)
        ).first
        if self.is_visible(ok, timeout=3_000):
            ok.click()

    def created_escrow_id(self) -> str:
        """Escrow ID visible after submit ('' if the screen omits it)."""
        match = ESCROW_ID_PATTERN.search(self.body_text())
        return match.group(0).strip() if match else ""
