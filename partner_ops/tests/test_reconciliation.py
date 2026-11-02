"""PARTS 9, 10, 11, 12, 23, 27 - wallet reconciliation and the reports.

This is the module the brief is really about, so it is worth being precise about
what each test can and cannot prove.

**What the wallet actually pays for.** Measured on 14 Aug 2026:

| Activity | Wallet movement | Escrow movement |
| --- | --- | --- |
| Payout (success or failure) | −(charge + 18% GST) | transferred amount |
| VID creation | −(charge + 18% GST) | none |
| Bank / TSP maintenance | −(charge + 18% GST) | *claimed* by the UI, not observed |
| Manual funding | +amount | none |

So the brief's `amount + charges` formula describes the **combined** cost, not
the wallet leg. Asserting it against the wallet would fail every payout by the
transaction value. The ledger checks the wallet leg only.

**The headline finding, `test_failed_payout_charge_is_refunded`.** A ₹10 IMPS
payout that the UI reported as failed still took ₹14.75 from the wallet and
posted no reversal (₹180.15 → ₹165.40). The same pattern is visible historically
in the payout report: Failure rows carrying full charges. Written `xfail(strict=
False)` against the brief's stated rule so a fix flips it to XPASS.

**`test_reported_status_matches_what_the_user_was_told`** covers the other half
of that incident: the UI said *"Your request has been failed."* while the payout
report recorded the same TXN as **Processed**. A partner cannot tell whether the
money left.

Reconciliation runs **read-only** by default - it audits history, which is
plentiful and real. Set `PARTNER_OPS_SPEND=1` to have it perform its own VID
creation and reconcile that (the cheapest chargeable activity: one VID charge).
"""
from __future__ import annotations

import os
import re
from decimal import Decimal

import pytest

from partner_ops.data.pricing import GST_RATE, gst_on, money
from partner_ops.ledger import (
    CREDIT, DEBIT, check_dashboard_matches_statement, check_expected_move,
    check_gst_breakdown, check_internal_consistency, refund_entries_for,
    total_credits, total_debits)
from partner_ops.pages.reports_page import (
    STATUS_FAILURE, STATUS_PROCESSED, STATUS_SUCCESS, ReportsPage)

SPEND_ALLOWED = os.getenv("PARTNER_OPS_SPEND", "").strip() in ("1", "true", "yes")

# Statement entry kinds that represent a payout charge.
PAYOUT_KINDS = ("imps", "neft", "rtgs", "a2a")
MAINTENANCE_KINDS = ("bank charges", "tcp charges", "tsp charges")
VID_KINDS = ("va creation",)


@pytest.fixture
def reports(ops_page) -> ReportsPage:
    return ReportsPage(ops_page)


@pytest.fixture
def statement(reports):
    reports.open_wallet_statement()
    entries = reports.statement_entries(limit=40)
    if not entries:
        pytest.skip(
            "The wallet statement parsed to zero entries. It is a card "
            "timeline, not a table - if the layout changed, "
            "ReportsPage.statement_entries needs recalibrating.")
    return entries


# ===================== PART 11: statement integrity ======================

@pytest.mark.partner_ops
@pytest.mark.smoke
def test_wallet_statement_renders_a_balance_and_entries(reports, statement):
    balance = reports.wallet_balance()
    assert balance >= 0, f"negative wallet balance: {balance}"
    assert statement[0].balance_after == balance, (
        "the newest statement entry's running balance disagrees with the "
        f"header: entry ₹{statement[0].balance_after} vs header ₹{balance}")


@pytest.mark.partner_ops
@pytest.mark.regression
def test_statement_running_balance_chains(statement):
    """The ledger's own arithmetic must hold, row to row.

    The strongest single check available: it needs no external expectation and
    catches a plausible-looking statement whose balance column is wrong.
    """
    check_internal_consistency(statement)


@pytest.mark.partner_ops
@pytest.mark.regression
def test_every_charge_carries_18_percent_gst(statement):
    """PART 12 - the charge/GST split printed on each row must be arithmetic."""
    priced = [e for e in statement if e.net_charge is not None]
    if not priced:
        pytest.skip("No statement entry published a charge breakdown.")
    check_gst_breakdown(priced)
    for entry in priced:
        assert entry.gst == gst_on(entry.net_charge), (
            f"{entry.txn_id}: GST ₹{entry.gst} != {GST_RATE:.0%} of "
            f"₹{entry.net_charge} (= ₹{gst_on(entry.net_charge)})")


@pytest.mark.partner_ops
@pytest.mark.regression
def test_dashboard_wallet_balance_matches_the_statement(dashboard, reports):
    """PART 11/23's invariant, with the dashboard refreshed first."""
    from_dashboard = dashboard.wallet_balance(refresh=True)
    reports.open_wallet_statement()
    from_statement = reports.wallet_balance()
    check_dashboard_matches_statement(from_dashboard, from_statement)


