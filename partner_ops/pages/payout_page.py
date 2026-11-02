"""Payout: the Make Payout wizard, its transfer modes, limits and OTP.

Calibrated 14 Aug 2026 against `#/escrow-detail/payout`, including a real
payout executed end to end.

The Payout tab renders the **beneficiary** list again (`Bene Name | Bank name |
Account Number | IFSC Code | Status | Action`) with a *Payout* link per row,
plus *Bulk Payout* and *Payout Batches*. A **Deactive beneficiary has no Payout
link at all** - that is how the product blocks paying a disabled beneficiary,
so the negative test asserts the absence of the control rather than expecting an
error message.

`Make Payout` is a 3-step modal (`1 DETAILS -> 2 VERIFY -> 3 DONE`):

* **Details** - optional document upload (jpeg/jpg/png/pdf), `paymentType`
  select (only *Bank Transfer*), `mode` select (**IMPS / NEFT / RTGS / A2A**),
  `Amount` (note the capital A in the control name), `remarks` textarea, and a
  *Make Payout* button that fires `payout/excrow/send-payout-otp`.
* **Verify** - `₹<amount> to <bene> · <mode>`, six `input[maxlength='1']`
  boxes, a resend countdown, and *Verify & Pay* which posts
  `payout/excrow-payout-request`. UAT accepts `123456`.
* **Done** - the success state.

Three findings encoded here, all reproduced live:

1. **No charge is ever disclosed before authorisation** (DEF-OPS-03). Neither
   Details nor Verify shows the fee; Verify shows only the amount. The partner
   authorises a wallet debit whose size the portal never states.
   `charge_disclosed()` exists so the test can assert this rather than describe it.
2. **Per-mode amount limits are enforced only after the OTP is consumed**
   (DEF-OPS-04). A ₹1 IMPS payout sails through Details and the OTP step, then
   fails at the final request with *"For imps, enter an amount between ₹10 and
   ₹500000"*. `MODE_LIMITS` records the limits the API states.
3. **Failures return HTTP 201** with `status:false` and `responsecode:3`
   (`Invalid OTP!`, `Your request has been failed.`) instead of a 4xx, so a
   status-code assertion alone would call a rejection a success. Always read the
   body - `last_result()` does.
"""
from __future__ import annotations

import re
from decimal import Decimal

from core.base_page import BasePage
from partner_ops.data.pricing import money

MODE_IMPS = "IMPS"
MODE_NEFT = "NEFT"
MODE_RTGS = "RTGS"
MODE_A2A = "A2A"
ALL_MODES = (MODE_IMPS, MODE_NEFT, MODE_RTGS, MODE_A2A)

PAYMENT_TYPE_BANK_TRANSFER = "Bank Transfer"

# Limits as stated by the API's own rejection message. Only IMPS has been
# observed directly; the rest are probed by the boundary test and recorded
# rather than assumed, so a wrong entry here shows up as a test failure with
# the portal's real wording attached.
MODE_LIMITS: dict[str, tuple[Decimal, Decimal] | None] = {
    MODE_IMPS: (Decimal("10"), Decimal("500000")),
    MODE_NEFT: None,
    MODE_RTGS: None,
    MODE_A2A: None,
}

OTP_LENGTH = 6
SEND_OTP_ENDPOINT = "payout/excrow/send-payout-otp"
PAYOUT_ENDPOINT = "payout/excrow-payout-request"

LIMIT_MESSAGE = re.compile(
    r"for\s+(\w+),\s*enter an amount between\s*₹?([\d,]+)\s*and\s*₹?([\d,]+)", re.I)


class PayoutResult:
    """The outcome of a payout attempt, read from the API body not the status."""

    def __init__(self, status: int, payload: dict | None, ui_text: str = ""):
        self.status = status
        self.payload = payload or {}
        self.ui_text = ui_text

    @property
    def message(self) -> str:
        return str(self.payload.get("message", "")).strip()

    @property
    def ok(self) -> bool:
        return bool(self.payload.get("status")) is True

    @property
    def response_code(self):
        return self.payload.get("responsecode")

    def limit_hint(self) -> tuple[str, Decimal, Decimal] | None:
        found = LIMIT_MESSAGE.search(self.message)
        if not found:
            return None
        return (found.group(1).upper(), money(found.group(2)), money(found.group(3)))

    def __repr__(self) -> str:
        return f"<PayoutResult http={self.status} ok={self.ok} {self.message!r}>"


