"""Partner login - calibrated against the live UAT DOM.

Login is **two-step**. Email + password only gets you as far as an OTP
challenge:

    POST /auth/login -> {"allowTwoStep": true, "message": "OTP send successfully."}

The portal then shows "Verify it's you - Enter the 4-digit code we just sent
you" against a masked address (`anand************@paysprint.in`), four
single-character boxes, a resend countdown and "Verify & sign in".

**The code is delivered by email**, so completing a partner login needs
mailbox access. `login()` therefore takes an optional `otp`; without one it
stops at the challenge and `otp_challenge_visible()` reports that it got there.

Other calibration notes:

* The email field is `#email`; the password input carries no
  formcontrolname/id/name, so it is addressed by type.
* "Login" is disabled until the form is valid - invalid input is signalled by
  the disabled button and `ng-invalid`, never by an inline message.
"""
from __future__ import annotations

import re
import time

from playwright.sync_api import expect

from config.settings import settings
from core.base_page import BasePage


class PartnerLoginPage(BasePage):
    def open(self) -> None:
        self.goto(settings.partner_base_url)
        self.wait_visible(self.username_input)

    # ---- credentials step ------------------------------------------------
    @property
    def username_input(self):
        return self.page.locator("#email, input[formcontrolname='email']").first

    @property
    def password_input(self):
        return self.page.locator("input[type='password']").first

    @property
    def login_button(self):
        return self.page.get_by_role("button", name=re.compile(r"^\s*login\s*$", re.I)).first

    @property
    def forgot_password_link(self):
        return self.page.get_by_text(re.compile(r"forgot\s*password", re.I)).first

    # ---- OTP step --------------------------------------------------------
    @property
    def otp_boxes(self):
        return self.page.locator("input[maxlength='1']")

    @property
    def verify_button(self):
        return self.page.get_by_role(
            "button", name=re.compile(r"verify\s*&?\s*sign\s*in", re.I)
        ).first

    @property
    def otp_heading(self):
        return self.page.get_by_text(re.compile(r"verify\s*it'?s\s*you", re.I)).first

    def otp_challenge_visible(self, timeout: int = 20_000) -> bool:
        return self.is_visible(self.otp_heading, timeout=timeout)

    def masked_email(self) -> str:
        """The partially hidden address the code was sent to."""
        match = re.search(r"[A-Za-z0-9._%+-]*\*+[A-Za-z0-9._%+-]*@[\w.-]+",
                          self.body_text())
        return match.group(0) if match else ""

    def resend_text(self) -> str:
        match = re.search(r"resend code(?: in (\d+)\s*s)?", self.body_text(), re.I)
        return match.group(0) if match else ""

    def wait_for_resend_countdown(self, timeout: int = 15_000) -> str:
        """Wait for the resend countdown to tick in.

        The "Verify it's you" heading renders a beat before the countdown line,
        so reading it immediately after the challenge appears finds nothing.
        """
        deadline = time.monotonic() + timeout / 1000
        while time.monotonic() < deadline:
            text = self.resend_text()
            if re.search(r"\d+\s*s", text):
                return text
            self.page.wait_for_timeout(500)
        return self.resend_text()

    # ---- actions ---------------------------------------------------------
    def submit_credentials(self, username: str, password: str) -> None:
        """Fill and submit step 1. Does not assume it succeeds."""
        self.fill(self.username_input, username)
        self.fill(self.password_input, password)
        self.click(self.login_button)

    def enter_otp(self, otp: str) -> None:
        boxes = self.otp_boxes
        boxes.first.wait_for(state="visible", timeout=20_000)
        if boxes.count() >= len(otp):
            for index, digit in enumerate(otp):
                boxes.nth(index).fill(digit)
        else:                      # a single combined field
            boxes.first.fill(otp)
        self.click(self.verify_button)

    def login(self, username: str, password: str, otp: str | None = None) -> None:
        """Sign in. Without `otp` this stops at the OTP challenge.

        The code arrives by email, so callers that need a real session must
        supply one (see `PARTNER_OTP`).
        """
        self.submit_credentials(username, password)
        if otp:
            self.enter_otp(otp)

    def assert_logged_in(self) -> None:
        expect(self.page).not_to_have_url(re.compile(r"/auth/login", re.I),
                                          timeout=settings.default_timeout_ms)
