"""Virtual ID (virtual account): list, Create Account, payout structure.

Calibrated 14 Aug 2026 against `#/escrow-detail/virtual-account`, including a
real VID created end to end (charge verified against the wallet).

List columns: `Created At | Prefix | VID No. | Name | Email | Phone | Charges |
PAN | Pincode | Min Limit | Max Limit | Status | Action`. Note the list prints
the **gross** creation charge (₹14.16 = ₹12.00 + 18% GST) while the escrow's
Charges tab quotes the net figure - reconciling one against the other without
accounting for GST is the classic false failure here.

`+ Create Account` opens a `.cdk-overlay-pane` titled *Create Virtual Account*:

| Field | control | limit |
| --- | --- | --- |
| Select Prefix | `prefixfeild` | select, populated per escrow (sic - the app misspells 'field') |
| VID number | `acc_no` | maxlength 18 |
| Merchant Name | `customername` | - |
| Merchant Email | `email` | - |
| Merchant Mobile | `phone` | maxlength 10 |
| Merchant PAN | `pan` | maxlength 10 |
| Pincode | `pincode` | maxlength 6 |
| Minimum Limit | `min_limit` | number |
| Maximum Limit | `max_limit` | number |
| Accept payments from | `paymentType` | radio, 'All Account' |

Creation posts `va/create-va` after up to four `va/check-duplicate` probes, and
succeeds with an alert *"Virtual ID Created successfully!"* + *Okay*.

**Precondition that blocks the whole module on most escrows:** an escrow with no
configured prefix answers `+ Create Account` with a blocking alert **"No Prefix
found! Please contact to administration"**. That is a data/provisioning
precondition, not a bug, and `prefix_available()` lets tests skip with that
reason instead of failing obscurely.

Payout Structure / Pay Now (PARTS 17-20) hang off a VID. On the escrows reachable
in this environment the list's Action cell renders empty and no split editor was
exposed, so `open_vid_details` / `payout_structure_available` report what they
find and the tests skip with the endpoint names to look for
(`merchant/split`, `merchant/virtual-account/<id>/split`) rather than driving
locators that were never verified.
"""
from __future__ import annotations

import random
import re
from decimal import Decimal

from core.base_page import BasePage
from partner_ops.data.pricing import money

CREATE_VA_ENDPOINT = "va/create-va"
CHECK_DUPLICATE_ENDPOINT = "va/check-duplicate"
SPLIT_ENDPOINTS = ("merchant/split", "/split")

NO_PREFIX_MESSAGE = re.compile(r"no prefix found", re.I)
CREATED_MESSAGE = re.compile(r"virtual id created successfully", re.I)

# PART 17's matrix. Recorded so the test can report which combinations the UI
# actually offers rather than assuming all six are supported.
PAYMENT_MODES = ("Automatic", "Instant", "Manual")
SPLIT_TYPES = ("Percentage", "Fixed")


class VirtualIdRow:
    def __init__(self, cells: list[str]):
        self.cells = cells

    def _cell(self, i: int) -> str:
        return self.cells[i] if i < len(self.cells) else ""

    created = property(lambda self: self._cell(0))
    prefix = property(lambda self: self._cell(1))
    vid = property(lambda self: self._cell(2))
    name = property(lambda self: self._cell(3))
    email = property(lambda self: self._cell(4))
    phone = property(lambda self: self._cell(5))
    charges = property(lambda self: self._cell(6))
    pan = property(lambda self: self._cell(7))
    pincode = property(lambda self: self._cell(8))
    min_limit = property(lambda self: self._cell(9))
    max_limit = property(lambda self: self._cell(10))
    status = property(lambda self: self._cell(11))
    action = property(lambda self: self._cell(12))

    @property
    def charge_amount(self) -> Decimal:
        return money(self.charges)

    def __repr__(self) -> str:
        return f"<VID {self.vid} {self.name!r} {self.status}>"


class CreateVirtualIdModal(BasePage):
    @property
    def panel(self):
        return self.page.locator(".cdk-overlay-pane").last

    def visible(self, timeout: int = 15_000) -> bool:
        return self.is_visible(self.panel, timeout=timeout)

    def field(self, control: str):
        return self.panel.locator(f"[formcontrolname='{control}']").first

    @property
    def prefix_select(self):
        return self.field("prefixfeild")

    @property
    def submit_button(self):
        return self.panel.get_by_role(
            "button", name=re.compile(r"^\s*submit\s*$", re.I)).first

    def prefixes(self) -> list[str]:
        options = [o.strip() for o in self.prefix_select.locator("option").all_inner_texts()]
        return [o for o in options if not o.lower().startswith("select")]

    def fill_form(self, data: dict[str, str]) -> None:
        prefix = data.get("prefixfeild")
        if prefix:
            self.prefix_select.select_option(label=prefix)
            self.page.wait_for_timeout(600)
        for control, value in data.items():
            if control == "prefixfeild" or value is None:
                continue
            locator = self.field(control)
            locator.fill("")
            locator.fill(str(value))
        self.page.wait_for_timeout(1_000)

    def message_for(self, control: str) -> str:
        return re.sub(r"\s+", " ", self.form_group_text(self.field(control))).strip()

    def invalid_controls(self) -> list[str]:
        return self.panel.evaluate(
            """p => [...p.querySelectorAll('.ng-invalid[formcontrolname]')]
                     .map(e => e.getAttribute('formcontrolname'))""")

    def submit(self) -> None:
        self.click(self.submit_button)
        self.page.wait_for_timeout(8_000)

    def panel_text(self) -> str:
        return re.sub(r"\s+", " ", self.panel.inner_text()).strip()


