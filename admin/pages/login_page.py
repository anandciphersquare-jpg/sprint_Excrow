"""Step 1 - Super Admin login: email + password, then a 4-digit OTP.

Calibrated against the live UAT DOM: the form uses
`formcontrolname="email"` / `formcontrolname="password"` and a `Sign In`
submit button.

**The OTP step is mandatory now.** The console was redesigned and
`POST /auth/login` no longer lands on `#/dashboard`; it routes to
`#/auth/otp-verify` - *"Let's verify it's you - We've sent a 4-digit code to
your registered email/phone. It'll auto-verify once entered."* - and only a
successful `POST /auth/verifyOtp` reaches the dashboard. UAT accepts the fixed
code in `ADMIN_PIN`.

Two things about that screen cost a debugging cycle:

* **The boxes must be typed into, not filled.** They carry Angular directives
  (`appotpmask`, `appfocustonextinput`) that advance the focus and fire the
  auto-verify off real key events. `locator.fill()` sets each value and emits
  `input`, so all four digits appear on screen and *nothing happens* - the page
  sits on `#/auth/otp-verify` until the wait times out. `keyboard.type()` into
  the first box works.
* **There is no Verify button** - entering the fourth digit submits. The button
  lookup is kept only as a fallback for other builds.
"""
from __future__ import annotations

import re

from playwright.sync_api import expect

from config.settings import settings
from core.base_page import BasePage


class AdminLoginPage(BasePage):
    def open(self) -> None:
        self.goto(settings.admin_base_url)

    # ---- locators (calibrated against live UAT DOM) ---------------------
    @property
    def username_input(self):
        return self.page.locator(
            "input[formcontrolname='email'], input[formcontrolname='username'], "
            "input[name='username']"
        ).first

    @property
    def password_input(self):
        return self.page.locator("input[formcontrolname='password'], "
                                 "input[type='password']").first

    @property
    def login_button(self):
        return self.page.get_by_role(
            "button", name=re.compile(r"log\s*in|sign\s*in|submit", re.I)
        ).or_(self.page.locator("button[type='submit']")).first

    def pin_boxes(self):
        # Four single-character inputs. The live build renders them as
        # `input[type=text][inputmode=numeric][autocomplete=one-time-code]
        #  [maxlength=1].digit`; the rest of the list covers other widgets.
        return self.page.locator(
            "input[autocomplete='one-time-code'], input.digit[maxlength='1'], "
            "input[type='tel'], input[formcontrolname^='pin'], "
            ".pin-input input, .otp-input input, input[maxlength='1']"
        )

    @property
    def verify_pin_button(self):
        return self.page.get_by_role(
            "button", name=re.compile(r"verify|submit|proceed|continue|confirm", re.I)
        ).first

    # ---- actions --------------------------------------------------------
    def login(self, username: str, password: str, pin: str) -> None:
        self.fill(self.username_input, username)
        self.fill(self.password_input, password)
        self.click(self.login_button)
        self.enter_pin(pin)

    def enter_pin(self, pin: str) -> None:
        """Answer the OTP challenge *if* the portal raises one.

        Typed, not filled: see the module docstring. A missing widget is still
        tolerated so the page object survives a build that skips the challenge,
        but a present one that cannot be answered is a hard failure rather than
        something to walk past - stalling here used to be reported as a
        successful login.
        """
        boxes = self.pin_boxes()
        if not self.is_visible(boxes.first, timeout=10_000):
            if settings.admin_pin_required:
                raise AssertionError(
                    "ADMIN_PIN_REQUIRED=true but no PIN input appeared within "
                    f"10s; url={self.page.url!r}"
                )
            return

        if not pin:
            raise AssertionError(
                "The admin console raised its OTP challenge "
                f"({self.page.url}) but ADMIN_PIN is empty. Set ADMIN_PIN in "
                ".env to UAT's fixed code."
            )

        # Re-entering after a rejected code leaves the old digits in place, and
        # the mask directive will not overwrite them.
        count = boxes.count()
        for i in range(count):
            try:
                boxes.nth(i).fill("")
            except Exception:
                pass

        # Real keystrokes: `appfocustonextinput` moves the caret box to box and
        # the last digit triggers the auto-verify. One `fill()` per box sets the
        # values without ever submitting them.
        boxes.first.click()
        self.page.keyboard.type(pin, delay=120)

        # No Verify button on the current build; harmless where one exists.
        try:
            if self.is_visible(self.verify_pin_button, timeout=2_000) \
                    and self.verify_pin_button.is_enabled():
                self.click(self.verify_pin_button)
        except Exception:
            pass

        self.wait_for_otp_to_clear()

    def wait_for_otp_to_clear(self, timeout: int | None = None) -> None:
        """Block until the challenge is accepted, and say so plainly if not."""
        timeout = timeout or settings.default_timeout_ms
        try:
            expect(self.page).not_to_have_url(re.compile(r"#/auth/", re.I),
                                              timeout=timeout)
        except AssertionError:
            raise AssertionError(
                f"The OTP challenge was not accepted - still on {self.page.url!r} "
                f"after {timeout}ms. Either ADMIN_PIN is wrong for this "
                "environment, or UAT has stopped accepting a fixed code."
            ) from None

    def assert_dashboard_loaded(self) -> None:
        """Past *every* auth route, not merely past the credentials form.

        `#/auth/otp-verify` is not `#/auth/login`, so a check for the latter
        alone reported a login that was still sitting on the OTP screen as a
        success - and the 14 escrow creates that followed then failed 30s
        later looking for a sidebar that was never on screen.
        """
        expect(self.page).not_to_have_url(re.compile(r"#/auth/", re.I),
                                          timeout=settings.default_timeout_ms)
