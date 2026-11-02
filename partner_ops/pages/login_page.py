"""Partner login for the operations suite - calibrated 14 Aug 2026.

Everything here is verified against live UAT. Two behaviours dominate the
design and both were discovered the hard way:

**1. Trusted-location 2FA, and the lockout it causes (DEF-OPS-01, Critical).**
`POST /auth/login` carries the browser's `lat`/`lng` (the form will not even
enable until `navigator.geolocation` answers - hence the geolocation permission
in the context fixtures). The backend then branches on whether it has seen that
location before:

* **new location**  -> `{"allowTwoStep": true}`, no token, OTP challenge shown.
* **known location** -> `{"allowTwoStep": false, firstLogin, authtoken}`.

The Angular client only implements the first branch. On the second it receives a
perfectly valid `authtoken`, does nothing with it, and leaves the user on
`#/auth/login` - no error, no navigation, empty storage. Reproduced directly
against the API: same coordinates twice returns `false` both times, a fresh set
of coordinates returns `true`. So a partner who signs in twice from the same
office is locked out of the portal permanently.

`login()` therefore defaults to `fresh_location=True`, rewriting the request
body with never-before-seen coordinates so the OTP branch is taken. That is a
**documented workaround for a product defect**, not a convenience: it is the
only way this suite can reach any authenticated screen. `login(fresh_location=
False)` deliberately reproduces the lockout and is what `test_login_and_session`
asserts against.

**2. A two-minute idle timeout.** After ~2 minutes without activity a modal
appears - "Are you still there? ... Logging out in 60 seconds" with *Logout Now*
and *Continue Session* - and then the app routes to `#/auth/session-expired`.
Any step that idles longer than that dies mid-test, so long flows must either
keep the session warm (`keep_alive()`) or accept the interruption.

Other calibration notes:

* Email is `#email`; the password input carries no formcontrolname and is
  addressed by type.
* Login OTP is **4 boxes** (`input[maxlength='1']`) and auto-submits on the
  fourth digit. UAT accepts the fixed code `1234`.
* Payout OTP is **6 boxes** and takes `123456` - a different length, so never
  share one helper between them blindly.
"""
from __future__ import annotations

import json
import random
import re
import time

from playwright.sync_api import expect

from config.settings import settings
from core.base_page import BasePage

LOGIN_ENDPOINT = "**/EXcrowPartnerBackendV2/auth/login"
SESSION_EXPIRED_ROUTE = "#/auth/session-expired"


