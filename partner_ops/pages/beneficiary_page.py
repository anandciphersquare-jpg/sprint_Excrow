"""Beneficiary list, Add Beneficiary modal, verification and bulk entry points.

Calibrated 14 Aug 2026 against `#/escrow-detail/beneficiary`.

List columns: `Bene Name | Bank name | Account Number | IFSC Code | Status |
Action`. Status is **Active** or **Deactive** (not 'Inactive'), and the Action
cell carries either *View* or *Verify Beneficiary* depending on state. Page
buttons: *Add Beneficiary*, *Bulk Beneficiary*, *Beneficiary Batches*, plus the
usual Submit/Clear filter pair with a status select.

The Add Beneficiary modal is a `.cdk-overlay-pane`. Its controls, with the real
`formcontrolname` values and the portal's own placeholders:

| Field | control | required | limit / hint |
| --- | --- | --- | --- |
| Display name | `displayName` | yes | up to 100 chars |
| Name on bank account | `name` | yes | up to 100 chars |
| Mobile number | `mobile` | yes | maxlength 10 |
| Email | `email` | yes | - |
| Bank | `bankName` | yes | must be picked from the type-ahead |
| IFSC code | `ifsc` | yes | maxlength 11 |
| Branch | `branchName` | yes | - |
| Account number | `accountNumber` | yes | maxlength 18, 9-18 digits |
| Pincode | `pincode` | yes | maxlength 6 |
| City / State | `city` / `state` | no | auto-filled from pincode |
| Billing address / name | `billingAddress` / `billingName` | no | - |
| Notes | `remarks` | no | - |

**The submit button is intentionally always enabled.** Clicking it with an
invalid form performs no network call, keeps the modal open and reveals every
message at once. That is a guarded-submit pattern, not a defect, and
`test_beneficiary` asserts exactly that (no request fired + messages shown)
rather than asserting a disabled button.

Observed messages, asserted verbatim by the negative tests:
`Enter a valid 10-digit mobile number`, `Enter a valid 11-character IFSC`,
`Enter 9 to 18 digits`, `Enter a valid email`, `Enter a valid 6-digit pincode`,
`Required, up to 100 characters`, `Pick a bank from the list`, `Required`.
"""
from __future__ import annotations

import re

from core.base_page import BasePage

BENEFICIARY_ENDPOINTS = re.compile(r"beneficiary", re.I)

STATUS_ACTIVE = "Active"
STATUS_DEACTIVE = "Deactive"

FIELDS = ("displayName", "name", "mobile", "email", "bankName", "ifsc",
          "branchName", "accountNumber", "pincode", "city", "state",
          "billingAddress", "billingName", "remarks")

MANDATORY = ("displayName", "name", "mobile", "email", "bankName", "ifsc",
             "branchName", "accountNumber", "pincode")


class BeneficiaryRow:
    def __init__(self, cells: list[str]):
        self.cells = cells

    def _cell(self, i: int) -> str:
        return self.cells[i] if i < len(self.cells) else ""

    name = property(lambda self: self._cell(0))
    bank = property(lambda self: self._cell(1))
    account = property(lambda self: self._cell(2))
    ifsc = property(lambda self: self._cell(3))
    status = property(lambda self: self._cell(4))
    action = property(lambda self: self._cell(5))

    @property
    def is_active(self) -> bool:
        return self.status.strip().lower() == "active"

    @property
    def is_payable(self) -> bool:
        """A Deactive beneficiary renders no action link - it cannot be paid."""
        return bool(self.action.strip())

    def __repr__(self) -> str:
        return f"<Beneficiary {self.name!r} {self.account} {self.status}>"