class VirtualIdPage(BasePage):
    """The Virtual ID tab of an escrow."""

    def __init__(self, page):
        super().__init__(page)
        self._creates: list[tuple[int, dict | None]] = []

        def on_response(response):
            if CREATE_VA_ENDPOINT in response.url:
                try:
                    self._creates.append((response.status, response.json()))
                except Exception:
                    self._creates.append((response.status, None))

        self.page.on("response", on_response)

    @property
    def create_button(self):
        return self.page.get_by_role(
            "button", name=re.compile(r"create\s*account", re.I)).first

    @property
    def status_filter(self):
        return self.page.locator("[formcontrolname='status']").first

    @property
    def prefix_filter(self):
        return self.page.locator("[formcontrolname='prefix']").first

    def rows(self) -> list[VirtualIdRow]:
        self.wait_for_loader()
        raw = self.page.eval_on_selector_all(
            "table tbody tr",
            "rs => rs.map(r => [...r.querySelectorAll('td')]"
            ".map(c => c.innerText.replace(/\\s+/g,' ').trim()))")
        return [VirtualIdRow(cells) for cells in raw
                if cells and len(cells) > 3 and "no record" not in cells[0].lower()]

    def find(self, needle: str) -> VirtualIdRow | None:
        for row in self.rows():
            if needle.lower() in " ".join(row.cells).lower():
                return row
        return None

    def count(self) -> int:
        return len(self.rows())

    # ---- creation --------------------------------------------------------
    def open_create_modal(self) -> tuple[CreateVirtualIdModal | None, str]:
        """Returns (modal, blocking_alert_text).

        An escrow with no prefix yields (None, 'No Prefix found! ...').
        """
        from partner_ops.pages.escrow_page import AlertDialog
        self.click(self.create_button)
        self.page.wait_for_timeout(4_000)
        alert = AlertDialog(self.page)
        if alert.visible(timeout=2_500):
            text = alert.text()
            if NO_PREFIX_MESSAGE.search(text):
                alert.dismiss()
                return None, text
            alert.dismiss()
            return None, text
        modal = CreateVirtualIdModal(self.page)
        if modal.visible(timeout=8_000):
            return modal, ""
        return None, ""

    def prefix_available(self) -> bool:
        modal, alert = self.open_create_modal()
        if modal is None:
            return False
        available = bool(modal.prefixes())
        self.page.keyboard.press("Escape")
        self.page.wait_for_timeout(1_000)
        return available

    def create_result(self) -> tuple[int, dict | None] | None:
        return self._creates[-1] if self._creates else None

    # ---- payout structure (PARTS 17-20) ----------------------------------
    def open_vid_details(self, vid: str) -> bool:
        """Try to open a VID's detail view. False if the row exposes no link."""
        row = self.page.locator("table tbody tr").filter(has_text=vid).first
        if not row.count():
            return False
        link = row.locator("a, button").filter(
            has_text=re.compile(r"view|detail|pay\s*now|structure|" + re.escape(vid), re.I))
        if not link.count():
            clickable = row.get_by_text(vid, exact=False).first
            if clickable.count():
                try:
                    clickable.click()
                    self.page.wait_for_timeout(4_000)
                    return "virtual-account" not in self.page.url or True
                except Exception:
                    return False
            return False
        link.first.click()
        self.page.wait_for_timeout(4_000)
        return True

    def payout_structure_available(self) -> bool:
        return bool(re.search(r"payout\s*structure|split\s*type", self.body_text(), re.I))

    def pay_now_available(self) -> bool:
        return self.page.get_by_role(
            "button", name=re.compile(r"pay\s*now", re.I)).count() > 0


def unique_vid_suffix(length: int = 8) -> str:
    """A VID number unlikely to collide, kept inside the 18-char maxlength."""
    return "77" + "".join(str(random.randint(0, 9)) for _ in range(length - 2))


def sample_vid_payload(prefix: str) -> dict[str, str]:
    """Valid Create-Virtual-Account data. PAN/pincode shapes match live records."""
    return {
        "prefixfeild": prefix,
        "acc_no": unique_vid_suffix(),
        "customername": "QA Ops Probe",
        "email": "anand.kumar+qaops@paysprint.in",
        "phone": "9876543211",
        "pan": "DQOPD9511Q",
        "pincode": "110091",
        "min_limit": "100",
        "max_limit": "50000",
    }
