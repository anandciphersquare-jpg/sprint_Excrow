"""Fixtures for the Partner operations suite.

Design decisions worth knowing before you add a test here:

* **One session per test is too expensive and too fragile.** A partner login is
  two network round trips plus an OTP, and the portal logs out after ~2 minutes
  idle. The authenticated page is therefore **module-scoped**: tests inside a
  module share a session and each is responsible for leaving the app on a sane
  route. `partner_ops_page` (function-scoped) is available where a test needs a
  pristine, unauthenticated page - the login and access-control tests use it.
* **Login goes through the DEF-OPS-01 workaround by default.** Without it the
  backend takes the direct-login branch and the front end strands the session on
  `#/auth/login`, so nothing authenticated is reachable at all. See
  `partner_ops.pages.login_page` for the full account.
* **Geolocation is mandatory.** Both portals gate the login button behind
  `navigator.geolocation`; the root `browser_context_args` already grants it.
* **A run must not leave the escrow ambiguous.** `ops_escrow` resolves the
  escrow under test from `PARTNER_OPS_ESCROW_ID`, then the artifact store, then
  the first fully-provisioned Active escrow it can find - and skips with an
  actionable message rather than silently testing a half-provisioned record.
"""
from __future__ import annotations

import os

import pytest
from playwright.sync_api import Browser

from config.settings import settings
from core.errors import PageErrorMonitor
from partner_ops.ledger import WalletLedger
from partner_ops.pages.beneficiary_page import BeneficiaryPage
from partner_ops.pages.dashboard_page import PartnerDashboardPage
from partner_ops.pages.escrow_page import (
    TAB_BENEFICIARY, TAB_PAYOUT, EscrowDetailPage, EscrowListPage)
from partner_ops.pages.login_page import PartnerOpsLoginPage
from partner_ops.pages.overlays import clear_blocking_overlays


@pytest.fixture
def ops_monitor() -> PageErrorMonitor:
    return PageErrorMonitor()


@pytest.fixture
def partner_ops_context(browser: Browser, browser_context_args):
    """A throwaway context for tests that must start unauthenticated."""
    context = browser.new_context(**browser_context_args)
    context.set_default_timeout(settings.default_timeout_ms)
    yield context
    context.close()


@pytest.fixture
def partner_ops_page(partner_ops_context, ops_monitor):
    page = partner_ops_context.new_page()
    ops_monitor.attach(page)
    yield page


# --------------------------------------------------------------------------
# Shared authenticated session (module-scoped)
# --------------------------------------------------------------------------
@pytest.fixture(scope="module")
def ops_session(browser: Browser, browser_context_args):
    """A logged-in partner page shared by every test in a module."""
    context = browser.new_context(**browser_context_args)
    context.set_default_timeout(settings.default_timeout_ms)
    page = context.new_page()

    login = PartnerOpsLoginPage(page)
    login.login(fresh_location=True)
    try:
        login.assert_logged_in()
    except AssertionError:
        context.close()
        pytest.skip(
            "Could not establish a partner session. If the app stayed on "
            "#/auth/login with a 200 login response, that is DEF-OPS-01 "
            "(direct-login branch unhandled) and the fresh-location workaround "
            "has stopped being sufficient - see docs/"
            "partner-operations-test-cases.md.")
    yield page
    context.close()


@pytest.fixture
def ops_page(ops_session):
    """The shared session, with any blocking overlay cleared before each test.

    Tests share a session, so one test's leftover alert or idle prompt would
    otherwise make the next test fail on an unrelated click - with an error
    naming the click target rather than the backdrop covering it.
    """
    clear_blocking_overlays(ops_session)
    return ops_session


@pytest.fixture
def dashboard(ops_page) -> PartnerDashboardPage:
    page = PartnerDashboardPage(ops_page)
    page.open()
    return page


@pytest.fixture
def escrow_list(ops_page) -> EscrowListPage:
    page = EscrowListPage(ops_page)
    page.open()
    page.show_all_rows()
    return page


@pytest.fixture
def ledger() -> WalletLedger:
    return WalletLedger()


