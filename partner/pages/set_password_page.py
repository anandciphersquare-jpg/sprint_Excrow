"""The forced first-login password change (`#/auth/set-password`).

Reached only by a partner whose account was created by the escrow wizard: after
the OTP the portal routes there with *"This is your first sign-in, so the
password we emailed you has to be replaced before you can continue"* and three
boxes - **Password From Email**, **New Password**, **Confirm New Password** -
behind **Set Password & Continue**.

Locator strategy, and why: the three inputs are all `type=password` and the
build does not label them with stable `formcontrolname`s. Matching on the
visible label is the honest way to address them, so each field tries its label
first and only falls back to positional order (email / new / confirm, top to
bottom) when the label markup does not associate. The fallback is recorded by
`locator_strategy()` so a test can say *how* it found the field rather than
quietly asserting against the wrong box.
"""
from __future__ import annotations

import re

from playwright.sync_api import expect

from config.settings import settings
from core.base_page import BasePage

SET_PASSWORD_ROUTE = "#/auth/set-password"
CHANGE_PASSWORD_ENDPOINT = "**/changePassword"

# Field label -> the position it occupies when labels cannot be matched.
FIELD_ORDER = ("email_password", "new_password", "confirm_password")

LABELS = {
    "email_password": re.compile(r"password\s*from\s*e?mail|temporary\s*password"
                                 r"|old\s*password|current\s*password", re.I),
    "new_password": re.compile(r"^\s*new\s*password", re.I),
    "confirm_password": re.compile(r"confirm\s*(new\s*)?password|re-?enter", re.I),
}


class SetPasswordPage(BasePage):
    # ---- fields ---------------------------------------------------------
    def _password_inputs(self):
        return self.page.locator("input[type='password'], input[formcontrolname*='assword']")

    def field(self, name: str):
        """Locate one of the three boxes, label first, position second."""
        by_label = self.page.get_by_label(LABELS[name]).first
        if by_label.count():
            return by_label
        return self._password_inputs().nth(FIELD_ORDER.index(name))

    def locator_strategy(self, name: str) -> str:
        return "label" if self.page.get_by_label(LABELS[name]).first.count() else "position"

    @property
    def email_password_input(self):
        return self.field("email_password")

    @property
    def new_password_input(self):
        return self.field("new_password")

    @property
    def confirm_password_input(self):
        return self.field("confirm_password")

    @property
    def submit_button(self):
        return self.page.get_by_role(
            "button", name=re.compile(r"set\s*password|continue|submit|update", re.I)).first

    @property
    def first_sign_in_notice(self):
        return self.page.get_by_text(
            re.compile(r"first\s*sign[- ]?in|password we (e?mailed|sent)", re.I)).first

    # ---- state ----------------------------------------------------------
    def is_current(self, timeout: int = 20_000) -> bool:
        """Whether the portal has forced us onto the set-password screen."""
        deadline = self.page.context and timeout
        del deadline
        try:
            self.page.wait_for_url(re.compile(r"set-password", re.I), timeout=timeout)
            return True
        except Exception:
            return SET_PASSWORD_ROUTE in self.page.url

    def field_count(self) -> int:
        return self._password_inputs().count()

    def submit_enabled(self, timeout: int = 5_000) -> bool:
        try:
            expect(self.submit_button).to_be_enabled(timeout=timeout)
            return True
        except AssertionError:
            return False

    def message(self) -> str:
        """Any success/error surfaced after a submit.

        The portal uses SweetAlert for both outcomes and inline text for field
        validation, so all three are read; whichever is present wins.
        """
        popup = self.page.locator(".swal2-popup").first
        if self.is_visible(popup, timeout=6_000):
            return popup.inner_text().strip()
        for selector in (".toast", ".alert", ".invalid-feedback", ".text-danger",
                         ".error-message", "mat-error"):
            node = self.page.locator(selector).first
            if node.count() and node.is_visible():
                return node.inner_text().strip()
        return ""

    def dismiss_message(self) -> None:
        confirm = self.page.locator(".swal2-confirm").first
        if confirm.count() and confirm.is_visible():
            confirm.click()
            self.page.wait_for_timeout(500)

    # ---- actions --------------------------------------------------------
    def fill_form(self, old: str, new: str, confirm: str | None = None) -> None:
        self.fill(self.email_password_input, old)
        self.fill(self.new_password_input, new)
        self.fill(self.confirm_password_input,
                  new if confirm is None else confirm)
        # Angular validates on blur; without it the button can still read
        # disabled a beat after the last keystroke.
        self.confirm_password_input.press("Tab")
        self.page.wait_for_timeout(400)

    def capture_change_responses(self) -> list[dict]:
        """Collect `changePassword` responses so a test can assert the status.

        Worth having: DEF-PARTNER-01 showed this portal reporting failures as
        HTTP 500 with nothing on screen, so the UI alone is not evidence.
        """
        seen: list[dict] = []

        def on_response(response):
            if "changepassword" not in response.url.lower():
                return
            try:
                seen.append({"status": response.status, "json": response.json()})
            except Exception:
                seen.append({"status": response.status, "json": None})

        self.page.on("response", on_response)
        return seen

    def submit(self) -> str:
        """Submit the form and return whatever message came back ('' if none)."""
        if not self.submit_enabled():
            return ""
        self.click(self.submit_button)
        self.page.wait_for_timeout(2_000)
        return self.message()

    def change_password(self, old: str, new: str) -> str:
        """The happy path: replace the emailed password."""
        self.fill_form(old, new)
        if not self.submit_enabled(timeout=10_000):
            raise AssertionError(
                "'Set Password & Continue' stayed disabled for a valid password; "
                f"invalid controls: {self.invalid_controls()}")
        return self.submit()

    def try_password(self, old: str, new: str, confirm: str | None = None) -> dict:
        """Attempt one (possibly invalid) password and report what happened.

        Returns `enabled` (did the form even allow a submit) and `message`, which
        is how the policy cases in `test_new_partner_onboarding` distinguish
        "blocked at the form" from "rejected by the server".
        """
        self.fill_form(old, new, confirm)
        enabled = self.submit_enabled(timeout=4_000)
        message = ""
        if enabled:
            message = self.submit()
            self.dismiss_message()
        else:
            message = self.inline_errors()
        return {"enabled": enabled, "message": message,
                "invalid": self.invalid_controls()}

    def inline_errors(self) -> str:
        """Validation text rendered around the three fields."""
        parts = []
        for name in FIELD_ORDER:
            try:
                parts.append(self.form_group_text(self.field(name)))
            except Exception:
                continue
        return " | ".join(p.strip() for p in parts if p and p.strip())

    def open_direct(self) -> None:
        """Navigate straight to the route - used to test it is guarded."""
        base = settings.partner_base_url.split("#")[0]
        self.goto(base + SET_PASSWORD_ROUTE)
