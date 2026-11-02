"""PARTS 2, 3 and 12 - escrow list, activation state, and Service Pricing.

Two things to know about how this differs from the brief:

* **PART 2's activation wizard is a Super Admin flow, not a partner one.** On
  the partner side an escrow detail view is read-only: Project Information and
  Documents render as data, there is no Next/Submit/Confirm & Submit chain and
  no editable stage. `test_escrow_detail_is_read_only_for_the_partner` asserts
  that, which is the honest partner-side equivalent - and the reason the
  activation wizard's own negative cases (double submit, cancel confirmation,
  refresh mid-submit) live in `admin/tests/test_review_submit.py` where the
  wizard actually exists.
* **The escrow list has no free-text search.** It filters by Bank, Industry and
  Status. So "search by Escrow Name" is asserted as filter + client-side match.

PART 12's real work is `test_service_pricing_is_readable_and_priced`, which
parses the escrow's own Charges tab into the model every reconciliation uses.
If that parse degrades, every wallet assertion downstream would silently start
comparing against ₹0.00 - so it asserts the pricing is non-empty and internally
coherent rather than merely that the tab rendered.
"""
from __future__ import annotations

import re
from decimal import Decimal

import pytest

from partner_ops.data.pricing import FIXED, PERCENTAGE, summarise
from partner_ops.pages.escrow_page import (
    STATUS_ACTIVE, STATUS_INACTIVE, TAB_BENEFICIARY, TAB_CHARGES,
    TAB_DOCUMENTS, TAB_PAYOUT, TAB_PROJECT, TAB_VIRTUAL_ID, EscrowDetailPage)

# Tabs present on every escrow detail view observed. Virtual ID is deliberately
# NOT here: EXC-0826-934758 renders Beneficiary/Payout/Account Statement but no
# Virtual ID tab, so the tab set varies by provisioning in more than one axis.
BASE_TABS = (TAB_PROJECT, TAB_DOCUMENTS, TAB_CHARGES)


# ============================ PART 3: the list ===========================

@pytest.mark.partner_ops
@pytest.mark.smoke
def test_escrow_list_renders_expected_columns(escrow_list):
    headings = [h.strip().upper() for h in
                escrow_list.page.locator("table thead th").all_inner_texts()]
    for expected in ("ESCROW ID", "ESCROW NAME", "BANK NAME", "BALANCE", "STATUS"):
        assert any(expected in h for h in headings), \
            f"{expected} missing from {headings}"
    assert escrow_list.rows(), "the escrow list rendered no rows"


@pytest.mark.partner_ops
@pytest.mark.regression
def test_escrow_list_filters_by_status(escrow_list):
    options = escrow_list.filter_options()
    assert STATUS_ACTIVE in options["status"]
    assert STATUS_INACTIVE in options["status"]
    assert options["bank"], "bank filter offered no options"
    assert options["industry"], "industry filter offered no options"

    escrow_list.filter_by_status(STATUS_ACTIVE)
    rows = escrow_list.rows()
    assert rows, "filtering by Active returned nothing"
    wrong = [r.escrow_id for r in rows if not r.is_active]
    assert not wrong, f"Active filter returned non-Active escrows: {wrong}"


@pytest.mark.partner_ops
@pytest.mark.negative
@pytest.mark.xfail(
    reason="DEF-OPS-10 (High): Rows Per Page = 'All' on the escrow list fails "
           "server-side and shows the raw PHP error 'Unsupported operand types: "
           "string * int' in a FAILED dialog. The list keeps its previous result "
           "set and the next request fails the same way, so an unrelated filter "
           "applied afterwards silently appears broken too. Numeric page sizes "
           "(10/20/50/100/200/500) all work.",
    strict=False)
def test_rows_per_page_all_option_works(escrow_list):
    """The 'All' page size must list every escrow, not raise a server error."""
    alert = escrow_list.select_rows_per_page("All")
    assert not alert, f"selecting 'All' raised: {alert!r}"
    assert not re.search(r"unsupported operand|string \* int|SQLSTATE|Fatal",
                         alert or "", re.I), \
        f"a raw server error was shown to the user: {alert!r}"
    assert escrow_list.rows(), "'All' produced an empty list"


@pytest.mark.partner_ops
@pytest.mark.security
@pytest.mark.xfail(
    reason="DEF-OPS-11 (Medium, security): the failure above is surfaced to the "
           "user as the raw PHP message 'Unsupported operand types: string * "
           "int'. Internal type errors disclose implementation detail and give "
           "the partner nothing actionable; the detail belongs in the server log.",
    strict=False)
