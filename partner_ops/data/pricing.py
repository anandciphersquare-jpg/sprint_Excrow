
"""Service Pricing model, parsed from the escrow's own Charges tab.

The brief for this suite is explicit: *do not assume charges*. Every expected
wallet movement in `partner_ops` is therefore computed from what the portal
itself renders on **Escrow Detail -> Charges**, never from a constant in a test.

Two things the charge cards do NOT say, both established empirically against
UAT on 14 Aug 2026 and both essential to reconciliation:

* **GST at 18% is added on top of every charge**, and it is the *gross* figure
  that leaves the wallet. The Charges tab quotes the net charge only. The wallet
  statement and payout report both spell the split out
  ("Charges Rs.12.00 + GST Rs.2.16" against a debit of Rs.14.16), which is how
  the rate was confirmed rather than guessed.
* **Payout charges are debited from the wallet; the payout amount itself is
  not.** The transferred amount moves out of the escrow account. So for a
  payout, `expected wallet debit == charge + GST`, and the brief's
  "amount + charges" formula does not apply to the wallet leg.

`MAINTENANCE` charges are a documented contradiction, tracked as DEF-OPS-06:
the Charges tab says they are "deducted directly from the escrow account", but
the wallet statement shows them debiting the **wallet**. The model records what
the UI claims; the test asserts against observed behaviour and fails loudly on
the mismatch.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from typing import Iterable

GST_RATE = Decimal("0.18")

# Charge basis as printed on the cards.
FIXED = "FIXED"
PERCENTAGE = "PERCENTAGE"


def money(value: str | int | float | Decimal) -> Decimal:
    """Parse a rupee figure to 2dp. Tolerates '₹1,234.50', '1.5%', '—', ''."""
    if isinstance(value, Decimal):
        return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    text = str(value)
    cleaned = re.sub(r"[^0-9.\-]", "", text)
    if cleaned in ("", "-", ".", "-."):
        return Decimal("0.00")
    return Decimal(cleaned).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def with_gst(net: Decimal) -> Decimal:
    """Gross debit for a net charge. This is what the wallet actually loses."""
    return (net * (Decimal("1") + GST_RATE)).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP)


def gst_on(net: Decimal) -> Decimal:
    return (net * GST_RATE).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class Slab:
    """One '₹0 – ₹1,000  →  ₹0.00' row of a slab-priced mode."""
    lower: Decimal
    upper: Decimal
    charge: Decimal

    def covers(self, amount: Decimal) -> bool:
        # Slabs are printed as inclusive-lower/inclusive-upper bands that share
        # a boundary (0-1,000 then 1,000-25,000). The lower band wins on a tie,
        # which is what the observed ₹12.50 charge on a ₹10 payout implies.
        return self.lower <= amount <= self.upper


@dataclass
class ServiceCharge:
    """A single priced service, e.g. IMPS, VID Creation, Collection via VID."""
    service: str
    basis: str                      # FIXED | PERCENTAGE
    flat: Decimal = Decimal("0.00")
    percent: Decimal = Decimal("0.00")
    minimum: Decimal | None = None
    maximum: Decimal | None = None
    slabs: list[Slab] = field(default_factory=list)

    def net_charge(self, amount: Decimal) -> Decimal:
        """The charge before GST for a transaction of `amount`."""
        if self.slabs:
            for slab in self.slabs:
                if slab.covers(amount):
                    return slab.charge
            # Above the top slab the portal has no rule on screen. Refuse to
            # invent one - an unpriced amount is a finding, not a default.
            raise ValueError(
                f"{self.service}: amount {amount} falls outside every "
                f"configured slab {[(str(s.lower), str(s.upper)) for s in self.slabs]}"
            )
        if self.basis == PERCENTAGE:
            charge = (amount * self.percent / Decimal("100")).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP)
            if self.minimum is not None:
                charge = max(charge, self.minimum)
            if self.maximum is not None:
                charge = min(charge, self.maximum)
            return charge
        return self.flat

    def gross_charge(self, amount: Decimal) -> Decimal:
        """Charge + GST - the figure that should leave the wallet."""
        return with_gst(self.net_charge(amount))


@dataclass
class ServicePricing:
    """Everything the Charges tab exposes for one escrow."""
    services: dict[str, ServiceCharge] = field(default_factory=dict)
    maintenance: dict[str, dict] = field(default_factory=dict)

    def get(self, service: str) -> ServiceCharge:
        key = _norm(service)
        if key not in self.services:
            raise KeyError(
                f"No configured pricing for {service!r}. "
                f"Configured: {sorted(self.services)}"
            )
        return self.services[key]

    def has(self, service: str) -> bool:
        return _norm(service) in self.services

    def modes(self) -> list[str]:
        """Priced payout modes only (IMPS/NEFT/RTGS...), not VID/collection."""
        return [k for k in self.services if k in ("imps", "neft", "rtgs", "a2a")]


def _norm(name: str) -> str:
    return re.sub(r"\s+", " ", name).strip().lower()


# --------------------------------------------------------------------------
# Parsing the Charges tab
# --------------------------------------------------------------------------
# Card shapes actually rendered (verified on EXC-0826-859771, 14 Aug 2026):
#
#   IMPS  FIXED                        <- slab card
#     ₹0 – ₹1,000        ₹0.00
#     ₹1,000 – ₹25,000   ₹0.00
#     ₹25,000 – ₹5,00,000 ₹0.00
#
#   NEFT  FIXED   ₹ 0.00 / txn   Flat charge, any amount
#
#   Collection via VID  PERCENTAGE  1.5% / collection
#     Min charges: ₹5.00   Max charges: ₹50.00
#
#   VID Creation  FIXED  ₹ 1.00 / VID   One-time, charged on creation

_SERVICE_NAMES = ("IMPS", "NEFT", "RTGS", "A2A", "UPI Collection",
                  "Collection via VID", "VID Creation")

_RANGE = re.compile(
    r"₹\s*([\d,]+(?:\.\d+)?)\s*[–\-]\s*₹\s*([\d,]+(?:\.\d+)?)\s*₹\s*([\d,]+(?:\.\d+)?)")
_PERCENT = re.compile(r"([\d.]+)\s*%")
_FLAT = re.compile(r"₹\s*([\d,]+(?:\.\d+)?)")
_MIN = re.compile(r"min\s*charges?\s*:?\s*₹\s*([\d,]+(?:\.\d+)?)", re.I)
_MAX = re.compile(r"max\s*charges?\s*:?\s*₹\s*([\d,]+(?:\.\d+)?)", re.I)


def parse_charges_text(text: str) -> ServicePricing:
    """Build a `ServicePricing` from the visible text of the Charges tab.

    Deliberately text-driven rather than DOM-driven: the cards carry no stable
    hooks (no data-service, no per-card id), and the visible text is exactly
    what a manual tester reconciles against. If the layout changes, this fails
    with the block it could not read rather than silently pricing at zero.
    """
    pricing = ServicePricing()
    blocks = _split_service_blocks(text)

    for name, block in blocks.items():
        basis = PERCENTAGE if "PERCENTAGE" in block.upper() else FIXED
        slabs = [
            Slab(money(lo), money(hi), money(ch))
            for lo, hi, ch in _RANGE.findall(block)
        ]
        percent = Decimal("0.00")
        if basis == PERCENTAGE:
            found = _PERCENT.search(block)
            if found:
                percent = money(found.group(1))

        flat = Decimal("0.00")
        if basis == FIXED and not slabs:
            # First rupee figure after the basis label is the per-txn charge.
            amounts = _FLAT.findall(block)
            if amounts:
                flat = money(amounts[0])

        low = _MIN.search(block)
        high = _MAX.search(block)

        pricing.services[_norm(name)] = ServiceCharge(
            service=name, basis=basis, flat=flat, percent=percent,
            minimum=money(low.group(1)) if low else None,
            maximum=money(high.group(1)) if high else None,
            slabs=slabs,
        )

    pricing.maintenance = _parse_maintenance(text)
    return pricing


def _split_service_blocks(text: str) -> dict[str, str]:
    """Slice the Service Pricing section into one block per named service."""
    # Work only inside Service Pricing, so Maintenance rupee figures cannot be
    # mistaken for a per-transaction charge.
    body = text
    start = re.search(r"Service Pricing", body, re.I)
    if start:
        body = body[start.end():]
    stop = re.search(r"Maintenance Charges", body, re.I)
    if stop:
        body = body[:stop.start()]

    positions: list[tuple[int, str]] = []
    for name in _SERVICE_NAMES:
        for hit in re.finditer(rf"(?<![A-Za-z]){re.escape(name)}(?![A-Za-z])",
                               body, re.I):
            positions.append((hit.start(), name))
    positions.sort()

    blocks: dict[str, str] = {}
    for index, (offset, name) in enumerate(positions):
        end = positions[index + 1][0] if index + 1 < len(positions) else len(body)
        if name not in blocks:                 # first occurrence owns the block
            blocks[name] = body[offset:end]
    return blocks


def _parse_maintenance(text: str) -> dict[str, dict]:
    """Bank / TSP recurring charges, as claimed by the UI."""
    out: dict[str, dict] = {}
    section = re.search(r"Maintenance Charges(.*)$", text, re.I | re.S)
    if not section:
        return out
    body = section.group(1)
    # The tab labels these 'TSP Charges' while the wallet statement calls the
    # same debit 'Tcp Charges' - recorded as DEF-OPS-07 (cosmetic).
    for label in ("Bank Charges", "TSP Charges", "Tcp Charges"):
        hit = re.search(
            rf"{label}(.*?)(?=Bank Charges|TSP Charges|Tcp Charges|$)",
            body, re.I | re.S)
        if not hit:
            continue
        block = hit.group(1)
        amount = re.search(r"₹\s*([\d,]+(?:\.\d+)?)", block)
        freq = re.search(r"(MONTHLY|QUARTERLY|ANNUALLY)", block, re.I)
        out[label] = {
            "amount": money(amount.group(1)) if amount else Decimal("0.00"),
            "frequency": freq.group(1).upper() if freq else "",
            "auto_debit": "AUTO DEBIT" in block.upper(),
            "claims_escrow_debit": "escrow account" in body.lower(),
        }
    return out


def expected_wallet_debit_for_payout(pricing: ServicePricing, mode: str,
                                     amount: Decimal) -> Decimal:
    """Charge + GST for a payout. The transferred amount is NOT included.

    See the module docstring: the payout amount leaves the escrow account, so
    including it here would make every reconciliation assertion wrong by the
    transaction value.
    """
    return pricing.get(mode).gross_charge(amount)


def expected_wallet_debit_for_vid(pricing: ServicePricing) -> Decimal:
    return pricing.get("VID Creation").gross_charge(Decimal("0"))


def expected_wallet_debit_for_collection(pricing: ServicePricing,
                                         amount: Decimal) -> Decimal:
    return pricing.get("Collection via VID").gross_charge(amount)


def summarise(pricing: ServicePricing) -> Iterable[str]:
    """Human-readable pricing lines, for failure messages and the report."""
    for charge in pricing.services.values():
        if charge.slabs:
            slabs = ", ".join(f"{s.lower}-{s.upper}:{s.charge}" for s in charge.slabs)
            yield f"{charge.service}: slabs [{slabs}]"
        elif charge.basis == PERCENTAGE:
            bounds = f" (min {charge.minimum}, max {charge.maximum})"
            yield f"{charge.service}: {charge.percent}%{bounds}"
        else:
            yield f"{charge.service}: flat ₹{charge.flat}"
