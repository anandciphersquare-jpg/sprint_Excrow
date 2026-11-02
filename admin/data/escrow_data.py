"""Test data for the admin escrow-creation flow.

Escrow name, account number and IFSC are randomised per `EscrowData()` so
every create is a fresh record and repeat runs never collide with the
"existing escrow name / account" rules. They are generated **once at
construction** (not per attribute read) so a test can assert afterwards on the
same values that were typed.

Values that are not ours to invent - industry, bank, account type - use real
options read off the live portal. The company representative is deliberately an
email that already exists as a partner, because the portal then auto-fills
name/company/mobile as readonly fields.
"""
from __future__ import annotations

import random
import string
from dataclasses import dataclass, field
from typing import List

from config.settings import settings
from core.file_factory import ensure_sample_documents

# IFSC is 4 letters + '0' + 6 alphanumerics; the field caps at 11 chars.
BANK_IFSC_PREFIX = {"IDFC": "IDFB", "NSDL": "NSPB"}


def random_escrow_name(prefix: str = "AutoEsc") -> str:
    """Unique, human-recognisable name (letters/digits only)."""
    suffix = "".join(random.choices(string.ascii_uppercase + string.digits, k=6))
    return f"{prefix}{suffix}"


def random_account_number(digits: int = 12) -> str:
    return "".join(random.choices(string.digits, k=digits))


def random_ifsc(bank: str = "IDFC") -> str:
    prefix = BANK_IFSC_PREFIX.get(bank.upper(), "IDFB")
    return f"{prefix}0{''.join(random.choices(string.digits, k=6))}"


def alias_email(tag: str, base: str = "") -> str:
    """`anand.kumar@x.in` + tag 'new2' -> `anand.kumar+new2@x.in`.

    Sub-addressing gives each onboarding run a distinct, never-registered
    address that still lands in the one real inbox - so the welcome email and
    its temporary password can actually be read.
    """
    local, _, domain = (base or settings.representative_email).partition("@")
    return f"{local.split('+')[0]}+{tag}@{domain}"


def unique_alias_tag(prefix: str = "new") -> str:
    """A tag unlikely to collide with an alias already onboarded."""
    return f"{prefix}{random.randint(1000, 9999)}"


def random_company_name(prefix: str = "QA") -> str:
    return f"{prefix}{''.join(random.choices(string.ascii_uppercase, k=5))} Ltd"


def random_person_name() -> tuple[str, str]:
    tag = "".join(random.choices(string.ascii_uppercase, k=4))
    return f"Qa{tag.capitalize()}", f"Test{tag}"


def random_mobile() -> str:
    """A 10-digit Indian mobile (leading 6-9)."""
    return random.choice("6789") + "".join(random.choices(string.digits, k=9))


@dataclass
class NewRepresentative:
    """Details typed by hand for an address with no partner record yet.

    **Every identifying field must be unique per run** - email, company, name
    *and* mobile. Reusing any of them (the mobile especially) gets the escrow
    matched to that existing partner instead of creating a new one: the portal
    then sends the existing-partner mail, with no User ID or temporary
    password, greeting the *other* partner's company name.
    """
    email: str
    first_name: str = ""
    last_name: str = ""
    company_name: str = field(default_factory=random_company_name)
    mobile: str = field(default_factory=random_mobile)

    def __post_init__(self):
        if not self.first_name or not self.last_name:
            first, last = random_person_name()
            self.first_name = self.first_name or first
            self.last_name = self.last_name or last

    @classmethod
    def with_tag(cls, tag: str, **kwargs) -> "NewRepresentative":
        return cls(email=alias_email(tag), **kwargs)


@dataclass
class EscrowData:
    # --- randomised per create ---
    name: str = field(default_factory=random_escrow_name)
    account_number: str = field(default_factory=random_account_number)
    ifsc: str = field(default_factory=random_ifsc)

    # --- fixed, real portal options ---
    industry: str = "Real Estate"
    bank: str = "IDFC"
    # Account Type is a multiselect, and the combination chosen decides which
    # Service Pricing sections appear: Payout -> IMPS/NEFT/RTGS,
    # VID Collection -> Collection via VID + VID Creation.
    account_types: List[str] = field(
        default_factory=lambda: ["Payout", "VID Collection"])

    # --- representative ---
    # Default: an existing partner, so the portal auto-fills the rest readonly.
    # Set `new_representative` to onboard a fresh address instead - then the
    # details are typed in and the portal emails a temporary password.
    representative_email: str = settings.representative_email
    new_representative: "NewRepresentative | None" = None

    # --- trustee: key from admin.pages.trustee_page.OPTIONS ---
    trustee_option: str = "not_required"

    # --- service pricing ---
    rate_type: str = "Percentage"     # "Fixed" or "Percentage"
    charge_frequency: str = "Monthly"  # Monthly | Quarterly | Annually
    price: str = "1"                  # Fixed: flat / per-slab charge
    percent: str = "1.5"              # Percentage: the rate itself
    min_charge: str = "5"             # Percentage: floor
    max_charge: str = "50"            # Percentage: cap
    charge_amount: str = "100"        # Bank & TSP maintenance amount

    documents: List[str] = field(default_factory=ensure_sample_documents)
    # Signing / Effective / Expiry are optional per document - filled anyway so
    # the happy path covers them.
    document_dates: bool = True

    @property
    def rep_email(self) -> str:
        """The address that will actually be entered on the form."""
        return (self.new_representative.email if self.new_representative
                else self.representative_email)

    @property
    def details(self) -> dict:
        """Stage-1 escrow fields, as `EscrowDetailsPage.fill_details` takes them."""
        return {
            "name": self.name,
            "industry": self.industry,
            "bank": self.bank,
            "account_types": list(self.account_types),
            "account_number": self.account_number,
            "ifsc": self.ifsc,
        }

    @property
    def pricing(self) -> dict:
        """Stage-2 fields, as `ServicePricingPage.fill_pricing` takes them."""
        return {
            "rate_type": self.rate_type,
            "frequency": self.charge_frequency,
            "percent": self.percent,
            "min_charge": self.min_charge,
            "max_charge": self.max_charge,
        }


# Reusable payloads for negative tests (stage-1 validation checklist).
INVALID_IFSC = "12345"                 # too short / wrong pattern
INVALID_ACCOUNT_NUMBER = "12ab!@"      # non-numeric / special chars
XSS_PAYLOAD = "<script>alert('xss')</script>"
SQLI_PAYLOAD = "' OR '1'='1"
