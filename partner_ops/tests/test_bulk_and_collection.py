"""PARTS 21, 22, 24 - bulk payment, bulk reconciliation, collection.

What exists here and what does not:

* **Bulk entry points are real and asserted**: *Bulk Payout* and *Payout
  Batches* on the Payout tab, *Bulk Beneficiary* and *Beneficiary Batches* on the
  Beneficiary tab, and a *Bulk Bene Report* tab under Reports. The API carries
  `payout/excrow/bulk-payment`, `v2/payout/bulk-beneficiary`,
  `payout/bulk-payout-report`, `payout/bulk-payout-batch-report`,
  `payout/bulk-bene-report` and `payout/bulk-payout-batch-report` downloads.
* **Executing a bulk payment is not automated.** It is an upload-driven flow
  whose template comes from `service/get-template-list`, and a bulk run would
  fire many chargeable payouts at once - on a wallet holding ~₹150, against a
  product that keeps the charge when a payout fails (DEF-OPS-02). Automating a
  spray of unrefundable debits before that defect is fixed would be reckless,
  so the executing tests **assert the entry points and reconciliation surfaces**
  and skip the money-moving step with that reason stated.
* **Collection (PART 24) is inbound.** A collection is money arriving at a
  Virtual ID or UPI VPA from outside; nothing in the partner portal initiates
  one, so it cannot be driven from here. What *is* verifiable is the accounting:
  the `VID Collection` report, the configured `Collection via VID` percentage
  price with its min/max, and the resulting wallet movement. That is what these
  tests do.
"""
from __future__ import annotations

import re
from decimal import Decimal

import pytest

from partner_ops.data.pricing import money
from partner_ops.ledger import check_gst_breakdown
from partner_ops.pages.escrow_page import TAB_BENEFICIARY, TAB_PAYOUT
from partner_ops.pages.reports_page import (
    TAB_BULK_BENE, TAB_VID_COLLECTION, ReportsPage)


# ===================== PART 21: bulk entry points ========================

@pytest.mark.partner_ops
@pytest.mark.regression
def test_bulk_payout_entry_points_exist(escrow_detail, ops_page):
    from partner_ops.pages.payout_page import PayoutPage
    escrow_detail.open_tab(TAB_PAYOUT)
    payout = PayoutPage(ops_page)
    assert payout.bulk_payout_button.is_visible(), "no Bulk Payout control"
    assert payout.payout_batches_button.is_visible(), \
        "no Payout Batches control - bulk runs would not be traceable"


@pytest.mark.partner_ops
@pytest.mark.regression
def test_bulk_beneficiary_entry_points_exist(escrow_detail, ops_page):
    from partner_ops.pages.beneficiary_page import BeneficiaryPage
    escrow_detail.open_tab(TAB_BENEFICIARY)
    bene = BeneficiaryPage(ops_page)
    assert bene.bulk_button.is_visible(), "no Bulk Beneficiary control"
    assert bene.batches_button.is_visible(), "no Beneficiary Batches control"


@pytest.mark.partner_ops
@pytest.mark.regression
def test_bulk_payout_dialog_explains_its_input(escrow_detail, ops_page):
    """Opening Bulk Payout should offer a template or an upload, not a dead end."""
    from partner_ops.pages.payout_page import PayoutPage
    escrow_detail.open_tab(TAB_PAYOUT)
    payout = PayoutPage(ops_page)
    payout.bulk_payout_button.click()
    ops_page.wait_for_timeout(5_000)

    from partner_ops.pages.escrow_page import AlertDialog
    alert = AlertDialog(ops_page)
    if alert.visible(timeout=2_500):
        message = alert.dismiss()
        pytest.skip(f"Bulk Payout is gated on this escrow: {message!r}")

    panes = ops_page.locator(".cdk-overlay-pane")
    if not panes.count():
        pytest.skip("Bulk Payout opened no dialog and raised no alert.")

    text = re.sub(r"\s+", " ", panes.last.inner_text())
    has_upload = ops_page.locator("input[type='file']").count() > 0
    offers_template = bool(re.search(r"template|sample|download|format", text, re.I))
    assert has_upload or offers_template, (
        "Bulk Payout offers neither a file input nor a template/format hint, so "
        f"a partner cannot tell what to supply. Dialog said: {text[:300]!r}")
    ops_page.keyboard.press("Escape")


@pytest.mark.partner_ops
@pytest.mark.create
def test_execute_a_bulk_payment(escrow_detail):
    """PARTS 21/22 - a real bulk run, then per-transaction reconciliation."""
    pytest.skip(
        "Not automated deliberately. A bulk run fires many payouts at once, and "
        "DEF-OPS-02 means every failure inside the batch keeps its charge + GST "
        "with no refund. Against a wallet of ~₹150 that is both destructive and "
        "unreconcilable. Re-enable once DEF-OPS-02 is fixed, then assert per "
        "PART 22: every row's amount, each charge, total bulk amount, "
        "success/failure/refund counts, and "
        "'total successful transactions + charges == wallet deduction' via "
        "partner_ops.ledger.check_expected_move.")


