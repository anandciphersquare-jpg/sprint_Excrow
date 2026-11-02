"""Wizard stage 3 (Documents) - positive and negative coverage.

Documents are added one at a time through an "Add New Document" modal that
needs a file and a name; Signing / Effective / Expiry dates are optional. At
least one saved document is required to leave the stage.
"""
import re

import pytest
from playwright.sync_api import expect

from core.file_factory import (ensure_sample_documents, oversize_sample,
                               unsupported_sample)

SAMPLES = ensure_sample_documents()          # [pdf, png, jpg]


@pytest.fixture
def docs(documents_stage):
    return documents_stage.docs


# =========================== positive ====================================

@pytest.mark.admin
@pytest.mark.regression
def test_stage_starts_empty(docs):
    assert docs.is_empty()
    assert docs.uploaded_count() == 0
    assert docs.next_button.is_disabled()


@pytest.mark.admin
@pytest.mark.regression
def test_add_document_with_all_optional_dates(docs):
    """Signing / Effective / Expiry are optional, but must work when used."""
    dates = docs.add_document(SAMPLES[0], name="WithDates", with_dates=True)
    assert all(dates.values()), f"a date did not register: {dates}"
    assert docs.uploaded_names() == ["WithDates"]
    card = docs.document_card("WithDates").inner_text()
    for label in ("Added on", "Signing Date", "Effective Date", "Expiry Date"):
        assert label in card
    # The dates chosen must actually be shown on the card, not left blank.
    populated = re.findall(r"[A-Z][a-z]{2} \d{1,2}, \d{4}", card)
    assert len(populated) >= 4, f"card is missing date values: {card!r}"
    assert docs.next_is_enabled()


@pytest.mark.admin
@pytest.mark.regression
def test_add_document_without_dates(docs):
    """Dates are optional: a file and a name are enough to save and continue."""
    docs.add_document(SAMPLES[0], name="NoDates", with_dates=False)
    assert docs.uploaded_count() == 1
    assert docs.next_is_enabled()


@pytest.mark.admin
@pytest.mark.regression
@pytest.mark.parametrize("sample", SAMPLES, ids=["pdf", "png", "jpg"])
def test_each_supported_format_is_accepted(docs, sample):
    docs.add_document(sample, name=f"Fmt{sample[-3:].upper()}", with_dates=False)
    assert docs.uploaded_count() == 1


@pytest.mark.admin
@pytest.mark.regression
def test_multiple_documents_accumulate(docs):
    added = docs.upload_files(SAMPLES, with_dates=False)
    assert docs.uploaded_count() == added == len(SAMPLES)
    assert docs.view_links().count() == len(SAMPLES), \
        "each saved document should be viewable"


@pytest.mark.admin
@pytest.mark.regression
def test_delete_document_removes_it(docs):
    docs.upload_files(SAMPLES[:2], with_dates=False)
    assert docs.uploaded_count() == 2
    docs.remove_first()
    assert docs.uploaded_count() == 1


@pytest.mark.admin
@pytest.mark.regression
def test_deleting_the_last_document_reblocks_next(docs):
    docs.add_document(SAMPLES[0], name="Solo", with_dates=False)
    assert docs.next_is_enabled()
    docs.remove_first()
    assert docs.uploaded_count() == 0
    assert docs.next_button.is_disabled(), \
        "with no documents left the stage must not be completable"


@pytest.mark.admin
@pytest.mark.regression
def test_cancel_modal_adds_nothing(docs):
    docs.open_modal()
    docs.file_input.set_input_files(SAMPLES[0])
    docs.fill(docs.document_name_input, "Discarded")
    docs.click(docs.cancel_button)
    docs.page.wait_for_timeout(1_500)
    assert docs.uploaded_count() == 0
    assert docs.is_empty()


# =========================== negative ====================================

@pytest.mark.admin
@pytest.mark.negative
def test_zero_documents_blocks_progress(docs):
    assert docs.next_button.is_disabled()
    assert docs.next_button.get_attribute("title") == \
        "Please complete all required fields"


@pytest.mark.admin
@pytest.mark.negative
def test_save_requires_a_file(docs):
    docs.open_modal()
    docs.fill(docs.document_name_input, "NameOnly")
    assert docs.save_document_button.is_disabled(), \
        "a name without a file must not be saveable"


@pytest.mark.admin
@pytest.mark.negative
def test_save_requires_a_name(docs):
    docs.open_modal()
    docs.file_input.set_input_files(SAMPLES[0])
    docs.page.wait_for_timeout(1_000)
    assert docs.save_document_button.is_disabled(), \
        "a file without a name must not be saveable"


@pytest.mark.admin
@pytest.mark.negative
@pytest.mark.xfail(reason="UAT accepts unsupported types: the modal advertises "
                          "PDF/JPG/JPEG/PNG/DOC/DOCX but a .txt saves without "
                          "any client- or server-side complaint",
                  strict=False)
def test_unsupported_file_type_is_rejected(docs):
    docs.open_modal()
    docs.file_input.set_input_files(unsupported_sample())
    docs.fill(docs.document_name_input, "UnsupportedTxt")
    docs.page.wait_for_timeout(2_000)
    if not docs.save_document_button.is_disabled():
        docs.click(docs.save_document_button)
        docs.page.wait_for_timeout(5_000)
        assert docs.uploaded_count() == 0, \
            "an unsupported file type was saved as a document"
    assert docs.save_document_button.is_disabled()


@pytest.mark.admin
@pytest.mark.negative
@pytest.mark.xfail(reason="UAT enforces no size cap: a 12MB upload saves "
                          "successfully",
                  strict=False)
def test_oversize_file_is_rejected(docs):
    """Save is briefly disabled while the file is read, so wait it out.

    A fixed short sleep here races the file read and makes this test flap
    between xfail and xpass; waiting explicitly for the button to settle is
    what makes the outcome mean something.
    """
    docs.open_modal()
    docs.file_input.set_input_files(oversize_sample(12))
    docs.fill(docs.document_name_input, "Oversize12MB")
    try:
        expect(docs.save_document_button).to_be_enabled(timeout=45_000)
    except AssertionError:
        return          # never became saveable - the size was rejected
    docs.click(docs.save_document_button)
    docs.page.wait_for_timeout(10_000)
    assert docs.uploaded_count() == 0, "an oversize file was saved"


@pytest.mark.admin
@pytest.mark.regression
def test_cancelling_a_delete_keeps_the_document(docs):
    docs.add_document(SAMPLES[0], name="Kept", with_dates=False)
    docs.cancel_remove_first()
    assert docs.uploaded_names() == ["Kept"]
    assert docs.next_is_enabled()