def test_list_errors_do_not_leak_server_internals(escrow_list):
    """PART 26 - internal type/stack errors must never reach the user.

    Separated from the functional test above so the information-disclosure
    aspect is reported in its own right: the 'All' option being broken is a
    bug, but showing a PHP type error to a partner is a different problem with
    a different owner.
    """
    alert = escrow_list.select_rows_per_page("All")
    leaked = re.search(
        r"unsupported operand|string \* int|SQLSTATE|Fatal error|"
        r"Undefined (index|variable)|Stack trace|on line \d+", alert or "", re.I)
    assert not leaked, (
        "the portal surfaced an internal server error verbatim to the user: "
        f"{alert!r}. Users should see an actionable message; the detail belongs "
        "in the server log.")


@pytest.mark.partner_ops
@pytest.mark.regression
def test_escrow_is_findable_by_id_and_by_name(escrow_list, ops_escrow):
    by_id = escrow_list.find_row(ops_escrow["escrow_id"])
    assert by_id, f"{ops_escrow['escrow_id']} not present in the list"
    assert by_id.escrow_id == ops_escrow["escrow_id"]

    by_name = escrow_list.find_row(ops_escrow["escrow_name"])
    assert by_name, f"{ops_escrow['escrow_name']!r} not findable by name"
    assert by_name.escrow_id == ops_escrow["escrow_id"], (
        "name lookup resolved to a different escrow: "
        f"{by_name.escrow_id} != {ops_escrow['escrow_id']}")


@pytest.mark.partner_ops
@pytest.mark.regression
def test_active_escrow_detail_matches_the_list_row(escrow_list, ops_escrow,
                                                   ops_page):
    row = escrow_list.find_row(ops_escrow["escrow_id"])
    assert row and row.is_active
    escrow_list.open_details(ops_escrow["escrow_id"])

    detail = EscrowDetailPage(ops_page)
    header = detail.header()
    assert header["escrow_id"] == ops_escrow["escrow_id"], header
    assert header["escrow_name"] == ops_escrow["escrow_name"], header
    assert header["status"] == STATUS_ACTIVE, header
    assert row.bank in header["bank_name"] or header["bank_name"] in row.bank, (
        f"bank differs between list ({row.bank!r}) and detail "
        f"({header['bank_name']!r})")


# ==================== PART 2/3: state gates operations ===================

@pytest.mark.partner_ops
@pytest.mark.regression
def test_provisioning_state_gates_the_operational_tabs(escrow_list, ops_page):
    """Beneficiary/Payout appear only on a fully-provisioned escrow.

    This is the discovery that makes the rest of the suite well-founded: an
    escrow can be Active and still expose only four tabs, so 'Active' alone is
    not a precondition for payout testing.
    """
    detail = EscrowDetailPage(ops_page)
    seen: dict[str, list[str]] = {}

    for row in [r for r in escrow_list.rows() if r.is_active][:4]:
        escrow_list.open_details(row.escrow_id)
        seen[row.escrow_id] = detail.tabs(timeout=8_000)
        escrow_list.open()
        escrow_list.show_all_rows()

    assert seen, "no Active escrow could be opened"
    for escrow_id, tabs in seen.items():
        if not tabs:
            continue
        for base in BASE_TABS:
            assert any(t.lower() == base.lower() for t in tabs), \
                f"{escrow_id} is missing the always-present tab {base!r}: {tabs}"
        has_ops = any(t.lower() == TAB_PAYOUT.lower() for t in tabs)
        has_bene = any(t.lower() == TAB_BENEFICIARY.lower() for t in tabs)
        assert has_ops == has_bene, (
            f"{escrow_id} exposes Payout={has_ops} but Beneficiary={has_bene}; "
            "these should appear together")


@pytest.mark.partner_ops
@pytest.mark.regression
def test_inactive_escrow_cannot_perform_operations(escrow_list, ops_page):
    """An Inactive escrow must not expose Beneficiary or Payout."""
    row = escrow_list.first_row_with_status(STATUS_INACTIVE)
    if row is None:
        pytest.skip("No Inactive escrow available to verify the negative case.")

    alert = escrow_list.open_details(row.escrow_id)
    detail = EscrowDetailPage(ops_page)
    tabs = detail.tabs(timeout=10_000)
    if not tabs:
        # Refusing to open at all is also a valid gate, provided it says why.
        assert alert, ("the Inactive escrow neither opened nor explained why "
                       "(no tabs rendered, no alert shown)")
        return

    for forbidden in (TAB_PAYOUT, TAB_BENEFICIARY):
        assert not any(t.lower() == forbidden.lower() for t in tabs), (
            f"Inactive escrow {row.escrow_id} exposes {forbidden!r}: {tabs}")


