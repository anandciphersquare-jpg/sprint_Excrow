"""Reports: Payout, VID Collection, QR Statement, Wallet Statement, Bulk Bene.

Calibrated 14 Aug 2026. `#/reports-statement` redirects to
`#/reports-statement/payout`; the five tabs are `a.nav-link` and the wallet tab
is directly addressable at `#/reports-statement/wallet`.

**Payout report** is a table:
`DATE & TIME | ESCROW NAME | TXN ID | BENE NAME | AMOUNT | TOTAL CHARGES | UTR |
STATUS | ACTION`, where TOTAL CHARGES is the gross debit with its own breakdown
underneath ("₹14.75  Charges ₹12.50 + GST ₹2.25"). Statuses seen live:
**Success**, **Failure**, **Processed**, **Pending**. Filter offers All Status /
Success / Pending / Failed, plus Export.

**Wallet Statement** is *not* a table - it is a card timeline, so `querySelector
('table')` returns nothing and a table-based reader silently sees zero rows.
Each entry reads:

    14-08-2026 1:17:26 PM
    Va Creation
    TXN VA123620260814011726 · Escrow EXC-0726-212489
    <narration>
    −₹14.16
    Charges ₹12.00 + GST ₹2.16
    Bal ₹151.24

The **running `Bal` per entry** is what makes real reconciliation possible: the
ledger's internal-consistency check chains those figures instead of trusting a
single headline. The header shows `WALLET BALANCE ₹151.24` with Credit/Debit
filters. Debits are prefixed with U+2212 ('−'), not a hyphen - matching on '-'
alone misses every debit and silently treats them as credits.

Entry kinds observed: `Imps`, `Va Creation`, `Bank Charges`, `Tcp Charges`,
`Manual Funding`. Note `Tcp` here vs `TSP Charges` on the escrow's Charges tab
(DEF-OPS-07).
"""
from __future__ import annotations

import re
from decimal import Decimal

from config.settings import settings
from core.base_page import BasePage
from partner_ops.data.pricing import money
from partner_ops.ledger import CREDIT, DEBIT, StatementEntry

REPORTS_ROUTE = "#/reports-statement"
WALLET_ROUTE = "#/reports-statement/wallet"
PAYOUT_ROUTE = "#/reports-statement/payout"

TAB_PAYOUT = "Payout"
TAB_VID_COLLECTION = "VID Collection"
TAB_QR_STATEMENT = "QR Statement"
TAB_WALLET_STATEMENT = "Wallet Statement"
TAB_BULK_BENE = "Bulk Bene Report"

STATUS_SUCCESS = "Success"
STATUS_FAILURE = "Failure"
STATUS_PROCESSED = "Processed"
STATUS_PENDING = "Pending"

# A debit carries U+2212. Keep both forms so a font/locale change cannot
# silently reclassify every debit as a credit.
_MINUS = "[−\\-]"

_ENTRY_START = re.compile(
    r"^\d{2}-\d{2}-\d{4}\s+\d{1,2}:\d{2}:\d{2}\s*(?:AM|PM)?$", re.I)
_AMOUNT = re.compile(rf"^({_MINUS}|\+)?\s*₹\s*([\d,]+\.\d{{2}})$")
_BREAKDOWN = re.compile(
    r"charges?\s*₹\s*([\d,]+\.\d{2})\s*\+\s*GST\s*₹\s*([\d,]+\.\d{2})", re.I)
_BALANCE = re.compile(r"^Bal\s*₹\s*([\d,]+\.\d{2})$", re.I)
_TXN = re.compile(r"TXN\s+(\S+)(?:\s*·\s*Escrow\s+(\S+))?", re.I)
_HEADER_BALANCE = re.compile(r"WALLET BALANCE\s*₹\s*([\d,]+\.\d{2})", re.I)


class PayoutReportRow:
    def __init__(self, cells: list[str]):
        self.cells = cells

    def _cell(self, i: int) -> str:
        return self.cells[i] if i < len(self.cells) else ""

    stamp = property(lambda self: self._cell(0))
    escrow_name = property(lambda self: self._cell(1))
    txn_id = property(lambda self: self._cell(2))
    beneficiary = property(lambda self: self._cell(3))
    status = property(lambda self: self._cell(7))

    @property
    def amount(self) -> Decimal:
        return money(self._cell(4))

    @property
    def total_charges(self) -> Decimal:
        """The gross charge figure ('₹14.75 Charges ₹12.50 + GST ₹2.25')."""
        found = re.search(r"₹\s*([\d,]+\.\d{2})", self._cell(5))
        return money(found.group(1)) if found else Decimal("0.00")

    @property
    def net_charge(self) -> Decimal | None:
        found = _BREAKDOWN.search(self._cell(5))
        return money(found.group(1)) if found else None

    @property
    def gst(self) -> Decimal | None:
        found = _BREAKDOWN.search(self._cell(5))
        return money(found.group(2)) if found else None

    @property
    def utr(self) -> str:
        return self._cell(6)

    @property
    def has_utr(self) -> bool:
        return bool(self.utr) and self.utr.strip().upper() not in ("NA", "-", "—")

    @property
    def is_success(self) -> bool:
        return self.status.strip().lower() == "success"

    @property
    def is_failure(self) -> bool:
        return self.status.strip().lower() in ("failure", "failed")

    def __repr__(self) -> str:
        return (f"<PayoutRow {self.txn_id} {self.beneficiary!r} ₹{self.amount} "
                f"charge ₹{self.total_charges} {self.status}>")