# ================= PART 22: bulk reconciliation surfaces =================

@pytest.mark.partner_ops
@pytest.mark.regression
def test_bulk_bene_report_is_reachable_and_structured(ops_page):
    reports = ReportsPage(ops_page)
    reports.open()
    assert TAB_BULK_BENE in reports.tabs(), \
        f"Bulk Bene Report tab missing: {reports.tabs()}"
    reports.open_tab(TAB_BULK_BENE)
    headings = reports.table_headings()
    rows = reports.table_rows()
    assert headings or rows or re.search(
        r"no record", ops_page.locator("body").inner_text(), re.I), (
        "the Bulk Bene Report rendered neither headings, rows, nor an "
        "empty-state message")


# ======================== PART 24: collection ============================

@pytest.mark.partner_ops
@pytest.mark.regression
def test_vid_collection_report_is_reachable_and_structured(ops_page):
    reports = ReportsPage(ops_page)
    reports.open()
    assert TAB_VID_COLLECTION in reports.tabs(), \
        f"VID Collection tab missing: {reports.tabs()}"
    reports.open_tab(TAB_VID_COLLECTION)
    body = ops_page.locator("body").inner_text()
    assert reports.table_headings() or reports.table_rows() or \
        re.search(r"no record", body, re.I), \
        "the VID Collection report rendered nothing at all"


@pytest.mark.partner_ops
@pytest.mark.regression
def test_collection_pricing_is_configured_with_sane_bounds(ops_pricing):
    """PART 12/24 - the collection price and its min/max must be coherent."""
    if not ops_pricing.has("Collection via VID"):
        pytest.skip("This escrow prices no VID collection.")
    charge = ops_pricing.get("Collection via VID")

    assert charge.percent > 0, "collection is percentage-based but priced at 0%"
    assert charge.minimum is not None and charge.maximum is not None, (
        "a percentage collection charge with no min/max is unbounded - "
        f"min={charge.minimum}, max={charge.maximum}")
    assert charge.minimum <= charge.maximum, \
        f"min ₹{charge.minimum} exceeds max ₹{charge.maximum}"

    # The cap must actually bind, and the floor must actually lift.
    tiny, huge = Decimal("10"), Decimal("10000000")
    assert charge.net_charge(tiny) == charge.minimum, (
        f"a ₹{tiny} collection should be charged the ₹{charge.minimum} floor, "
        f"got ₹{charge.net_charge(tiny)}")
    assert charge.net_charge(huge) == charge.maximum, (
        f"a ₹{huge} collection should be capped at ₹{charge.maximum}, "
        f"got ₹{charge.net_charge(huge)}")


@pytest.mark.partner_ops
@pytest.mark.regression
def test_collection_charges_in_the_wallet_are_priced_correctly(ops_page,
                                                              ops_pricing):
    """Reconcile any real collection charge against the configured percentage."""
    reports = ReportsPage(ops_page)
    reports.open_wallet_statement()
    entries = reports.statement_entries(limit=40)
    collections = [e for e in entries
                   if re.search(r"collect|upi|vid", e.kind, re.I)
                   and e.net_charge is not None]
    if not collections:
        pytest.skip("No collection charge entries in the visible statement window.")

    check_gst_breakdown(collections)
    if not ops_pricing.has("Collection via VID"):
        return
    charge = ops_pricing.get("Collection via VID")
    for entry in collections:
        assert charge.minimum <= entry.net_charge <= charge.maximum, (
            f"{entry.txn_id}: collection charge ₹{entry.net_charge} sits outside "
            f"the configured ₹{charge.minimum}-₹{charge.maximum} band")


@pytest.mark.partner_ops
@pytest.mark.regression
def test_collection_cannot_be_initiated_from_the_partner_portal(escrow_detail):
    """PART 24 - records that collection is inbound-only from here.

    If a control to raise a collection ever appears, this fails and the
    end-to-end collection tests should be written against it.
    """
    body = escrow_detail.body_text()
    initiators = escrow_detail.page.get_by_role(
        "button", name=re.compile(r"collect now|raise collection|new collection",
                                  re.I))
    assert initiators.count() == 0, (
        "a collection-initiating control now exists - implement PART 24's "
        "end-to-end collection tests (success, failure, refund, wallet and "
        "report reconciliation) against it")
    assert re.search(r"escrow|charges|payout", body, re.I)
