"""Editing a **submitted** escrow from the dashboard list.

Distinct from the Review-stage edit in `test_review_submit.py`, which walks back
into a wizard that has not been submitted yet. This is the other half: the
record exists, it is listed, and the only way back into it is the list's row
action.

Calibrated against the live list. Two things cost a debugging cycle:

* **The row action is an `<i class="icon-edit">`** - not an anchor, not a
  button. It carries no `link` or `button` role, so
  `get_by_role("button", name="Edit")` finds nothing and a test asserting "there
  is no edit control" passes for the wrong reason. Exactly the trap the Review
  stage sets. `test_the_row_action_carries_no_aria_role` pins it so a future
  refactor to a real button is noticed rather than silently weakening the suite.
* **There is no separate edit screen.** The icon routes to
  `#/excrow/add-excrow/<ESCROW-ID>` - the same four-stage wizard, reopened on
  **Review** with every section summarised and its own Edit link.

These tests **read** an existing record. None of them saves, because doing so
would mutate a real UAT escrow; the reopened wizard is verified and abandoned.
"""
from __future__ import annotations

import re

import pytest

from admin.pages.escrow_nav_page import EscrowNavPage
from admin.pages.review_page import ReviewPage


@pytest.fixture
def escrow_list(logged_in_admin_page) -> EscrowNavPage:
    """The escrow dashboard, with at least one record listed."""
    nav = EscrowNavPage(logged_in_admin_page)
    nav.open_escrow_module()
    if not nav.listed_escrow_ids():
        pytest.skip("no escrow is listed on the dashboard - create one first "
                    "(pytest -m 'admin and create')")
    return nav


@pytest.fixture
def listed_id(escrow_list) -> str:
    """The newest listed escrow. The list is newest-first."""
    return escrow_list.listed_escrow_ids()[0]


# =========================== the list ====================================

@pytest.mark.admin
@pytest.mark.smoke
def test_the_list_offers_an_action_column(escrow_list):
    headers = escrow_list.column_headers()
    assert headers == list(escrow_list.COLUMNS), \
        f"the dashboard columns changed: {headers}"
    assert headers[-1] == "Action", "an escrow must be reachable from its row"


@pytest.mark.admin
@pytest.mark.regression
def test_the_row_shows_the_escrow_it_belongs_to(escrow_list, listed_id):
    assert escrow_list.row_value(listed_id, "Excrow Id") == listed_id
    assert escrow_list.row_value(listed_id, "Excrow Name"), \
        "a row with no name is not identifiable by a human"


@pytest.mark.admin
@pytest.mark.regression
def test_every_listed_escrow_has_exactly_one_edit_affordance(escrow_list):
    """One row action, not zero and not several."""
    for escrow_id in escrow_list.listed_escrow_ids()[:5]:
        icons = escrow_list.escrow_row(escrow_id).locator("i.icon-edit")
        assert icons.count() == 1, \
            f"{escrow_id} has {icons.count()} edit icons, expected 1"
        assert icons.first.is_visible()


@pytest.mark.admin
@pytest.mark.negative
def test_the_row_action_carries_no_aria_role(escrow_list, listed_id):
    """Pins the trap rather than working around it silently.

    The icon is an `<i>`, so a role-based lookup finds nothing. If this ever
    starts failing the markup has been improved to a real button - at which
    point `row_edit_icon` should be simplified, and this test deleted rather
    than adjusted.
    """
    row = escrow_list.escrow_row(listed_id)
    assert row.get_by_role("button", name=re.compile("edit", re.I)).count() == 0
    assert row.get_by_role("link", name=re.compile("edit", re.I)).count() == 0

    icon = escrow_list.row_edit_icon(listed_id)
    assert icon.evaluate("e => e.tagName").lower() == "i", \
        "the row action is no longer an <i>; revisit row_edit_icon()"


# =========================== opening it ==================================

@pytest.mark.admin
@pytest.mark.regression
def test_the_row_action_reopens_that_escrow(escrow_list, listed_id):
    """The icon routes to the wizard for *this* record, not a blank one."""
    escrow_list.open_escrow_for_edit(listed_id)
    assert listed_id in escrow_list.page.url, \
        f"edit opened {escrow_list.page.url}, expected it to carry {listed_id}"
    assert re.search(r"add-e[sx]crow", escrow_list.page.url, re.I), \
        "edit should reopen the create wizard, not a separate screen"


@pytest.mark.admin
@pytest.mark.regression
def test_the_reopened_wizard_lands_on_review_with_its_values(escrow_list, listed_id):
    """A submitted escrow reopens summarised, not as an empty form."""
    name_in_list = escrow_list.row_value(listed_id, "Excrow Name")
    bank_in_list = escrow_list.row_value(listed_id, "Bank Name")
    account_in_list = escrow_list.row_value(listed_id, "Bank Acc Number")

    escrow_list.open_escrow_for_edit(listed_id)
    review = ReviewPage(escrow_list.page)
    body = review.body_text()

    assert re.search(r"project\s*information", body, re.I), \
        "the reopened wizard is not showing the Review summary"
    for label, value in (("name", name_in_list), ("bank", bank_in_list),
                         ("account number", account_in_list)):
        if value:
            assert value in body, \
                f"the {label} from the list ({value!r}) is missing from the " \
                "reopened wizard - the row and the record disagree"


@pytest.mark.admin
@pytest.mark.regression
def test_each_section_is_editable_after_submission(escrow_list, listed_id):
    """A submitted record is not read-only: every section still offers Edit.

    Uses the markup match, not a role lookup - `a:has(.icon-edit)` - for the
    reason in the module docstring.
    """
    escrow_list.open_escrow_for_edit(listed_id)
    review = ReviewPage(escrow_list.page)
    sections = review.sections()
    assert review.edit_links().count() == 3, \
        f"a submitted escrow should offer one Edit per section; sections are " \
        f"{sections}"
    joined = " | ".join(sections).lower()
    for expected in ("project information", "documents", "service"):
        assert expected in joined, \
            f"{expected!r} section is missing from the reopened wizard: {sections}"


@pytest.mark.admin
@pytest.mark.negative
def test_a_tampered_escrow_id_does_not_open_someone_elses_record(escrow_list,
                                                                 listed_id):
    """Hand-editing the id in the URL must not surface another escrow.

    The route takes the id straight from the address bar, so this is the one
    place an IDOR would show up.
    """
    bogus = "EXC-0000-000000"
    page = escrow_list.page
    page.goto(escrow_list.edit_url_for(bogus), wait_until="domcontentloaded")
    page.wait_for_timeout(5_000)

    body = page.inner_text("body")
    assert bogus not in body or not re.search(r"project\s*information", body, re.I), \
        "a non-existent escrow id rendered a populated wizard"
    assert listed_id not in body, \
        f"a bogus id fell back to showing {listed_id} - the route ignores its " \
        "own parameter"
