"""Orchestrates the admin escrow-creation wizard.

The live wizard has four stages - Project Information, Service Pricing,
Documents, Review - and stage 1 carries the escrow details, the company
representative and the trustee choice together. The page objects stay split
along those three concerns because they are independently meaningful (and
independently testable), while this class gives tests one readable happy path
that returns the generated Escrow ID.
"""
from __future__ import annotations

from playwright.sync_api import Page

from admin.pages.company_rep_page import CompanyRepPage
from admin.pages.document_upload_page import DocumentUploadPage
from admin.pages.escrow_details_page import EscrowDetailsPage
from admin.pages.escrow_nav_page import EscrowNavPage
from admin.pages.review_page import ReviewPage
from admin.pages.service_pricing_page import ServicePricingPage
from admin.pages.trustee_page import TrusteePage


class EscrowCreationFlow:
    def __init__(self, page: Page):
        self.page = page
        # Set once stage 1 is filled; the portal generates it, we only read it.
        self.escrow_id: str = ""
        self.nav = EscrowNavPage(page)
        self.details = EscrowDetailsPage(page)
        self.rep = CompanyRepPage(page)
        self.trustee = TrusteePage(page)
        self.pricing = ServicePricingPage(page)
        self.docs = DocumentUploadPage(page)
        self.review = ReviewPage(page)

    # ---- stages, so a test can start wherever it needs to ---------------
    # Each transition waits for the next stage to render: the wizard swaps
    # stages in place, so without this a caller reads the outgoing stage's DOM.
    def open_new_escrow_form(self, *, from_dashboard: bool = False) -> None:
        """Land on a blank Project Information form (wizard stage 1).

        `from_dashboard=True` skips the sidebar and drives the router straight
        at the escrow list instead. Use it when one session creates several
        escrows in a row: a just-submitted wizard leaves a
        `cdk-overlay-backdrop` behind that swallows the sidebar click, so the
        second create would fail on a click target that is visible but covered.
        """
        if from_dashboard:
            self.nav.goto_escrow_dashboard()
        else:
            self.nav.open_escrow_module()
        self.nav.start_new_escrow()
        self.details.wait_ready()

    def fill_project_information(self, data) -> str:
        """Fill stage 1 without advancing; returns the generated Escrow ID."""
        self.escrow_id = self.details.generated_escrow_id()
        self.details.fill_details(**data.details)
        if data.new_representative:
            # Unregistered address: details are typed, and the portal will email
            # a User ID + temporary password for this new partner.
            self.rep.enter_new_representative(data.new_representative)
        else:
            self.rep.enter_email_and_wait_autofill(data.representative_email)
        self.trustee.set_option(data.trustee_option)
        return self.escrow_id

    def complete_project_information(self, data) -> str:
        escrow_id = self.fill_project_information(data)
        self.details.continue_next()
        self.pricing.wait_ready()
        return escrow_id

    def complete_service_pricing(self, data) -> dict:
        filled = self.pricing.fill_pricing(data.price, data.charge_amount,
                                           **data.pricing)
        self.pricing.continue_next()
        self.docs.wait_ready()
        return filled

    def complete_documents(self, data) -> None:
        self.docs.upload_files(data.documents, with_dates=data.document_dates)
        self.docs.continue_next()
        self.review.wait_ready()

    def create(self, data, *, from_dashboard: bool = False) -> str:
        """Run the whole wizard and return the created escrow's ID."""
        self.open_new_escrow_form(from_dashboard=from_dashboard)
        escrow_id = self.complete_project_information(data)
        self.complete_service_pricing(data)
        self.complete_documents(data)

        # --- stage 4: Review -> submit -> confirm ---
        confirmed = self.review.submit()
        if confirmed and confirmed.upper() != escrow_id.upper():
            raise AssertionError(
                f"Escrow ID mismatch: stage 1 showed {escrow_id!r}, the "
                f"confirmation dialog reported {confirmed!r}"
            )
        self.review.assert_success()
        return escrow_id
