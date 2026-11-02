"""Partner dashboard - the wallet balance source for every reconciliation.

Calibrated 14 Aug 2026. The stat cards are `.stat-card`, each with a
`.stat-label`, and the balances are **masked by default**: the amount is only
rendered after the card's eye button (`button[aria-label='Show balance']`) is
pressed, at which point it appears as `.bal-amt` and the button's label flips to
'Hide balance'. Masking-by-default is correct behaviour and
`test_security` asserts it stays that way.

Two traps this page exists to handle:

* **The stat is cached and goes stale.** After a payout the card kept showing
  the pre-transaction figure (₹180.15) while the wallet statement already read
  ₹165.40. Pressing the card's own refresh (`a[aria-label='Refresh']`) or
  reloading brought it to ₹165.40. So `wallet_balance(refresh=True)` is the
  default: comparing a stale card against a live statement invents a
  reconciliation failure that does not exist. Tracked as DEF-OPS-05.
* **Reading before revealing** yields the label text with a bare '₹' and no
  number, which `money()` would happily parse as 0.00 and quietly corrupt a
  ledger. `wallet_balance` reveals first and fails loudly if no figure appears.
"""
from __future__ import annotations

import re
from decimal import Decimal

from config.settings import settings
from core.base_page import BasePage
from partner_ops.data.pricing import money

DASHBOARD_ROUTE = "#/dashboard"

WALLET_BALANCE = "Wallet Balance"
ESCROW_BALANCE = "Total Escrow Balance"
ACTIVE_ESCROWS = "Total Active/Total Excrows"
PAYOUT_SUCCESS_RATE = "Payout Success Rate"


class PartnerDashboardPage(BasePage):
    def open(self) -> None:
        base = settings.partner_base_url.split("#")[0]
        self.goto(base + DASHBOARD_ROUTE)
        self.wait_for_loader()
        self.page.locator(".stat-card").first.wait_for(state="visible",
                                                       timeout=30_000)

    # ---- cards -----------------------------------------------------------
    def card(self, label: str):
        return self.page.locator(".stat-card").filter(
            has=self.page.locator(".stat-label", has_text=re.compile(
                re.escape(label), re.I))).first

    def _eye(self, label: str, action: str = "Show"):
        return self.card(label).locator(
            f"button[aria-label='{action} balance']").first

    def _refresh(self, label: str):
        return self.card(label).locator("a[aria-label='Refresh']").first

    def is_masked(self, label: str = WALLET_BALANCE) -> bool:
        """True while the figure is hidden behind the eye toggle."""
        return self._eye(label, "Show").count() > 0

    def reveal(self, label: str = WALLET_BALANCE) -> None:
        eye = self._eye(label, "Show")
        if eye.count():
            eye.click()
            self.page.wait_for_timeout(800)

    def hide(self, label: str = WALLET_BALANCE) -> None:
        eye = self._eye(label, "Hide")
        if eye.count():
            eye.click()
            self.page.wait_for_timeout(400)

    def refresh_card(self, label: str = WALLET_BALANCE) -> None:
        refresh = self._refresh(label)
        if refresh.count():
            refresh.click()
            self.wait_for_loader()
            self.page.wait_for_timeout(2_500)

    # ---- balances --------------------------------------------------------
    def _amount_in(self, label: str) -> Decimal:
        card = self.card(label)
        card.wait_for(state="visible", timeout=20_000)
        amount = card.locator(".bal-amt").first
        amount.wait_for(state="visible", timeout=15_000)
        text = (amount.text_content() or "").strip()
        if not re.search(r"\d", text):
            raise AssertionError(
                f"{label!r} rendered no figure (got {text!r}). The card is "
                "probably still masked - reveal it before reading, or the "
                "balance API returned nothing.")
        return money(text)

    def wallet_balance(self, *, refresh: bool = True) -> Decimal:
        """The dashboard's wallet figure. Refreshes first by default.

        `refresh=False` deliberately reads the cached value and is what the
        DEF-OPS-05 staleness test uses.
        """
        if refresh:
            self.refresh_card(WALLET_BALANCE)
        self.reveal(WALLET_BALANCE)
        return self._amount_in(WALLET_BALANCE)

    def escrow_balance(self, *, refresh: bool = True) -> Decimal:
        if refresh:
            self.refresh_card(ESCROW_BALANCE)
        self.reveal(ESCROW_BALANCE)
        return self._amount_in(ESCROW_BALANCE)

    def payout_success_rate(self) -> str:
        return self.text_of(self.card(PAYOUT_SUCCESS_RATE))

    def active_escrow_counts(self) -> tuple[int, int]:
        """'6/31' -> (6, 31)."""
        text = self.text_of(self.card(ACTIVE_ESCROWS))
        found = re.search(r"(\d+)\s*/\s*(\d+)", text)
        if not found:
            raise AssertionError(f"could not read active/total from {text!r}")
        return int(found.group(1)), int(found.group(2))

    # ---- wallet links ----------------------------------------------------
    @property
    def statement_link(self):
        return self.card(WALLET_BALANCE).get_by_role(
            "link", name=re.compile(r"statement", re.I)).first

    @property
    def wallet_management_link(self):
        return self.card(WALLET_BALANCE).get_by_role(
            "link", name=re.compile(r"wallet\s*management", re.I)).first

    # ---- top navigation --------------------------------------------------
    def nav_link(self, name: str):
        return self.page.get_by_role("link", name=re.compile(name, re.I)).first

    def nav_targets(self) -> list[str]:
        """Every routed link in the chrome - used by the access-control tests."""
        return self.page.eval_on_selector_all(
            'a[href*="#/"]', "els => [...new Set(els.map(e => e.getAttribute('href')))]")
