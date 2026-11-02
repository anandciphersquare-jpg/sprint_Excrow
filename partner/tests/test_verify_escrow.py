"""Search the escrow created by admin, open details, Next->Next, verify id.

Escrow ID source (in order): ESCROW_ID env var -> artifact store written by
the admin suite. Skips clearly if neither is available.
"""
import os

import pytest

from partner.pages.escrow_details_page import PartnerEscrowDetailsPage
from partner.pages.escrow_management_page import PartnerEscrowManagementPage


def _resolve_escrow_id(artifact_store) -> str | None:
    return os.getenv("ESCROW_ID") or artifact_store.get("escrow_id")


@pytest.mark.partner
@pytest.mark.e2e
@pytest.mark.regression
def test_search_and_verify_escrow(logged_in_partner_page, partner_monitor, artifact_store):
    escrow_id = _resolve_escrow_id(artifact_store)
    if not escrow_id:
        pytest.skip("No escrow_id. Run the admin create-escrow test first, "
                    "or set ESCROW_ID in the environment.")

    mgmt = PartnerEscrowManagementPage(logged_in_partner_page)
    mgmt.open()
    mgmt.search(escrow_id)
    mgmt.open_view_details(escrow_id)

    details = PartnerEscrowDetailsPage(logged_in_partner_page)
    details.next()
    details.next()

    assert details.contains_escrow_id(escrow_id), \
        f"Escrow ID {escrow_id!r} not shown on the partner details view"
    assert not partner_monitor.page_errors, partner_monitor.summary()
