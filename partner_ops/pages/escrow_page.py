"""Escrow list and escrow detail (tabs, activation, charges).

Calibrated 14 Aug 2026.

**Escrow List** (`#/escrow-list`) is a plain table:
`CREATED DATE | ESCROW ID | ESCROW NAME | BANK NAME | BANK ACC NUMBER |
BALANCE | STATUS | ACTION`, with a filter panel offering *Select by Bank*
(IDFC, NSDL), *Select by Industry* (Real Estate, E-commerce, Marketplace,
Lending, Capital Markets, Payroll) and *Select Status* (Active, Inactive), plus
Submit/Clear and a rows-per-page select. There is **no free-text search box** -
the brief's "search by Escrow Name" is not a feature of this screen, so
`find_row` filters client-side over the rendered rows and `search` is a
deliberate alias that says so.

**Escrow Detail** tabs are `a.nav-link` and the set is *conditional on how far
the escrow is provisioned* - the single most useful discovery on this screen:

| Escrow state | Tabs |
| --- | --- |
| Active, no VID prefix / no usable bank credential | Project Information, Documents, Virtual ID, Charges |
| Fully provisioned | + Beneficiary, Payout, Account Statement |

So Beneficiary and Payout are simply absent on a half-provisioned escrow rather
than present-and-erroring, and any test that assumes seven tabs on an arbitrary
Active escrow will fail for the wrong reason. `tabs()` reports what is really
there and `open_tab` raises with the available set.

Two gates worth knowing, both hit live:

* Opening some Active escrows raises a blocking alert **"Please add bank
  credentials first"**.
* `+ Create Account` on an escrow with no configured prefix raises **"No Prefix
  found! Please contact to administration"**.

Both render as `.cdk-overlay-pane.alert-modal-panel` over a
`.cdk-overlay-backdrop`, which swallows every click on the page until it is
dismissed - hence `dismiss_alert()` and its use in `open_details`.
"""
from __future__ import annotations

import re
from decimal import Decimal

from config.settings import settings
from core.base_page import BasePage
from partner_ops.data.pricing import ServicePricing, money, parse_charges_text
from partner_ops.pages.overlays import clear_blocking_overlays

ESCROW_LIST_ROUTE = "#/escrow-list"

TAB_PROJECT = "Project Information"
TAB_DOCUMENTS = "Documents"
TAB_VIRTUAL_ID = "Virtual ID"
TAB_BENEFICIARY = "Beneficiary"
TAB_PAYOUT = "Payout"
TAB_ACCOUNT_STATEMENT = "Account Statement"
TAB_CHARGES = "Charges"

STATUS_ACTIVE = "Active"
STATUS_INACTIVE = "Inactive"


class EscrowRow:
    """One rendered row of the escrow list."""

    def __init__(self, cells: list[str]):
        self.cells = cells

    def _cell(self, index: int) -> str:
        return self.cells[index] if index < len(self.cells) else ""

    created = property(lambda self: self._cell(0))
    escrow_id = property(lambda self: self._cell(1))
    name = property(lambda self: self._cell(2))
    bank = property(lambda self: self._cell(3))
    account = property(lambda self: self._cell(4))
    balance = property(lambda self: self._cell(5))
    status = property(lambda self: self._cell(6))

    @property
    def is_active(self) -> bool:
        return self.status.strip().lower() == "active"

    def __repr__(self) -> str:
        return f"<EscrowRow {self.escrow_id} {self.name!r} {self.status}>"


class AlertDialog(BasePage):
    """The portal's blocking alert: FAILED/SUCCESS + a single OK/Okay button."""

    @property
    def panel(self):
        return self.page.locator(".cdk-overlay-pane.alert-modal-panel").first

    def visible(self, timeout: int = 4_000) -> bool:
        return self.is_visible(self.panel, timeout=timeout)

    def text(self) -> str:
        if not self.visible(timeout=1_500):
            return ""
        return re.sub(r"\s+", " ", self.panel.inner_text()).strip()

    def dismiss(self) -> str:
        """Close it and return what it said. Safe when nothing is open.

        Delegates to `clear_blocking_overlays`, which also handles the case that
        cost real debugging time here: the alert and the idle-session prompt
        stacking, so dismissing one leaves the other's backdrop swallowing every
        click on the page.
        """
        messages = clear_blocking_overlays(self.page)
        return messages[0] if messages else ""