class MakePayoutModal(BasePage):
    def __init__(self, page):
        super().__init__(page)
        self._responses: list[tuple[int, dict | None, str]] = []
        self._attach()

    def _attach(self) -> None:
        def on_response(response):
            url = response.url
            if PAYOUT_ENDPOINT in url or SEND_OTP_ENDPOINT in url:
                try:
                    payload = response.json()
                except Exception:
                    payload = None
                tag = PAYOUT_ENDPOINT if PAYOUT_ENDPOINT in url else SEND_OTP_ENDPOINT
                self._responses.append((response.status, payload, tag))

        self.page.on("response", on_response)

    # ---- structure -------------------------------------------------------
    @property
    def panel(self):
        return self.page.locator(".cdk-overlay-pane").first

    def visible(self, timeout: int = 15_000) -> bool:
        return self.is_visible(self.panel, timeout=timeout)

    def text(self) -> str:
        return re.sub(r"\s+", " ", self.panel.inner_text()).strip()

    def field(self, control: str):
        return self.panel.locator(f"[formcontrolname='{control}']").first

    @property
    def payment_type_select(self):
        return self.field("paymentType")

    @property
    def mode_select(self):
        return self.field("mode")

    @property
    def amount_input(self):
        return self.field("Amount")          # capital A, as the app declares it

    @property
    def remarks_input(self):
        return self.field("remarks")

    @property
    def file_input(self):
        return self.panel.locator("input[type='file']").first

    @property
    def make_payout_button(self):
        return self.panel.get_by_role(
            "button", name=re.compile(r"make\s*payout", re.I)).first

    @property
    def verify_pay_button(self):
        return self.panel.get_by_role(
            "button", name=re.compile(r"verify\s*&?\s*pay", re.I)).first

    @property
    def back_to_details_link(self):
        return self.panel.get_by_text(re.compile(r"back to details", re.I)).first

    @property
    def otp_boxes(self):
        return self.panel.locator("input[maxlength='1']")

    @property
    def close_button(self):
        return self.panel.locator("button", has_text="✕").first

    # ---- reads -----------------------------------------------------------
    def available_modes(self) -> list[str]:
        options = [o.strip() for o in self.mode_select.locator("option").all_inner_texts()]
        return [o for o in options if not o.lower().startswith("select")]

    def available_payment_types(self) -> list[str]:
        options = [o.strip() for o in
                   self.payment_type_select.locator("option").all_inner_texts()]
        return [o for o in options if not o.lower().startswith("select")]

    def charge_disclosed(self) -> bool:
        """Does the modal state a fee/charge/total anywhere? (DEF-OPS-03)"""
        return bool(re.search(r"charge|fee|gst|total\s*deb|you will be charged",
                              self.text(), re.I))

    def on_verify_step(self) -> bool:
        return self.otp_boxes.count() >= OTP_LENGTH

    def resend_text(self) -> str:
        found = re.search(r"resend code(?: in (\d+)\s*s)?", self.text(), re.I)
        return found.group(0) if found else ""

    # ---- actions ---------------------------------------------------------
    def fill_details(self, *, mode: str, amount: str | Decimal,
                     remarks: str = "", payment_type: str = PAYMENT_TYPE_BANK_TRANSFER,
                     document: str | None = None) -> None:
        if document:
            self.file_input.set_input_files(document)
            self.page.wait_for_timeout(1_500)
        self.payment_type_select.select_option(label=payment_type)
        self.page.wait_for_timeout(600)
        self.mode_select.select_option(label=mode)
        self.page.wait_for_timeout(600)
        self.amount_input.fill(str(amount))
        if remarks:
            self.remarks_input.fill(remarks)
        self.page.wait_for_timeout(800)

    def request_otp(self) -> None:
        """Advance Details -> Verify. Fires send-payout-otp."""
        self.click(self.make_payout_button)
        self.page.wait_for_timeout(5_000)

    def enter_otp(self, otp: str) -> None:
        boxes = self.otp_boxes
        boxes.first.wait_for(state="visible", timeout=20_000)
        for index, digit in enumerate(otp[:OTP_LENGTH]):
            boxes.nth(index).fill("")
            boxes.nth(index).fill(digit)
        self.page.wait_for_timeout(900)

    def confirm(self) -> PayoutResult:
        """Submit the OTP and return the real outcome (body, not status)."""
        before = len(self._responses)
        self.click(self.verify_pay_button)
        self.page.wait_for_timeout(10_000)
        for status, payload, tag in self._responses[before:]:
            if tag == PAYOUT_ENDPOINT:
                return PayoutResult(status, payload, self.text())
        return PayoutResult(0, None, self.text())

    def back_to_details(self) -> None:
        self.click(self.back_to_details_link)
        self.page.wait_for_timeout(2_000)

    def otp_was_sent(self) -> bool:
        return any(tag == SEND_OTP_ENDPOINT and (payload or {}).get("status")
                   for _, payload, tag in self._responses)

    def payout_attempts(self) -> list[PayoutResult]:
        return [PayoutResult(s, p, "") for s, p, tag in self._responses
                if tag == PAYOUT_ENDPOINT]

    def close(self) -> None:
        if self.close_button.count():
            try:
                self.close_button.click()
                self.page.wait_for_timeout(1_200)
            except Exception:
                pass


class PayoutPage(BasePage):
    """The Payout tab: beneficiaries with a per-row Payout action."""

    @property
    def bulk_payout_button(self):
        return self.page.get_by_role(
            "button", name=re.compile(r"bulk\s*payout", re.I)).first

    @property
    def payout_batches_button(self):
        return self.page.get_by_role(
            "button", name=re.compile(r"payout\s*batches", re.I)).first

    def rows(self) -> list[list[str]]:
        self.wait_for_loader()
        return self.page.eval_on_selector_all(
            "table tbody tr",
            "rs => rs.map(r => [...r.querySelectorAll('td')]"
            ".map(c => c.innerText.replace(/\\s+/g,' ').trim()))")

    def payable_names(self) -> list[str]:
        """Beneficiaries that actually offer a Payout action."""
        return [r[0] for r in self.rows()
                if len(r) > 5 and re.search(r"payout", r[5], re.I)]

    def blocked_names(self) -> list[str]:
        """Rows with no action - Deactive beneficiaries cannot be paid."""
        return [r[0] for r in self.rows()
                if len(r) > 5 and not r[5].strip()]

    def open_payout(self, beneficiary: str) -> MakePayoutModal:
        row = self.page.locator("table tbody tr").filter(has_text=beneficiary).first
        row.wait_for(state="visible", timeout=20_000)
        row.get_by_text(re.compile(r"^\s*payout\s*$", re.I)).first.click()
        modal = MakePayoutModal(self.page)
        modal.visible()
        return modal

    def has_payout_action(self, beneficiary: str) -> bool:
        row = self.page.locator("table tbody tr").filter(has_text=beneficiary).first
        if not row.count():
            return False
        return row.get_by_text(re.compile(r"^\s*payout\s*$", re.I)).count() > 0