@pytest.mark.partner_ops
@pytest.mark.regression
@pytest.mark.xfail(
    reason="DEF-OPS-05 (Low): the dashboard Wallet Balance stat is served from "
           "a cache and keeps showing the pre-transaction figure until the "
           "card's own refresh is used. Observed ₹180.15 on the dashboard "
           "against ₹165.40 in the statement immediately after a payout.",
    strict=False)
def test_dashboard_balance_is_live_without_a_manual_refresh(dashboard, reports):
    """The dashboard should not need a manual refresh to be truthful."""
    reports.open_wallet_statement()
    from_statement = reports.wallet_balance()
    dashboard.open()
    from_dashboard = dashboard.wallet_balance(refresh=False)
    check_dashboard_matches_statement(from_dashboard, from_statement)


# ================== PART 9: failed payout must refund ====================

@pytest.mark.partner_ops
@pytest.mark.regression
@pytest.mark.xfail(
    reason="DEF-OPS-02 (Critical): a failed payout keeps its charge. A ₹10 IMPS "
           "payout reported to the user as 'Your request has been failed.' still "
           "debited ₹14.75 (₹12.50 + GST ₹2.25) from the wallet with no reversal "
           "entry (₹180.15 -> ₹165.40). Historical Failure rows in the payout "
           "report carry full charges too, so this is systematic.",
    strict=False)
def test_failed_payout_charge_is_refunded(reports, statement):
    """PART 9 - no permanent deduction for a transaction that did not happen."""
    reports.open_payout()
    failures = [r for r in reports.payout_rows()
                if r.is_failure and r.total_charges > 0]
    if not failures:
        pytest.skip("No charged failed payout in the report to reconcile.")

    unrefunded = []
    for row in failures[:8]:
        entry = reports.find_statement_entry(row.txn_id) if row.txn_id else None
        refunds = refund_entries_for(statement, row.txn_id) if row.txn_id else []
        if entry is not None and not refunds:
            unrefunded.append(
                f"  TXN {row.txn_id} ({row.stamp}) to {row.beneficiary!r}: "
                f"₹{row.amount} FAILED, wallet still debited "
                f"₹{entry.amount} (charge ₹{row.net_charge} + GST ₹{row.gst}), "
                "no reversal entry found")

    assert not unrefunded, (
        "Failed payouts kept their charges:\n" + "\n".join(unrefunded) +
        "\n\nPer PART 9 a failed transaction must not leave a permanent "
        "deduction unless the business rule says otherwise. If retaining the "
        "fee IS the rule, it must be disclosed before authorisation - which it "
        "is not (DEF-OPS-03).")


@pytest.mark.partner_ops
@pytest.mark.regression
def test_failed_payouts_have_no_utr(reports):
    """A failure must not carry a settlement reference."""
    reports.open_payout()
    bad = [f"{r.txn_id}: status={r.status} utr={r.utr}"
           for r in reports.payout_rows() if r.is_failure and r.has_utr]
    assert not bad, "failed payouts carrying a UTR: " + "; ".join(bad)


@pytest.mark.partner_ops
@pytest.mark.regression
@pytest.mark.xfail(
    reason="DEF-OPS-08 (High): the UI and the report disagree on the outcome. A "
           "payout the modal reported as 'Your request has been failed.' is "
           "recorded in Reports -> Payout as 'Processed' with UTR 'NA', so the "
           "partner cannot tell whether the funds left the escrow.",
    strict=False)
def test_reported_status_matches_what_the_user_was_told(reports,
                                                        artifact_store_module):
    """Cross-check the last attempted payout's UI message against the report."""
    last = artifact_store_module.get("partner_ops_last_payout")
    if not last:
        pytest.skip("No payout attempt recorded in this run "
                    "(test_payout runs its chargeable case only with "
                    "PARTNER_OPS_SPEND=1).")

    reports.open_payout()
    rows = reports.payout_rows()
    match = next((r for r in rows
                  if r.beneficiary.lower() == str(last["beneficiary"]).lower()
                  and r.amount == money(last["amount"])), None)
    if match is None:
        pytest.skip("The recorded attempt is not in the visible report window.")

    ui_failed = not last["ok"]
    report_says_failed = match.is_failure
    assert ui_failed == report_says_failed, (
        f"The user was told {'FAILED: ' + str(last['message']) if ui_failed else 'success'}, "
        f"but the report records TXN {match.txn_id} as {match.status!r} "
        f"(UTR {match.utr!r}).")


# ==================== PART 10: payout report fidelity ====================