# --------------------------------------------------------------------------
# Escrow selection
# --------------------------------------------------------------------------
@pytest.fixture(scope="module")
def ops_escrow(ops_session, artifact_store_module) -> dict:
    """Resolve a fully-provisioned Active escrow to run operations against.

    'Fully provisioned' means the detail view exposes the Beneficiary and Payout
    tabs. A merely-Active escrow may expose only four tabs, and every payout
    test against it would fail for a precondition reason rather than a product
    reason.
    """
    wanted = os.getenv("PARTNER_OPS_ESCROW_ID") or artifact_store_module.get(
        "partner_ops_escrow_id")

    listing = EscrowListPage(ops_session)
    listing.open()
    listing.show_all_rows()

    candidates = []
    if wanted:
        row = listing.find_row(wanted)
        if row:
            candidates.append(row)
    candidates += [r for r in listing.rows()
                   if r.is_active and r not in candidates]

    detail = EscrowDetailPage(ops_session)
    rejected: list[str] = []
    best: dict | None = None

    for row in candidates[:6]:
        # A shorter tab wait here: this loop may try several escrows, and one
        # that refuses to open should cost seconds, not half a minute.
        alert = listing.open_details(row.escrow_id)
        tabs = detail.tabs(timeout=8_000)
        provisioned = (any(t.lower() == TAB_BENEFICIARY.lower() for t in tabs)
                       and any(t.lower() == TAB_PAYOUT.lower() for t in tabs))
        if not provisioned:
            rejected.append(
                f"{row.escrow_id}: "
                f"{alert or (', '.join(tabs) if tabs else 'did not open')}")
            listing.open()
            listing.show_all_rows()
            continue

        # Prefer an escrow that actually has beneficiaries. A provisioned escrow
        # with an empty beneficiary list satisfies the tab check but makes most
        # of the beneficiary and payout suite skip, which looks like coverage
        # while proving nothing.
        beneficiaries = 0
        try:
            detail.open_tab(TAB_BENEFICIARY)
            # Via the page object, so a still-rendering table is waited out and
            # a "No Record Found" placeholder is not counted as a beneficiary.
            beneficiaries = len(BeneficiaryPage(ops_session).rows())
        except Exception:
            beneficiaries = 0

        candidate = {"escrow_id": row.escrow_id, "escrow_name": row.name,
                     "bank": row.bank, "tabs": tabs,
                     "beneficiary_count": beneficiaries}
        if best is None or beneficiaries > best["beneficiary_count"]:
            best = candidate
        # A pinned escrow, or one with data, is good enough - stop looking.
        if wanted and row.escrow_id == wanted:
            return candidate
        if beneficiaries:
            return candidate

        rejected.append(f"{row.escrow_id}: provisioned but no beneficiaries")
        listing.open()
        listing.show_all_rows()

    if best is not None:
        return best

    pytest.skip(
        "No fully-provisioned Active escrow found (needs the Beneficiary and "
        "Payout tabs, which require a VID prefix and a usable bank credential). "
        "Set PARTNER_OPS_ESCROW_ID to target one explicitly.\n  Tried:\n  "
        + "\n  ".join(rejected[:10]))


@pytest.fixture(scope="module")
def artifact_store_module():
    from core.artifact_store import ArtifactStore
    return ArtifactStore()


@pytest.fixture
def goto_escrow(ops_page, ops_escrow):
    """Re-open the escrow under test, optionally on a given tab.

    Needed because fixtures navigate: requesting `dashboard` alongside
    `escrow_detail` leaves the browser on the dashboard, so a later
    `escrow_detail.open_tab(...)` finds no tabs. Any test that touches both must
    navigate back explicitly rather than rely on fixture ordering.
    """
    def _open(tab: str | None = None) -> EscrowDetailPage:
        listing = EscrowListPage(ops_page)
        listing.open()
        listing.show_all_rows()
        listing.open_details(ops_escrow["escrow_id"])
        detail = EscrowDetailPage(ops_page)
        if tab:
            detail.open_tab(tab)
        return detail
    return _open


@pytest.fixture
def escrow_detail(goto_escrow) -> EscrowDetailPage:
    """The detail view of the escrow under test, already open."""
    return goto_escrow()


@pytest.fixture(scope="module")
def ops_pricing(ops_session, ops_escrow):
    """The escrow's real configured Service Pricing - read once per module."""
    listing = EscrowListPage(ops_session)
    listing.open()
    listing.show_all_rows()
    listing.open_details(ops_escrow["escrow_id"])
    return EscrowDetailPage(ops_session).service_pricing()
