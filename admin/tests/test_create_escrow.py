"""End-to-end escrow creation.

Each test here drives the whole four-stage wizard and **finishes as soon as the
Escrow ID is created** - the portal has confirmed the record at that point, so
there is nothing further to drive.

Coverage is split three ways:

* the happy path (default payload),
* one create per Account Type and per combination of them - the combination
  decides which Service Pricing sections appear, so each is a different form,
* one create per pricing model x charge frequency.

Every one of these creates a real record in UAT. They carry the `create` marker
so they can be selected or skipped deliberately:

    pytest -m "admin and not create"     # everything that leaves no trace
    pytest -m create                     # only the record-creating paths
"""
import re

import pytest

from admin.data.escrow_data import EscrowData
from admin.pages.escrow_creation_flow import EscrowCreationFlow
from admin.pages.service_pricing_page import FIXED, FREQUENCIES, PERCENTAGE

ESCROW_ID_RE = re.compile(r"^EXC-\d{4}-\d{4,}$", re.I)

ACCOUNT_TYPE_COMBINATIONS = [
    ["Payout"],
    ["UPI Collection"],
    ["VID Collection"],
    ["Payout", "UPI Collection"],
    ["Payout", "VID Collection"],
    ["UPI Collection", "VID Collection"],
    ["Payout", "UPI Collection", "VID Collection"],
]


def create_and_finish(page, data) -> str:
    """Run the wizard once and return the created Escrow ID."""
    flow = EscrowCreationFlow(page)
    escrow_id = flow.create(data)
    assert ESCROW_ID_RE.match(escrow_id), f"unexpected Escrow ID: {escrow_id!r}"
    return escrow_id


# =========================== happy path ==================================

@pytest.mark.admin
@pytest.mark.e2e
@pytest.mark.create
@pytest.mark.regression
def test_create_escrow_end_to_end(flow, escrow_data, admin_monitor, artifact_store):
    """Create an escrow with the default payload and stop at success."""
    escrow_id = flow.create(escrow_data)

    assert ESCROW_ID_RE.match(escrow_id), f"unexpected Escrow ID: {escrow_id!r}"
    artifact_store.set("escrow_id", escrow_id)
    artifact_store.set("escrow_name", escrow_data.name)

    # Spec: no uncaught JS errors during the workflow.
    assert not admin_monitor.page_errors, admin_monitor.summary()
    # Console/network issues are surfaced for review (often benign on UAT):
    if admin_monitor.console_errors or admin_monitor.failed_responses:
        print(admin_monitor.summary())


@pytest.mark.admin
@pytest.mark.e2e
@pytest.mark.create
@pytest.mark.regression
def test_created_escrow_is_listed_on_the_dashboard(flow, escrow_data):
    """Creation is only real if the record is actually listed afterwards."""
    escrow_id = flow.create(escrow_data)
    assert flow.nav.is_listed(escrow_id), \
        f"{escrow_id} was reported created but is not on the dashboard"


# =================== per account type / combination ======================

@pytest.mark.admin
@pytest.mark.create
@pytest.mark.regression
@pytest.mark.parametrize(
    "account_types", ACCOUNT_TYPE_COMBINATIONS,
    ids=["+".join(a.split()[0] for a in combo)
         for combo in ACCOUNT_TYPE_COMBINATIONS],
)
def test_create_escrow_per_account_type(logged_in_admin_page, account_types):
    """Every account type, alone and combined, must produce an escrow.

    Each combination renders a different Service Pricing form, so this is the
    test that proves the pricing stage adapts.
    """
    data = EscrowData(account_types=account_types)
    escrow_id = create_and_finish(logged_in_admin_page, data)
    assert escrow_id


# ======================== per pricing variant ============================

@pytest.mark.admin
@pytest.mark.create
@pytest.mark.regression
@pytest.mark.parametrize("rate_type", [FIXED, PERCENTAGE])
@pytest.mark.parametrize("frequency", FREQUENCIES)
def test_create_escrow_per_pricing_model(logged_in_admin_page, rate_type, frequency):
    """Fixed and Percentage charging, across each maintenance frequency."""
    data = EscrowData(rate_type=rate_type, charge_frequency=frequency)
    escrow_id = create_and_finish(logged_in_admin_page, data)
    assert escrow_id


# ===================== no browser / no records ===========================

@pytest.mark.admin
def test_random_payloads_are_unique_per_instance():
    """Each create gets its own name/account/IFSC. No browser needed."""
    a, b = EscrowData(), EscrowData()
    assert a.name != b.name
    assert a.account_number != b.account_number
    assert a.ifsc != b.ifsc
    # Stable within one instance - a test must be able to assert afterwards on
    # the same values it typed.
    assert a.details == a.details


@pytest.mark.admin
@pytest.mark.regression
def test_generated_escrow_ids_are_unique_per_form(flow):
    """Two fresh forms must be issued two different IDs (nothing submitted)."""
    flow.open_new_escrow_form()
    first = flow.details.generated_escrow_id()
    flow.nav.goto_escrow_dashboard()
    flow.nav.start_new_escrow()
    flow.details.wait_ready()
    second = flow.details.generated_escrow_id()
    assert first and second and first != second, \
        f"the portal reissued the same Escrow ID: {first!r}"