class PartnerOpsLoginPage(BasePage):
    # ---- step 1: credentials --------------------------------------------
    @property
    def username_input(self):
        return self.page.locator("#email, input[formcontrolname='email']").first

    @property
    def password_input(self):
        return self.page.locator("input[type='password']").first

    @property
    def login_button(self):
        return self.page.get_by_role(
            "button", name=re.compile(r"^\s*(login|location loading)", re.I)).first

    @property
    def forgot_password_link(self):
        return self.page.get_by_text(re.compile(r"forgot\s*password", re.I)).first

    # ---- step 2: OTP ----------------------------------------------------
    @property
    def otp_boxes(self):
        return self.page.locator("input[maxlength='1']")

    @property
    def verify_button(self):
        return self.page.get_by_role(
            "button", name=re.compile(r"verify\s*&?\s*sign\s*in", re.I)).first

    @property
    def otp_heading(self):
        return self.page.get_by_text(re.compile(r"verify\s*it'?s\s*you", re.I)).first

    # ---- session-timeout modal ------------------------------------------
    @property
    def session_modal(self):
        return self.page.locator(".session-timeout-modal-panel").first

    @property
    def continue_session_button(self):
        return self.page.get_by_role(
            "button", name=re.compile(r"continue\s*session", re.I)).first

    @property
    def logout_now_button(self):
        return self.page.get_by_role(
            "button", name=re.compile(r"logout\s*now", re.I)).first

    # ---- navigation ------------------------------------------------------
    def open(self) -> None:
        self.goto(settings.partner_base_url)
        # The button renders as a disabled "Location Loading..." until the
        # geolocation promise resolves; waiting for the field is enough.
        self.wait_visible(self.username_input)

    def wait_for_location_ready(self, timeout: int = 20_000) -> bool:
        """Wait for the location gate to lift - i.e. the button stops reading
        'Location Loading...' and becomes a real 'Login'.

        Deliberately does **not** wait for the button to be *enabled*: it is also
        disabled while the form is empty or invalid, so treating 'enabled' as
        'location ready' reports a location failure on a blank form. Callers that
        need it clickable should fill the form first and wait on enablement.
        """
        deadline = time.monotonic() + timeout / 1000
        while time.monotonic() < deadline:
            try:
                text = (self.login_button.inner_text() or "").strip()
            except Exception:
                text = ""
            if text and not re.search(r"location", text, re.I):
                return True
            self.page.wait_for_timeout(400)
        return False

    def wait_until_submittable(self, timeout: int = 20_000) -> bool:
        """Location resolved *and* the form considered valid by Angular."""
        if not self.wait_for_location_ready(timeout=timeout):
            return False
        try:
            expect(self.login_button).to_be_enabled(timeout=timeout)
            return True
        except AssertionError:
            return False

    # ---- the DEF-OPS-01 workaround --------------------------------------
    def force_fresh_location(self) -> None:
        """Rewrite the login payload with coordinates never used before.

        Keeps the backend on the `allowTwoStep: true` branch, which is the only
        one the front end can complete. See the module docstring.
        """
        def handler(route):
            body: dict = {}
            try:
                body = json.loads(route.request.post_data or "{}")
            except (TypeError, ValueError):
                body = {}
            body["lat"] = f"{random.uniform(8.0, 34.0):.4f}"
            body["lng"] = f"{random.uniform(69.0, 92.0):.4f}"
            route.continue_(post_data=json.dumps(body))

        self.page.route(LOGIN_ENDPOINT, handler)

    def capture_login_responses(self) -> list[dict]:
        """Collect the parsed `auth/login` payloads for assertions."""
        seen: list[dict] = []

        def on_response(response):
            if not response.url.endswith("/auth/login"):
                return
            if response.request.method != "POST":
                return
            try:
                seen.append({"status": response.status, "json": response.json()})
            except Exception:
                seen.append({"status": response.status, "json": None})

        self.page.on("response", on_response)
        return seen

    # ---- actions ---------------------------------------------------------
    def submit_credentials(self, username: str, password: str) -> None:
        self.fill(self.username_input, username)
        self.fill(self.password_input, password)
        if not self.wait_until_submittable():
            raise AssertionError(
                "Login never became clickable. Button reads "
                f"{self.login_button.inner_text()!r} - if that mentions "
                "'Location', the browser context is not granting geolocation.")
        self.click(self.login_button)

    def otp_challenge_visible(self, timeout: int = 20_000) -> bool:
        return self.is_visible(self.otp_heading, timeout=timeout)

    def enter_login_otp(self, otp: str = "") -> None:
        """Fill the 4 login boxes. Entering the last digit auto-submits."""
        otp = otp or settings.partner_login_otp
        boxes = self.otp_boxes
        boxes.first.wait_for(state="visible", timeout=20_000)
        count = boxes.count()
        if count >= len(otp):
            for index, digit in enumerate(otp):
                boxes.nth(index).fill(digit)
        else:
            boxes.first.fill(otp)
        # Auto-submit is the observed behaviour, but do not rely on it.
        if self.is_visible(self.verify_button, timeout=3_000):
            try:
                if self.verify_button.is_enabled():
                    self.verify_button.click()
            except Exception:
                pass

    def login(self, username: str = "", password: str = "", *,
              otp: str = "", fresh_location: bool = True) -> None:
        """Full sign-in. `fresh_location=False` reproduces DEF-OPS-01."""
        if fresh_location:
            self.force_fresh_location()
        self.open()
        self.submit_credentials(username or settings.partner_username,
                                password or settings.partner_password)
        if self.otp_challenge_visible():
            self.enter_login_otp(otp)

    def assert_logged_in(self, timeout: int | None = None) -> None:
        expect(self.page).not_to_have_url(
            re.compile(r"/auth/(login|session-expired)", re.I),
            timeout=timeout or settings.default_timeout_ms)

    # ---- failure feedback ------------------------------------------------
    # DEF-PARTNER-02: a rejected login shows nothing at all. Asserting that
    # needs somewhere to look, and "nothing" is only credible if every place a
    # message *could* appear was checked - hence the full list rather than one
    # selector that happens to be absent.
    ERROR_SELECTORS = (".swal2-popup", ".toast", ".toast-message", ".alert",
                       ".text-danger", ".invalid-feedback", ".error-message",
                       "mat-error", "[role='alert']")

    def visible_error(self) -> str:
        """Whatever failure text the portal is showing, '' if none."""
        for selector in self.ERROR_SELECTORS:
            node = self.page.locator(selector).first
            try:
                if node.count() and node.is_visible():
                    text = (node.inner_text() or "").strip()
                    if text:
                        return text
            except Exception:
                continue
        return ""

    def message_visible(self, timeout: int = 4_000) -> bool:
        """Whether any feedback appears within `timeout` of a failed submit."""
        deadline = time.monotonic() + timeout / 1000
        while time.monotonic() < deadline:
            if self.visible_error():
                return True
            self.page.wait_for_timeout(400)
        return False

    # ---- session handling ------------------------------------------------
    def session_prompt_visible(self, timeout: int = 5_000) -> bool:
        return self.is_visible(self.session_modal, timeout=timeout)

    def continue_session(self) -> None:
        if self.session_prompt_visible(timeout=2_000):
            self.click(self.continue_session_button)
            self.page.wait_for_timeout(500)

    def is_session_expired(self) -> bool:
        return SESSION_EXPIRED_ROUTE in self.page.url

    def keep_alive(self) -> None:
        """Dismiss the idle prompt if it is up. Call between slow steps."""
        self.continue_session()

    def logout(self) -> None:
        avatar = self.page.locator(".stat-label").first     # any page chrome
        del avatar                                          # navigation varies
        self.goto(settings.partner_base_url.split("#")[0] + "#/auth/login")