class EscrowListPage(BasePage):
    def open(self) -> None:
        base = settings.partner_base_url.split("#")[0]
        self.goto(base + ESCROW_LIST_ROUTE)
        self.wait_for_loader()
        # A leftover alert/idle backdrop from a previous step would make every
        # click below time out against the wrong element.
        clear_blocking_overlays(self.page)
        self.page.locator("table tbody tr").first.wait_for(state="visible",
                                                           timeout=30_000)

    # ---- filter panel ----------------------------------------------------
    # Selects are located by the options they contain, not by index. The panel
    # renders four (<bank>, <industry>, <status>, <rows-per-page>) and their
    # order is not guaranteed after a re-render - an index-based status filter
    # silently drove the wrong control here and looked like a broken filter.
    def _select_containing(self, option: str):
        selects = self.page.locator("select")
        for index in range(selects.count()):
            candidate = selects.nth(index)
            try:
                texts = [t.strip().lower()
                         for t in candidate.locator("option").all_inner_texts()]
            except Exception:
                continue
            if option.strip().lower() in texts:
                return candidate
        raise AssertionError(
            f"No <select> on this page offers the option {option!r}. "
            f"Found {selects.count()} selects.")

    @property
    def bank_filter(self):
        return self._select_containing("Select by Bank")

    @property
    def industry_filter(self):
        return self._select_containing("Select by Industry")

    @property
    def status_filter(self):
        return self._select_containing("Select Status")

    @property
    def rows_per_page_select(self):
        return self._select_containing("All")

    @property
    def submit_button(self):
        return self.page.get_by_role("button", name=re.compile(r"^\s*submit\s*$", re.I)).first

    @property
    def clear_button(self):
        return self.page.get_by_role("button", name=re.compile(r"^\s*clear\s*$", re.I)).first

    def filter_options(self) -> dict[str, list[str]]:
        def options(locator):
            return [o.strip() for o in locator.locator("option").all_inner_texts()]
        return {"bank": options(self.bank_filter),
                "industry": options(self.industry_filter),
                "status": options(self.status_filter)}

    def wait_for_table_settled(self, timeout: int = 20_000) -> int:
        """Wait until the row count stops changing, then return it.

        The table re-renders asynchronously after Submit, so a fixed sleep reads
        the *previous* result set - which looks exactly like a broken filter and
        was briefly mistaken for one here. Settling on the count is outcome-
        neutral: it waits for the render to finish without assuming what the
        filter should return.
        """
        previous, stable, waited = -1, 0, 0
        while waited < timeout:
            count = self.page.locator("table tbody tr").count()
            stable = stable + 1 if count == previous else 0
            if stable >= 2 and count > 0:
                return count
            previous = count
            self.page.wait_for_timeout(500)
            waited += 500
        return self.page.locator("table tbody tr").count()

    def filter_by_status(self, status: str) -> None:
        clear_blocking_overlays(self.page)
        self.status_filter.select_option(label=status)
        self.page.wait_for_timeout(500)
        self.click(self.submit_button)
        self.wait_for_loader()
        self.wait_for_table_settled()

    def clear_filters(self) -> None:
        self.click(self.clear_button)
        self.wait_for_loader()
        self.page.wait_for_timeout(2_000)

    def show_all_rows(self) -> None:
        """Show every escrow on one page, so a target is not stranded on page 2.

        Deliberately picks the **largest numeric** page size rather than the
        'All' option: 'All' is broken server-side (DEF-OPS-10) - it raises
        "Unsupported operand types: string * int", leaves the list showing its
        previous result set, and poisons the next request too, which makes an
        unrelated status filter look broken. `select_rows_per_page("All")`
        exists so the regression test can still exercise it deliberately.
        """
        clear_blocking_overlays(self.page)
        try:
            select = self.rows_per_page_select
            options = [o.strip() for o in select.locator("option").all_inner_texts()]
            numeric = sorted((o for o in options if o.isdigit()), key=int, reverse=True)
            if not numeric:
                return
            select.select_option(label=numeric[0])
            self.wait_for_loader()
            self.wait_for_table_settled()
        except Exception:
            pass

    def select_rows_per_page(self, label: str) -> str:
        """Choose a page size and return any alert it raised."""
        clear_blocking_overlays(self.page)
        self.rows_per_page_select.select_option(label=label)
        self.wait_for_loader()
        self.page.wait_for_timeout(4_000)
        return AlertDialog(self.page).text()

    # ---- rows ------------------------------------------------------------
    def rows(self) -> list[EscrowRow]:
        raw = self.page.eval_on_selector_all(
            "table tbody tr",
            "rs => rs.map(r => [...r.querySelectorAll('td')]"
            ".map(c => c.innerText.replace(/\\s+/g,' ').trim()))")
        return [EscrowRow(cells) for cells in raw if cells and len(cells) > 2]

    def find_row(self, needle: str) -> EscrowRow | None:
        """Match on Escrow ID or Escrow Name (the list has no search box)."""
        for row in self.rows():
            if needle.lower() in (row.escrow_id.lower(), row.name.lower()):
                return row
        for row in self.rows():                        # fall back to substring
            if needle.lower() in " ".join(row.cells).lower():
                return row
        return None

    def search(self, needle: str) -> EscrowRow | None:
        """Alias for `find_row`: this screen offers filters, not a search box."""
        return self.find_row(needle)

    def first_row_with_status(self, status: str) -> EscrowRow | None:
        for row in self.rows():
            if row.status.strip().lower() == status.strip().lower():
                return row
        return None

    def open_details(self, needle: str) -> str:
        """Click View Details for `needle`. Returns any blocking alert text.

        Some Active escrows answer with "Please add bank credentials first"
        instead of opening, so the alert text is returned rather than swallowed:
        callers need to distinguish "did not open" from "opened empty".
        """
        clear_blocking_overlays(self.page)
        row = self.page.locator("table tbody tr").filter(has_text=needle).first
        row.wait_for(state="visible", timeout=20_000)
        row.get_by_text(re.compile(r"view\s*details", re.I)).first.click()
        self.wait_for_loader()
        self.page.wait_for_timeout(3_000)
        return AlertDialog(self.page).dismiss()


