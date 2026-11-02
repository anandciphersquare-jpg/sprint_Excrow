"""Wizard stage 4 (Review) - what the summary shows, and the two-step submit.

Submitting is deliberately two actions: the page's "Submit Application" opens
an "Are You Sure?" dialog that echoes the Escrow ID, and only that dialog's
Submit creates the record. Cancelling there must leave nothing behind.
"""
import re

import pytest

ESCROW_ID_RE = re.compile(r"EXC-\d{4}-\d{4,}", re.I)


@pytest.fixture
def review(review_stage):
    return review_stage.review


# =========================== positive ====================================

@pytest.mark.admin
@pytest.mark.regression
def test_review_shows_what_was_entered(review_stage, escrow_data):
    """The summary is the last chance to catch a mistake - it must be faithful.

    Note the Escrow ID is *not* repeated here; it only reappears in the
    confirmation dialog (see `test_confirmation_dialog_echoes_the_escrow_id`).
    """
    body = review_stage.page.inner_text("body")
    assert escrow_data.name in body
    assert escrow_data.account_number in body
    assert escrow_data.ifsc in body
    # representative details came from the partner lookup, not from us
    assert escrow_data.representative_email.lower() in body.lower()
    for section in ("Project Information", "Service Pricing", "Documents"):
        assert section in body


@pytest.mark.admin
@pytest.mark.regression
def test_review_lists_the_uploaded_documents(review_stage, escrow_data):
    body = review_stage.page.inner_text("body")
    from pathlib import Path
    for index, path in enumerate(escrow_data.documents, start=1):
        assert f"{Path(path).stem}_{index}" in body


@pytest.mark.admin
@pytest.mark.regression
def test_confirmation_dialog_echoes_the_escrow_id(review_stage, review):
    """Stop at the dialog: it must quote the same ID stage 1 showed."""
    confirmed = review.submit(confirm=False)
    assert confirmed == review_stage.escrow_id, (
        f"dialog quoted {confirmed!r} but the form showed "
        f"{review_stage.escrow_id!r}"
    )
    review.cancel_submit()


@pytest.mark.admin
@pytest.mark.regression
def test_every_review_section_offers_edit(review):
    """Each summarised section has its own Edit affordance.

    It is an `<a>` with no href, so it carries no `link` role and a
    `get_by_role("button", name="Edit")` lookup finds nothing - the reason to
    match the markup instead.
    """
    assert review.edit_links().count() == 3, \
        f"expected one Edit per section, sections are {review.sections()}"
    for title in ("Project Information", "Service", "Documents"):
        assert any(title.lower() in s.lower() for s in review.sections()), \
            f"{title!r} section missing from {review.sections()}"


@pytest.mark.admin
@pytest.mark.regression
def test_edit_a_reviewed_value_and_carry_it_forward(review_stage, escrow_data):
    """Edit from Review, change something, and walk back to Review.

    The edited value must be what the summary then shows - otherwise a
    correction made at the last moment would be silently dropped.
    """
    page = review_stage.page
    review = review_stage.review
    details = review_stage.details

    review.edit_section("Project Information")
    details.wait_ready()
    assert details.escrow_name_input.input_value() == escrow_data.name, \
        "Edit should reopen the form with what was already entered"

    corrected = f"{escrow_data.name}EDIT"
    details.fill(details.escrow_name_input, corrected)
    details.escrow_name_input.press("Tab")

    # Forward through the remaining stages - the earlier answers are retained,
    # so each stage should already be complete.
    details.continue_next()
    review_stage.pricing.wait_ready()
    review_stage.pricing.continue_next()
    review_stage.docs.wait_ready()
    assert review_stage.docs.uploaded_count() == len(escrow_data.documents), \
        "documents must survive an edit round-trip"
    review_stage.docs.continue_next()
    review.wait_ready()

    body = page.inner_text("body")
    assert corrected in body, "the edited name is missing from the summary"
    assert escrow_data.name not in body.replace(corrected, ""), \
        "the summary still shows the pre-edit name"


@pytest.mark.admin
@pytest.mark.regression
def test_previous_returns_to_documents_with_them_intact(review_stage, escrow_data):
    """You can also step back a stage from Review without losing work."""
    review_stage.review.previous_button.click()
    review_stage.page.wait_for_timeout(3_000)
    docs = review_stage.docs
    docs.wait_ready()
    assert docs.uploaded_count() == len(escrow_data.documents), \
        "documents must survive a trip back from Review"


# =========================== negative ====================================

@pytest.mark.admin
@pytest.mark.negative
def test_cancelling_the_confirmation_creates_nothing(review_stage, review):
    """The escrow must not exist after backing out of the confirmation."""
    escrow_id = review_stage.escrow_id
    review.submit(confirm=False)
    review.cancel_submit()
    review_stage.page.wait_for_timeout(2_000)

    assert not review.confirm_dialog.is_visible(), "dialog should be dismissed"
    assert "PROJECT INFORMATION" in review_stage.page.inner_text("body").upper(), \
        "cancelling should leave us on Review, able to submit later"
    assert not review_stage.nav.is_listed(escrow_id, timeout=10_000), \
        f"{escrow_id} was created despite cancelling the confirmation"


@pytest.mark.admin
@pytest.mark.negative
def test_review_is_not_reachable_with_an_incomplete_wizard(documents_stage):
    """Skipping the documents stage must not reach Review."""
    assert documents_stage.docs.next_button.is_disabled()
    body = documents_stage.page.inner_text("body").upper()
    assert "SUBMIT APPLICATION" not in body