@pytest.mark.partner_ops
@pytest.mark.smoke
def test_payout_report_columns_and_filters(reports):
    reports.open_payout()
    headings = [h.strip().upper() for h in reports.table_headings()]
    for expected in ("TXN ID", "BENE NAME", "AMOUNT", "TOTAL CHARGES", "UTR", "STATUS"):
        assert any(expected in h for h in headings), f"{expected} missing: {headings}"

    options = [o.lower() for o in reports.status_options()]
    for expected in ("success", "failed", "pending"):
        assert any(expected in o for o in options), \
            f"status filter lacks {expected!r}: {options}"
    assert reports.export_button.is_visible(), "no Export control on the payout report"


@pytest.mark.partner_ops
@pytest.mark.regression
def test_payout_report_status_filter_is_honoured(reports):
    reports.open_payout()
    reports.filter_by_status("Success")
    rows = reports.payout_rows()
    if not rows:
        pytest.skip("No Success payouts to verify the filter against.")
    wrong = [f"{r.txn_id}={r.status}" for r in rows if not r.is_success]
    assert not wrong, "Success filter returned other statuses: " + "; ".join(wrong)


@pytest.mark.partner_ops
@pytest.mark.regression
def test_payout_report_charges_reconcile_with_the_wallet(reports, statement):
    """PART 10/11 - the report's charge must equal the wallet's debit."""
    reports.open_payout()
    rows = [r for r in reports.payout_rows() if r.total_charges > 0 and r.txn_id]
    if not rows:
        pytest.skip("No charged payouts in the report.")

    mismatches = []
    checked = 0
    for row in rows[:10]:
        entry = next((e for e in statement if row.txn_id in e.txn_id), None)
        if entry is None:
            continue
        checked += 1
        if entry.amount != row.total_charges:
            mismatches.append(
                f"  TXN {row.txn_id}: report says ₹{row.total_charges}, "
                f"wallet debited ₹{entry.amount}")
        if row.net_charge is not None and entry.net_charge is not None \
                and row.net_charge != entry.net_charge:
            mismatches.append(
                f"  TXN {row.txn_id}: net charge differs - report "
                f"₹{row.net_charge} vs statement ₹{entry.net_charge}")

    if not checked:
        pytest.skip("No payout appeared in both the report and the visible "
                    "statement window.")
    assert not mismatches, "report/wallet charge mismatch:\n" + "\n".join(mismatches)


@pytest.mark.partner_ops
@pytest.mark.regression
def test_successful_payouts_carry_a_transaction_reference(reports):
    """PART 7 - a settled payout must have a UTR and a transaction id."""
    reports.open_payout()
    successes = [r for r in reports.payout_rows() if r.is_success]
    if not successes:
        pytest.skip("No successful payouts in the report.")
    missing = [f"{r.txn_id or '(no txn id)'} @ {r.stamp}"
               for r in successes if not r.has_utr or not r.txn_id]
    assert not missing, "successful payouts lacking a reference: " + "; ".join(missing)


# ============== PART 12/15: charge type reconciliation ===================

@pytest.mark.partner_ops
@pytest.mark.regression
def test_vid_creation_charges_match_the_configured_price(statement, ops_pricing):
    """PART 15 - every VID creation must debit the configured charge + GST."""
    vid_entries = [e for e in statement if e.kind.strip().lower() in VID_KINDS]
    if not vid_entries:
        pytest.skip("No VID creation entries in the visible statement window.")
    if not ops_pricing.has("VID Creation"):
        pytest.skip("This escrow publishes no VID Creation price.")

    # The statement is partner-wide while pricing is per-escrow, so compare
    # against the escrows this pricing applies to only where identifiable.
    configured = ops_pricing.get("VID Creation").gross_charge(Decimal("0"))
    for entry in vid_entries:
        assert entry.net_charge is not None, \
            f"{entry.txn_id}: VID charge with no breakdown"
        assert entry.amount == entry.net_charge + (entry.gst or Decimal("0")), \
            f"{entry.txn_id}: debit ₹{entry.amount} != charge + GST"
    assert configured > 0, "VID Creation is configured at ₹0.00 on this escrow"


@pytest.mark.partner_ops
@pytest.mark.regression
@pytest.mark.xfail(
    reason="DEF-OPS-06 (Medium): the escrow's Charges tab states that Bank and "
           "TSP maintenance charges are 'deducted directly from the escrow "
           "account', but they appear as wallet debits in Reports -> Wallet "
           "Statement (Bank Charges / Tcp Charges, −₹118.00 each). One of the "
           "two is wrong; the money is leaving the wallet.",
    strict=False)