class ReportsPage(BasePage):
    def _goto(self, route: str) -> None:
        base = settings.partner_base_url.split("#")[0]
        self.goto(base + route)
        self.wait_for_loader()
        self.page.wait_for_timeout(3_500)

    def open(self) -> None:
        self._goto(REPORTS_ROUTE)

    def open_payout(self) -> None:
        self._goto(PAYOUT_ROUTE)
        self.page.locator("table tbody tr").first.wait_for(state="visible",
                                                           timeout=30_000)

    def open_wallet_statement(self) -> None:
        self._goto(WALLET_ROUTE)
        self.page.get_by_text(re.compile(r"wallet balance", re.I)).first.wait_for(
            state="visible", timeout=30_000)

    def tabs(self) -> list[str]:
        return [t.strip() for t in self.page.locator("a.nav-link").all_inner_texts()]

    def open_tab(self, name: str) -> None:
        for index, label in enumerate(self.tabs()):
            if label.lower() == name.lower():
                self.page.locator("a.nav-link").nth(index).click()
                self.wait_for_loader()
                self.page.wait_for_timeout(3_000)
                return
        raise AssertionError(f"Report tab {name!r} not found in {self.tabs()}")

    # ---- filters ---------------------------------------------------------
    @property
    def status_filter(self):
        return self.page.locator("select").first

    @property
    def date_range_input(self):
        return self.page.get_by_placeholder(re.compile(r"date range", re.I)).first

    @property
    def submit_button(self):
        return self.page.get_by_role("button", name=re.compile(r"^\s*submit\s*$", re.I)).first

    @property
    def clear_button(self):
        return self.page.get_by_role("button", name=re.compile(r"^\s*clear\s*$", re.I)).first

    @property
    def export_button(self):
        return self.page.get_by_role("button", name=re.compile(r"^\s*export\s*$", re.I)).first

    def status_options(self) -> list[str]:
        return [o.strip() for o in self.status_filter.locator("option").all_inner_texts()]

    def filter_by_status(self, status: str) -> None:
        self.status_filter.select_option(label=status)
        self.click(self.submit_button)
        self.wait_for_loader()
        self.page.wait_for_timeout(3_000)

    # ---- payout report ---------------------------------------------------
    def payout_rows(self) -> list[PayoutReportRow]:
        raw = self.page.eval_on_selector_all(
            "table tbody tr",
            "rs => rs.map(r => [...r.querySelectorAll('td')]"
            ".map(c => c.innerText.replace(/\\s+/g,' ').trim()))")
        return [PayoutReportRow(cells) for cells in raw if cells and len(cells) > 6]

    def find_payout(self, txn_id: str) -> PayoutReportRow | None:
        for row in self.payout_rows():
            if txn_id and txn_id in row.txn_id:
                return row
        return None

    # ---- wallet statement ------------------------------------------------
    def wallet_balance(self) -> Decimal:
        found = _HEADER_BALANCE.search(self.body_text())
        if not found:
            raise AssertionError(
                "Wallet Statement did not render a 'WALLET BALANCE' figure.")
        return money(found.group(1))

    def statement_entries(self, limit: int = 40) -> list[StatementEntry]:
        """Parse the card timeline into structured entries, newest first."""
        lines = [re.sub(r"[ \t]+", " ", line).strip()
                 for line in self.body_text().split("\n")]
        lines = [line for line in lines if line]

        entries: list[StatementEntry] = []
        current: StatementEntry | None = None
        narration: list[str] = []

        for line in lines:
            if _ENTRY_START.match(line):
                if current is not None:
                    current.narration = " ".join(narration).strip()
                    entries.append(current)
                current = StatementEntry(stamp=line)
                narration = []
                continue
            if current is None:
                continue

            amount = _AMOUNT.match(line)
            if amount:
                current.direction = CREDIT if amount.group(1) == "+" else DEBIT
                current.amount = money(amount.group(2))
                continue

            breakdown = _BREAKDOWN.search(line)
            if breakdown:
                current.net_charge = money(breakdown.group(1))
                current.gst = money(breakdown.group(2))
                continue

            balance = _BALANCE.match(line)
            if balance:
                current.balance_after = money(balance.group(1))
                continue

            txn = _TXN.search(line)
            if txn:
                current.txn_id = txn.group(1)
                current.escrow_id = txn.group(2) or ""
                continue

            if not current.kind:
                current.kind = line
            else:
                narration.append(line)

        if current is not None:
            current.narration = " ".join(narration).strip()
            entries.append(current)

        # Keep only fully-formed entries: a partial parse must not masquerade
        # as a zero-value movement inside a reconciliation.
        good = [e for e in entries if e.balance_after and e.amount]
        return good[:limit]

    def find_statement_entry(self, txn_id: str) -> StatementEntry | None:
        for entry in self.statement_entries():
            if txn_id and txn_id in entry.txn_id:
                return entry
        return None

    # ---- other tabs ------------------------------------------------------
    def table_rows(self) -> list[list[str]]:
        return self.page.eval_on_selector_all(
            "table tbody tr",
            "rs => rs.map(r => [...r.querySelectorAll('td')]"
            ".map(c => c.innerText.replace(/\\s+/g,' ').trim()))")

    def table_headings(self) -> list[str]:
        return [h.strip() for h in
                self.page.locator("table thead th").all_inner_texts()]
