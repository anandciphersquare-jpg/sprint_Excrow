"""Step 2 - Escrow module: Escrow -> Switch Escrow -> dashboard -> Add New."""
from __future__ import annotations

import re

from core.base_page import BasePage


# The admin portal labels the module "Excrow" (sic) in places and "Escrow" in
# others, so every label match must tolerate both spellings.
_ESCROW = r"e[sx]crow"


class EscrowNavPage(BasePage):
    @property
    def escrow_menu(self):
        name = re.compile(rf"^\s*{_ESCROW}", re.I)
        return self.page.get_by_role("link", name=name).or_(
            self.page.get_by_role("button", name=name)
        ).or_(
            self.page.get_by_text(re.compile(rf"^\s*{_ESCROW}\s*$", re.I))
        ).first

    @property
    def switch_escrow_button(self):
        return self.page.get_by_role(
            "button", name=re.compile(rf"switch\s*{_ESCROW}", re.I)
        ).or_(self.page.get_by_text(re.compile(rf"switch\s*{_ESCROW}", re.I))).first

    @property
    def escrow_dashboard_link(self):
        return self.page.get_by_role(
            "link", name=re.compile(rf"{_ESCROW}\s*dashboard", re.I)
        ).first

    @property
    def add_new_escrow_button(self):
        return self.page.get_by_role(
            "button",
            name=re.compile(rf"add\s*(new\s*)?{_ESCROW}|new\s*{_ESCROW}|"
                            rf"create\s*{_ESCROW}", re.I),
        ).first

    def open_escrow_module(self) -> None:
        """Sidebar "Excrow" -> #/excrow/dashboard.

        The live portal jumps straight to the module dashboard; a
        "Switch Escrow" confirmation only appears for some roles, so it is
        clicked opportunistically rather than required.
        """
        self.click(self.escrow_menu)
        if self.is_visible(self.switch_escrow_button, timeout=3_000):
            self.click(self.switch_escrow_button)
        # Hash-only route changes fire no 'load' event, so wait on controls
        # rather than the URL, and let the app-wide spinner clear first.
        self.wait_for_loader()
        if self.is_visible(self.add_new_escrow_button, timeout=10_000):
            return
        # Entering via the header mode-pill can land on an empty shell route;
        # the sidebar's own dashboard link loads the escrow list.
        if self.is_visible(self.escrow_dashboard_link, timeout=5_000):
            self.click(self.escrow_dashboard_link)
            self.wait_for_loader()
            if self.is_visible(self.add_new_escrow_button, timeout=15_000):
                return
        # UAT occasionally leaves the list unrendered on first entry; drive the
        # router straight at it (this reloads if the route alone is not enough).
        self.goto_escrow_dashboard()

    def start_new_escrow(self) -> None:
        self.wait_visible(self.add_new_escrow_button)
        self.wait_for_loader()
        self.click(self.add_new_escrow_button)

    def goto_escrow_dashboard(self) -> None:
        """Navigate to the escrow list by URL rather than by clicking.

        Clicking the sidebar is unreliable here: the add-escrow form and the
        dismissed submit confirmation both leave a `cdk-overlay-backdrop`
        behind that swallows clicks. So drive the router directly, and fall
        back to a full reload if the route does not take.
        """
        base = self.page.url.split("#")[0]
        self.page.goto(f"{base}#/excrow/dashboard", wait_until="domcontentloaded")
        self.page.wait_for_timeout(1_500)
        if not self.is_visible(self.add_new_escrow_button, timeout=10_000):
            self.page.reload(wait_until="domcontentloaded")
        self.wait_visible(self.add_new_escrow_button)
        self.wait_for_loader()

    # ---- dashboard list -------------------------------------------------
    def escrow_row(self, escrow_id: str):
        """Row for an escrow in the dashboard table (newest first)."""
        return self.page.locator("table tbody tr").filter(
            has_text=re.compile(re.escape(escrow_id), re.I)
        ).first

    def listed_escrow_ids(self) -> list[str]:
        return re.findall(r"EXC-\d{4}-\d{4,}", self.page.locator("table").inner_text())

    def is_listed(self, escrow_id: str, *, timeout: int = 20_000) -> bool:
        """Whether an escrow appears on the dashboard's first page.

        The list is newest-first, so a just-created record is on page 1.
        """
        self.goto_escrow_dashboard()
        try:
            self.escrow_row(escrow_id).wait_for(state="visible", timeout=timeout)
            return True
        except Exception:
            return False

    # Calibrated against the live list. Columns, in order:
    COLUMNS = ("Created", "Excrow Name", "Excrow Id", "Industry", "Bank Name",
               "Bank Acc Number", "Trustee", "Balance", "Status", "Action")

    def column_headers(self) -> list[str]:
        return [h.strip() for h in
                self.page.locator("table thead th").all_inner_texts()]

    def row_cells(self, escrow_id: str) -> list[str]:
        return [c.strip() for c in
                self.escrow_row(escrow_id).locator("td").all_inner_texts()]

    def row_value(self, escrow_id: str, column: str) -> str:
        """One cell, addressed by its column heading rather than by index."""
        headers = self.column_headers()
        try:
            index = headers.index(column)
        except ValueError:
            raise AssertionError(
                f"No {column!r} column on the escrow dashboard. "
                f"Columns are {headers}") from None
        cells = self.row_cells(escrow_id)
        return cells[index] if index < len(cells) else ""

    def row_edit_icon(self, escrow_id: str):
        """The list's only row action.

        It is an `<i class="icon-edit">` - **not** an anchor or a button - so it
        carries no `link`/`button` role and `get_by_role("button", name="Edit")`
        finds nothing. A test written that way passes for the wrong reason, the
        same trap the Review stage's Edit links set (see README). Match the
        markup.
        """
        return self.escrow_row(escrow_id).locator("i.icon-edit, [class*='icon-edit']").first

    def edit_url_for(self, escrow_id: str) -> str:
        base = self.page.url.split("#")[0]
        return f"{base}#/excrow/add-excrow/{escrow_id}"

    def open_escrow_for_edit(self, escrow_id: str) -> None:
        """Row edit icon -> the wizard reopened on that escrow.

        Lands on `#/excrow/add-excrow/<id>`, which renders the **Review** stage
        with every section filled in and its own Edit link - the same wizard the
        create flow ends on, not a separate edit screen.
        """
        if not self.is_listed(escrow_id):
            raise AssertionError(
                f"{escrow_id} is not on the first page of the escrow dashboard, "
                "so its row cannot be opened for edit.")
        self.wait_for_loader()
        self.click(self.row_edit_icon(escrow_id))
        self.page.wait_for_url(re.compile(rf"add-e[sx]crow/{re.escape(escrow_id)}", re.I),
                               timeout=30_000)
        self.wait_for_loader()
        self.wait_for_review_sections()

    def wait_for_review_sections(self, timeout: int = 30_000) -> None:
        """Wait for the summary to actually populate.

        The route and the loader both clear before the sections are on screen -
        they are filled from an API call a beat later - so reading the summary
        straight after the navigation finds an empty page *sometimes*. Measured:
        on one run the sections were present at t+0.1s, on another they were
        still absent when the assertion ran, which is exactly the shape of a
        test that flaps rather than one that fails.
        """
        self.page.locator(".card-header").first.wait_for(state="visible",
                                                         timeout=timeout)