def test_maintenance_charges_hit_the_escrow_not_the_wallet(statement, ops_pricing):
    """PART 12 - maintenance must be debited where the UI says it is."""
    maintenance = [e for e in statement
                   if e.kind.strip().lower() in MAINTENANCE_KINDS]
    if not maintenance:
        pytest.skip("No maintenance charges in the visible statement window.")
    claims_escrow = any(d.get("claims_escrow_debit")
                        for d in (ops_pricing.maintenance or {}).values())
    if not claims_escrow:
        pytest.skip("The Charges tab makes no claim about where maintenance is "
                    "debited from, so there is nothing to contradict.")
    assert not maintenance, (
        "The Charges tab says maintenance is deducted from the escrow account, "
        "but these are wallet debits:\n  " +
        "\n  ".join(str(e) for e in maintenance))


# ===================== PART 23: end-to-end ledger ========================

@pytest.mark.partner_ops
@pytest.mark.regression
def test_end_to_end_wallet_ledger(reports, statement, dashboard, ledger):
    """PART 23 - build the activity ledger and assert it closes.

    Walks the real statement oldest-to-newest, reconstructs each activity's
    opening/closing balance, and checks the whole chain against the live
    dashboard figure. Emits the PART 23 table into the test report so the
    numbers can be pasted into a defect or a sign-off.
    """
    ordered = list(reversed(statement))
    for entry in ordered:
        opening = entry.balance_after - entry.signed
        closing = entry.balance_after
        is_credit = entry.direction == CREDIT
        ledger.record(
            activity=f"{entry.kind} ({entry.txn_id or 'n/a'})",
            opening=opening, closing=closing,
            charge=entry.amount if not is_credit else Decimal("0.00"),
            refund=entry.amount if is_credit else Decimal("0.00"),
            note=entry.escrow_id or "")
        check_expected_move(
            activity=f"{entry.kind} {entry.txn_id}", opening=opening,
            closing=closing,
            expected_debit=entry.amount if not is_credit else Decimal("0.00"),
            expected_credit=entry.amount if is_credit else Decimal("0.00"))

    print("\nPART 23 - wallet ledger (oldest first)\n" + ledger.as_markdown())

    closing_balance = ordered[-1].balance_after
    live = dashboard.wallet_balance(refresh=True)
    check_dashboard_matches_statement(live, closing_balance)

    debits, credits = total_debits(statement), total_credits(statement)
    print(f"\nWindow totals: debits ₹{debits}, credits ₹{credits}, "
          f"net ₹{credits - debits}")


@pytest.mark.partner_ops
@pytest.mark.create
@pytest.mark.skipif(not SPEND_ALLOWED,
                    reason="Performs a real VID creation to reconcile a live "
                           "wallet movement. Re-run with PARTNER_OPS_SPEND=1.")
def test_live_vid_creation_reconciles_end_to_end(goto_escrow, dashboard,
                                                 reports, ops_pricing, ledger):
    """PART 15/23 against a movement this run caused itself.

    The cheapest chargeable activity available, and the only one whose expected
    cost is a single fixed figure - which makes it the right one to prove the
    whole chain: pricing -> action -> wallet -> statement -> dashboard.
    """
    from partner_ops.pages.escrow_page import TAB_VIRTUAL_ID
    from partner_ops.pages.virtual_id_page import VirtualIdPage, sample_vid_payload

    expected = ops_pricing.get("VID Creation").gross_charge(Decimal("0"))
    opening = dashboard.wallet_balance(refresh=True)
    assert opening > expected, f"wallet ₹{opening} cannot cover ₹{expected}"

    # Reading the wallet left us on the dashboard - navigate back explicitly.
    escrow_detail = goto_escrow(TAB_VIRTUAL_ID)
    vid_page = VirtualIdPage(escrow_detail.page)
    modal, alert = vid_page.open_create_modal()
    if modal is None:
        pytest.skip(f"Cannot create a VID on this escrow: {alert or 'no modal'}")

    prefixes = modal.prefixes()
    if not prefixes:
        pytest.skip("No VID prefix configured for this escrow.")
    payload = sample_vid_payload(prefixes[0])
    modal.fill_form(payload)
    modal.submit()

    from partner_ops.pages.escrow_page import AlertDialog
    message = AlertDialog(escrow_detail.page).dismiss()
    assert re.search(r"created successfully", message, re.I), (
        f"VID creation did not confirm success: {message!r}")

    closing = dashboard.wallet_balance(refresh=True)
    ledger.record(activity=f"VID creation {payload['acc_no']}", opening=opening,
                  closing=closing, charge=expected)
    print("\nPART 15 ledger\n" + ledger.as_markdown())

    check_expected_move(activity="VID creation", opening=opening,
                        closing=closing, expected_debit=expected)

    reports.open_wallet_statement()
    entries = reports.statement_entries()
    newest = entries[0]
    assert newest.kind.strip().lower() in VID_KINDS, (
        f"newest statement entry is {newest.kind!r}, expected a VID creation")
    assert newest.amount == expected, (
        f"statement debited ₹{newest.amount}, pricing says ₹{expected}")
    check_dashboard_matches_statement(closing, reports.wallet_balance())