class EscrowDetailPage(BasePage):
    """The tabbed escrow detail view."""

    @property
    def tab_links(self):
        return self.page.locator("a.nav-link")

    def tabs(self, timeout: int = 25_000) -> list[str]:
        """The tab labels on this escrow, or [] if the detail view never opened.

        Returns empty rather than raising: an escrow can refuse to open (for
        example "Please add bank credentials first"), and callers need to treat
        that as a precondition to skip past, not as an exception to unwind.
        """
        try:
            self.tab_links.first.wait_for(state="visible", timeout=timeout)
        except Exception:
            return []
        return [t.strip() for t in self.tab_links.all_inner_texts()]

    def open_tab(self, name: str) -> None:
        """Click a tab by visible name.

        Matched on the trimmed text rather than a `hasText` regex: the anchors
        carry surrounding whitespace, so an anchored `/^Charges$/` filter never
        matches and times out.
        """
        clear_blocking_overlays(self.page)
        available = self.tabs()
        for index, label in enumerate(available):
            if label.lower() == name.lower():
                self.tab_links.nth(index).click()
                self.wait_for_loader()
                self.page.wait_for_timeout(2_500)
                return
        raise AssertionError(
            f"Tab {name!r} is not present on this escrow. Available: "
            f"{available or 'none - the detail view never rendered'}. "
            "Beneficiary/Payout/Account Statement only appear once the escrow is "
            "fully provisioned (VID prefix + usable bank credential).")

    def has_tab(self, name: str) -> bool:
        return any(t.lower() == name.lower() for t in self.tabs())

    # ---- header ----------------------------------------------------------
    def header(self) -> dict[str, str]:
        text = re.sub(r"\s+", " ", self.body_text())
        def grab(label: str) -> str:
            found = re.search(rf"{label}:\s*([^:]+?)(?=\s+(?:Escrow|Industry|Bank)\b|\s*$)",
                              text)
            return found.group(1).strip() if found else ""
        status = "Active" if re.search(r"\bActive\b", text) else (
            "Inactive" if re.search(r"\bInactive\b", text) else "")
        return {"escrow_id": grab("Escrow ID"), "escrow_name": grab("Escrow Name"),
                "industry": grab("Industry"), "bank_name": grab("Bank Name"),
                "status": status}

    def shows_escrow_id(self, escrow_id: str) -> bool:
        return escrow_id.lower() in self.body_text().lower()

    # ---- Project Information / Documents --------------------------------
    def project_info(self) -> dict[str, str]:
        self.open_tab(TAB_PROJECT)
        text = re.sub(r"[ \t]+", " ", self.body_text())
        return {"raw": text}

    def document_rows(self) -> list[list[str]]:
        self.open_tab(TAB_DOCUMENTS)
        return self.page.eval_on_selector_all(
            "table tbody tr",
            "rs => rs.map(r => [...r.querySelectorAll('td')]"
            ".map(c => c.innerText.replace(/\\s+/g,' ').trim()))")

    # ---- Charges ---------------------------------------------------------
    def charges_text(self) -> str:
        self.open_tab(TAB_CHARGES)
        self.page.get_by_text(re.compile(r"service\s*pricing", re.I)).first.wait_for(
            state="visible", timeout=25_000)
        return self.body_text()

    def service_pricing(self) -> ServicePricing:
        """The escrow's real configured pricing - never assume, read it."""
        return parse_charges_text(self.charges_text())

    # ---- Account Statement ----------------------------------------------
    def account_statement_rows(self) -> list[list[str]]:
        self.open_tab(TAB_ACCOUNT_STATEMENT)
        return self.page.eval_on_selector_all(
            "table tbody tr",
            "rs => rs.map(r => [...r.querySelectorAll('td')]"
            ".map(c => c.innerText.replace(/\\s+/g,' ').trim()))")

    def escrow_balance_on_page(self) -> Decimal | None:
        found = re.search(r"₹\s*([\d,]+\.\d{2})", self.body_text())
        return money(found.group(1)) if found else None
