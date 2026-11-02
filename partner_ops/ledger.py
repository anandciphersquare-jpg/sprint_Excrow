"""Wallet reconciliation: the ledger every financial test is judged against.

The rule this suite exists to enforce: **a success message is not a pass.** A
financial step is only green when the transaction record, the report entry and
the wallet movement all agree, and the closing balance is arithmetically what
the configured pricing says it should be.

Three independent checks are implemented, because they fail independently:

1. `check_internal_consistency` - the statement's own running balances must
   chain. Entry *n*'s printed balance minus entry *n*'s signed amount must
   equal entry *n+1*'s printed balance. This catches a ledger that renders
   plausible rows over a broken balance column, which no amount of comparing
   the dashboard to the statement would reveal.
2. `check_expected_move` - one activity: opening, expected debit from the
   escrow's own pricing, actual closing. This is where a wrong charge, a
   missing refund or a double debit surfaces.
3. `check_dashboard_matches_statement` - the invariant from PART 11/23.
   Note the dashboard stat is served from a cache and is stale until the card's
   refresh is used (DEF-OPS-05), so callers must refresh before comparing or
   they will chase a phantom.

Every failure message carries the numbers, because "balances did not match" is
useless in a defect report and this suite's output is meant to be pasteable
straight into one.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from partner_ops.data.pricing import gst_on, money

# The statement prints a debit as '−₹14.75' (U+2212) and a credit as '+₹500.00'.
DEBIT = "debit"
CREDIT = "credit"


@dataclass
class StatementEntry:
    """One row of Reports -> Wallet Statement."""
    stamp: str = ""
    kind: str = ""                     # 'Imps', 'Va Creation', 'Bank Charges'...
    txn_id: str = ""
    escrow_id: str = ""
    narration: str = ""
    direction: str = DEBIT
    amount: Decimal = Decimal("0.00")          # always positive
    net_charge: Decimal | None = None
    gst: Decimal | None = None
    balance_after: Decimal = Decimal("0.00")   # the printed 'Bal ₹x'

    @property
    def signed(self) -> Decimal:
        return -self.amount if self.direction == DEBIT else self.amount

    def __str__(self) -> str:
        sign = "-" if self.direction == DEBIT else "+"
        return (f"{self.stamp} {self.kind} {self.txn_id} "
                f"{sign}₹{self.amount} -> bal ₹{self.balance_after}")


@dataclass
class LedgerRow:
    """One row of the PART 23 end-to-end reconciliation table."""
    activity: str
    opening: Decimal
    transaction: Decimal = Decimal("0.00")
    charge: Decimal = Decimal("0.00")
    refund: Decimal = Decimal("0.00")
    closing: Decimal = Decimal("0.00")
    note: str = ""

    def as_markdown(self) -> str:
        return (f"| {self.activity} | {self.opening} | {self.transaction} | "
                f"{self.charge} | {self.refund} | {self.closing} | {self.note} |")


class ReconciliationError(AssertionError):
    """Raised with the full arithmetic, ready to paste into a defect."""


@dataclass
class WalletLedger:
    """Accumulates activities across a run and reports the whole table."""
    rows: list[LedgerRow] = field(default_factory=list)

    def record(self, activity: str, opening: Decimal, closing: Decimal, *,
               transaction: Decimal = Decimal("0.00"),
               charge: Decimal = Decimal("0.00"),
               refund: Decimal = Decimal("0.00"), note: str = "") -> LedgerRow:
        row = LedgerRow(activity=activity, opening=opening, closing=closing,
                        transaction=transaction, charge=charge, refund=refund,
                        note=note)
        self.rows.append(row)
        return row

    def as_markdown(self) -> str:
        head = ("| Activity | Opening | Transaction | Charge | Refund | Closing | Note |\n"
                "| --- | ---: | ---: | ---: | ---: | ---: | --- |")
        return "\n".join([head] + [r.as_markdown() for r in self.rows])


# --------------------------------------------------------------------------
# checks
# --------------------------------------------------------------------------
def check_expected_move(*, activity: str, opening: Decimal, closing: Decimal,
                        expected_debit: Decimal = Decimal("0.00"),
                        expected_credit: Decimal = Decimal("0.00"),
                        tolerance: Decimal = Decimal("0.00")) -> None:
    """Assert one activity moved the wallet by exactly the expected amount."""
    expected_closing = opening - expected_debit + expected_credit
    drift = (closing - expected_closing).copy_abs()
    if drift > tolerance:
        raise ReconciliationError(
            f"{activity}: wallet moved by the wrong amount.\n"
            f"  opening balance        ₹{opening}\n"
            f"  expected debit         ₹{expected_debit}\n"
            f"  expected credit/refund ₹{expected_credit}\n"
            f"  expected closing       ₹{expected_closing}\n"
            f"  actual closing         ₹{closing}\n"
            f"  discrepancy            ₹{drift}\n"
            f"  actual movement        ₹{closing - opening}"
        )


def check_internal_consistency(entries: list[StatementEntry],
                              limit: int | None = None) -> None:
    """The statement's own running-balance column must chain row to row.

    Entries arrive newest-first, as rendered. For adjacent rows the older row's
    printed balance plus the newer row's signed amount must equal the newer
    row's printed balance.
    """
    window = entries[:limit] if limit else entries
    problems: list[str] = []
    for newer, older in zip(window, window[1:]):
        expected = older.balance_after + newer.signed
        if (expected - newer.balance_after).copy_abs() > Decimal("0.00"):
            problems.append(
                f"  {newer.stamp} {newer.kind} {newer.txn_id}: "
                f"previous bal ₹{older.balance_after} {newer.signed:+} "
                f"= ₹{expected}, but the row prints ₹{newer.balance_after}"
            )
    if problems:
        raise ReconciliationError(
            "Wallet statement running balance does not chain:\n"
            + "\n".join(problems)
        )


def check_gst_breakdown(entries: list[StatementEntry],
                        limit: int | None = None) -> None:
    """Where a row spells out 'Charges X + GST Y', the arithmetic must hold."""
    window = entries[:limit] if limit else entries
    problems: list[str] = []
    for entry in window:
        if entry.net_charge is None or entry.gst is None:
            continue
        expected_gst = gst_on(entry.net_charge)
        if (entry.gst - expected_gst).copy_abs() > Decimal("0.01"):
            problems.append(
                f"  {entry.txn_id}: charge ₹{entry.net_charge} implies GST "
                f"₹{expected_gst} at 18%, row shows ₹{entry.gst}")
        total = entry.net_charge + entry.gst
        if (total - entry.amount).copy_abs() > Decimal("0.01"):
            problems.append(
                f"  {entry.txn_id}: charge ₹{entry.net_charge} + GST ₹{entry.gst}"
                f" = ₹{total}, but the debit is ₹{entry.amount}")
    if problems:
        raise ReconciliationError("GST breakdown inconsistent:\n" + "\n".join(problems))


def check_dashboard_matches_statement(dashboard: Decimal,
                                      statement: Decimal) -> None:
    """PART 11 / PART 23 invariant. Refresh the dashboard card first."""
    if dashboard != statement:
        raise ReconciliationError(
            "Dashboard wallet balance != Reports -> Wallet Statement balance.\n"
            f"  dashboard  ₹{dashboard}\n"
            f"  statement  ₹{statement}\n"
            f"  difference ₹{(dashboard - statement).copy_abs()}\n"
            "  If the dashboard was not refreshed first, this is DEF-OPS-05 "
            "(stale cached stat) rather than a ledger fault - refresh the "
            "Wallet Balance card and re-read before filing."
        )


def find_entry(entries: list[StatementEntry], *, txn_id: str = "",
               kind: str = "") -> StatementEntry | None:
    for entry in entries:
        if txn_id and txn_id in entry.txn_id:
            return entry
        if kind and kind.lower() == entry.kind.lower():
            return entry
    return None


def refund_entries_for(entries: list[StatementEntry],
                       txn_id: str) -> list[StatementEntry]:
    """Credits that reference a transaction - i.e. a reversal of its charge."""
    return [e for e in entries
            if e.direction == CREDIT
            and (txn_id in e.txn_id or txn_id in e.narration)]


def total_debits(entries: list[StatementEntry]) -> Decimal:
    return money(sum((e.amount for e in entries if e.direction == DEBIT),
                     Decimal("0.00")))


def total_credits(entries: list[StatementEntry]) -> Decimal:
    return money(sum((e.amount for e in entries if e.direction == CREDIT),
                     Decimal("0.00")))