class AddBeneficiaryModal(BasePage):
    @property
    def panel(self):
        return self.page.locator(".cdk-overlay-pane").last

    def field(self, control: str):
        return self.panel.locator(f"[formcontrolname='{control}']").first

    @property
    def submit_button(self):
        return self.panel.get_by_role(
            "button", name=re.compile(r"add\s*beneficiary", re.I)).first

    @property
    def cancel_button(self):
        return self.panel.get_by_role(
            "button", name=re.compile(r"^\s*cancel\s*$", re.I)).first

    def visible(self, timeout: int = 15_000) -> bool:
        return self.is_visible(self.panel, timeout=timeout)

    def fill_field(self, control: str, value: str) -> None:
        locator = self.field(control)
        locator.fill("")
        locator.fill(value)

    def fill_form(self, data: dict[str, str]) -> None:
        for control, value in data.items():
            if value is None:
                continue
            self.fill_field(control, str(value))

    def pick_bank(self, query: str) -> str:
        """Bank is a type-ahead; a typed string alone leaves it invalid.

        Returns the option text chosen, or '' if the list produced nothing -
        callers must treat an empty return as 'no such bank', not as success.
        """
        box = self.field("bankName")
        box.fill("")
        box.click()
        box.press_sequentially(query, delay=60)
        self.page.wait_for_timeout(1_800)
        options = self.page.locator(
            ".cdk-overlay-container li, .cdk-overlay-container .dropdown-item, "
            ".ng-dropdown-panel .ng-option")
        if not options.count():
            return ""
        chosen = (options.first.text_content() or "").strip()
        options.first.click()
        self.page.wait_for_timeout(600)
        return chosen

    def message_for(self, control: str) -> str:
        """Label + validation text rendered around one control."""
        return re.sub(r"\s+", " ", self.form_group_text(self.field(control))).strip()

    def messages(self) -> dict[str, str]:
        return {control: self.message_for(control) for control in MANDATORY}

    def invalid_controls(self) -> list[str]:
        return self.panel.evaluate(
            """p => [...p.querySelectorAll('.ng-invalid[formcontrolname]')]
                     .map(e => e.getAttribute('formcontrolname'))""")

    def submit(self) -> None:
        self.click(self.submit_button)
        self.page.wait_for_timeout(3_000)

    def cancel(self) -> None:
        if self.cancel_button.count():
            self.cancel_button.click()
            self.page.wait_for_timeout(1_200)

    def panel_text(self) -> str:
        return re.sub(r"\s+", " ", self.panel.inner_text()).strip()


class BeneficiaryPage(BasePage):
    """The Beneficiary tab of an escrow."""

    @property
    def add_button(self):
        return self.page.get_by_role(
            "button", name=re.compile(r"add\s*beneficiary", re.I)).first

    @property
    def bulk_button(self):
        return self.page.get_by_role(
            "button", name=re.compile(r"bulk\s*beneficiary", re.I)).first

    @property
    def batches_button(self):
        return self.page.get_by_role(
            "button", name=re.compile(r"beneficiary\s*batches", re.I)).first

    @property
    def status_filter(self):
        return self.page.locator("[formcontrolname='status']").first

    def rows(self) -> list[BeneficiaryRow]:
        self.wait_for_loader()
        raw = self.page.eval_on_selector_all(
            "table tbody tr",
            "rs => rs.map(r => [...r.querySelectorAll('td')]"
            ".map(c => c.innerText.replace(/\\s+/g,' ').trim()))")
        return [BeneficiaryRow(cells) for cells in raw if cells and len(cells) > 3]

    def find(self, needle: str) -> BeneficiaryRow | None:
        for row in self.rows():
            if needle.lower() in " ".join(row.cells).lower():
                return row
        return None

    def active_rows(self) -> list[BeneficiaryRow]:
        return [r for r in self.rows() if r.is_active]

    def deactive_rows(self) -> list[BeneficiaryRow]:
        return [r for r in self.rows() if not r.is_active]

    def open_add_modal(self) -> AddBeneficiaryModal:
        self.click(self.add_button)
        modal = AddBeneficiaryModal(self.page)
        modal.visible()
        return modal

    def verify_beneficiary(self, needle: str) -> None:
        row = self.page.locator("table tbody tr").filter(has_text=needle).first
        row.get_by_text(re.compile(r"verify\s*beneficiary", re.I)).first.click()
        self.wait_for_loader()
        self.page.wait_for_timeout(3_000)

    def open_view(self, needle: str) -> None:
        row = self.page.locator("table tbody tr").filter(has_text=needle).first
        row.get_by_text(re.compile(r"^\s*view\s*$", re.I)).first.click()
        self.page.wait_for_timeout(2_500)

    def capture_beneficiary_calls(self) -> list[str]:
        """Record beneficiary API traffic, to prove a guarded submit is silent."""
        seen: list[str] = []

        def on_request(request):
            if BENEFICIARY_ENDPOINTS.search(request.url) and request.method == "POST":
                seen.append(request.url.rsplit("/", 1)[-1])

        self.page.on("request", on_request)
        return seen