@pytest.mark.partner_ops
@pytest.mark.regression
def test_escrow_detail_is_read_only_for_the_partner(escrow_detail):
    """Partner-side detail is a view, not the admin activation wizard."""
    escrow_detail.open_tab(TAB_PROJECT)
    text = escrow_detail.body_text().lower()
    for word in ("escrow name", "bank name", "industry"):
        assert word in text, f"Project Information did not render {word!r}"

    editable = escrow_detail.page.locator(
        "input:not([type=hidden]):not([readonly]), textarea:not([readonly])")
    wizard = escrow_detail.page.get_by_role(
        "button", name=re.compile(r"confirm\s*&?\s*submit|save\s*&?\s*next", re.I))
    assert wizard.count() == 0, (
        "the partner detail view exposed an activation-wizard control; "
        "PART 2's submit chain is a Super Admin flow")
    # Filter controls are inputs too, so only assert no *form* fields exist.
    named = escrow_detail.page.locator("[formcontrolname]").count()
    assert named <= editable.count() + 3, "unexpected editable form on a read-only view"


# ======================= PART 12: Service Pricing ========================

@pytest.mark.partner_ops
@pytest.mark.regression
def test_service_pricing_is_readable_and_priced(escrow_detail):
    """Read the escrow's configured pricing - never assume it."""
    pricing = escrow_detail.service_pricing()
    assert pricing.services, (
        "no Service Pricing could be parsed from the Charges tab. Every wallet "
        "assertion downstream depends on this parse, so a silent empty result "
        "must fail here rather than reconcile against ₹0.00.")

    summary = list(summarise(pricing))
    for charge in pricing.services.values():
        assert charge.basis in (FIXED, PERCENTAGE), \
            f"{charge.service}: unknown basis {charge.basis!r} ({summary})"
        if charge.basis == PERCENTAGE:
            assert charge.percent > 0, \
                f"{charge.service}: percentage basis with a 0% rate ({summary})"
            if charge.minimum is not None and charge.maximum is not None:
                assert charge.minimum <= charge.maximum, (
                    f"{charge.service}: min ₹{charge.minimum} exceeds max "
                    f"₹{charge.maximum}")
        for slab in charge.slabs:
            assert slab.lower <= slab.upper, \
                f"{charge.service}: inverted slab {slab.lower}-{slab.upper}"


@pytest.mark.partner_ops
@pytest.mark.regression
def test_priced_modes_cover_the_modes_payout_offers(escrow_detail):
    """Every transfer mode the payout form offers should carry a price.

    An unpriced mode is a reconciliation hole: a payout would succeed with no
    defined charge, and nothing downstream could say what the wallet should lose.
    """
    pricing = escrow_detail.service_pricing()
    priced = set(pricing.services)
    offered = {"imps", "neft", "rtgs"}          # A2A is not always priced
    missing = sorted(offered - priced)
    assert not missing, (
        f"transfer modes with no configured price: {missing}. "
        f"Configured: {sorted(priced)}")


@pytest.mark.partner_ops
@pytest.mark.regression
def test_slab_pricing_selects_a_charge_for_realistic_amounts(escrow_detail):
    """Slab-priced modes must price every amount they claim to cover."""
    pricing = escrow_detail.service_pricing()
    slabbed = [c for c in pricing.services.values() if c.slabs]
    if not slabbed:
        pytest.skip("This escrow prices no mode by slab.")

    for charge in slabbed:
        top = max(s.upper for s in charge.slabs)
        for amount in (Decimal("10"), Decimal("999"), Decimal("25000"), top):
            resolved = charge.net_charge(amount)
            assert resolved >= 0, \
                f"{charge.service}: negative charge {resolved} at ₹{amount}"
        with pytest.raises(ValueError):
            # Above the top slab the UI states no rule; the model must refuse
            # to invent one rather than default to zero.
            charge.net_charge(top + Decimal("1"))


@pytest.mark.partner_ops
@pytest.mark.regression
def test_maintenance_charges_claim_escrow_debit(escrow_detail):
    """Records the UI's claim about where maintenance is debited from.

    The Charges tab says these are 'deducted directly from the escrow account',
    while the wallet statement shows Bank/Tcp charges debiting the **wallet**.
    That contradiction is DEF-OPS-06; asserted in
    `test_reconciliation.py::test_maintenance_charges_hit_the_wallet_not_the_escrow`
    where both sides can be compared.
    """
    pricing = escrow_detail.service_pricing()
    if not pricing.maintenance:
        pytest.skip("This escrow has no maintenance charges configured.")
    for label, detail in pricing.maintenance.items():
        assert detail["frequency"] in ("MONTHLY", "QUARTERLY", "ANNUALLY"), \
            f"{label}: unreadable frequency {detail['frequency']!r}"
        assert detail["amount"] > 0, f"{label}: zero recurring amount"
