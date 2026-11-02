"""The store that takes Gmail out of the loop after the password change.

No browser and no mailbox: this is the decision logic that says whether a
partner still needs the welcome mail, and it has to be right in both
directions. Saying "onboarded" too early sends a run to the portal with no
password; saying it too late sends it to the mailbox for a temporary password
that is already dead and a mail that will never be re-sent.

The OTP parser is here too, for the opposite reason: a login code is *never*
stored, so the only part of it testable without a live mailbox is the parse.
"""
import pytest

from core.credentials import CredentialStore, PartnerCredentials
from core.mailbox import parse_login_otp


@pytest.fixture
def store(tmp_path):
    return CredentialStore(tmp_path / "partner_credentials.json")


EMAIL = "anand.kumar+new6633@paysprint.in"


# =========================== the store ===================================

@pytest.mark.partner
def test_an_unknown_partner_still_needs_the_mailbox(store):
    assert store.get(EMAIL) is None
    assert store.needs_mailbox(EMAIL)
    assert store.password_for(EMAIL) == ""


@pytest.mark.partner
def test_the_welcome_mail_alone_does_not_end_the_mailbox_requirement(store):
    """A temporary password is not a usable one - the change still has to run."""
    store.record_onboarding(EMAIL, user_id=EMAIL, company="QAXIETR Ltd",
                            temp_password="Temp@123", escrow_id="EXC-0826-456206")
    creds = store.get(EMAIL)
    assert creds.temp_password == "Temp@123"
    assert not creds.is_onboarded
    assert store.needs_mailbox(EMAIL), \
        "a partner who has not changed their password still needs the mail"


@pytest.mark.partner
def test_the_password_change_is_what_ends_it(store):
    store.record_onboarding(EMAIL, user_id=EMAIL, temp_password="Temp@123")
    store.record_password_change(EMAIL, "NewStrong@456")

    assert not store.needs_mailbox(EMAIL)
    assert store.password_for(EMAIL) == "NewStrong@456"
    creds = store.get(EMAIL)
    assert creds.is_onboarded
    assert creds.password_changed_at, "the change must be dated"
    assert creds.temp_password == "Temp@123", \
        "the dead temporary password is kept so TC-PWD-03 can still try it"


@pytest.mark.partner
def test_credentials_survive_a_later_escrow_for_the_same_partner(store):
    """The bug this store exists to fix.

    `chain_state.json` holds one slot, so the next escrow overwrote the
    previous partner's password. Here a second escrow must append, not erase.
    """
    store.record_onboarding(EMAIL, temp_password="Temp@123",
                            escrow_id="EXC-0826-456206")
    store.record_password_change(EMAIL, "NewStrong@456")

    store.record_onboarding(EMAIL, escrow_id="EXC-0926-999999")

    creds = store.get(EMAIL)
    assert creds.new_password == "NewStrong@456", \
        "a second escrow must not wipe the working password"
    assert creds.escrow_ids == ["EXC-0826-456206", "EXC-0926-999999"]


@pytest.mark.partner
def test_partners_are_kept_apart(store):
    store.record_password_change("a@paysprint.in", "PwdA@1")
    store.record_password_change("b@paysprint.in", "PwdB@2")
    assert store.password_for("a@paysprint.in") == "PwdA@1"
    assert store.password_for("b@paysprint.in") == "PwdB@2"
    assert len(store.all()) == 2


@pytest.mark.partner
def test_the_address_is_matched_regardless_of_case(store):
    store.record_password_change(EMAIL.upper(), "NewStrong@456")
    assert store.password_for(EMAIL) == "NewStrong@456"


@pytest.mark.partner
@pytest.mark.negative
def test_an_empty_password_is_refused(store):
    """Recording "" would mark the partner onboarded with nothing to log in with."""
    with pytest.raises(ValueError):
        store.record_password_change(EMAIL, "")
    assert store.needs_mailbox(EMAIL)


@pytest.mark.partner
@pytest.mark.negative
def test_a_corrupt_store_degrades_to_needing_the_mailbox(store):
    """The worst case must be one extra mail read, not a crash."""
    store.path.write_text("{ this is not json", encoding="utf-8")
    assert store.get(EMAIL) is None
    assert store.needs_mailbox(EMAIL)


@pytest.mark.partner
def test_forget_sends_a_partner_back_to_the_mailbox(store):
    store.record_password_change(EMAIL, "NewStrong@456")
    assert store.forget(EMAIL) is True
    assert store.needs_mailbox(EMAIL)
    assert store.forget(EMAIL) is False


@pytest.mark.partner
@pytest.mark.security
def test_the_summary_never_carries_a_password(store):
    store.record_onboarding(EMAIL, temp_password="Temp@123")
    store.record_password_change(EMAIL, "NewStrong@456")
    dumped = str(store.get(EMAIL).summary())
    assert "NewStrong@456" not in dumped and "Temp@123" not in dumped
    assert "onboarded" in dumped


@pytest.mark.partner
def test_login_id_prefers_the_user_id_from_the_mail():
    creds = PartnerCredentials(email="rep@paysprint.in", user_id="USER123")
    assert creds.login_id == "USER123"
    assert PartnerCredentials(email="rep@paysprint.in").login_id == "rep@paysprint.in"


# =========================== the OTP parser ==============================

@pytest.mark.partner
@pytest.mark.parametrize("body,expected", [
    ("Your OTP is 4821 . It expires in 10 minutes.", "4821"),
    ("Your one-time password: 4821", "4821"),
    ("Verification code - 4821", "4821"),
    ("4821 is your OTP for SprintNXT login.", "4821"),
    ("Use security code 4821 to sign in.", "4821"),
])
def test_a_labelled_code_is_read(body, expected):
    assert parse_login_otp(body, length=4) == expected


@pytest.mark.partner
def test_the_right_length_is_chosen_when_both_appear():
    """Login is 4 boxes and payout is 6 - taking the wrong one fills the
    wrong number for no visible reason."""
    body = "Your payout OTP is 123456 and your login code is 4821."
    assert parse_login_otp(body, length=4) == "4821"
    assert parse_login_otp(body, length=6) == "123456"


@pytest.mark.partner
@pytest.mark.negative
def test_a_mail_that_is_not_an_otp_mail_yields_nothing():
    """A bare 4-digit run is far too common to guess at: a year, an amount,
    part of a phone number. Without OTP wording, return nothing."""
    body = ("Dear QAXIETR Ltd, your escrow EXC-0826-456206 was created in 2026 "
            "for an amount of 5000. Call 9643063234.")
    assert parse_login_otp(body, length=4) == ""


@pytest.mark.partner
def test_a_bare_code_is_taken_only_with_otp_wording():
    body = "Verify it's you\n\n4821\n\nThis code expires shortly."
    assert parse_login_otp(body, length=4) == "4821"


@pytest.mark.partner
@pytest.mark.negative
def test_an_escrow_id_is_not_mistaken_for_a_code():
    body = "Your OTP for escrow EXC-0826-456206 is 4821."
    assert parse_login_otp(body, length=4) == "4821"


@pytest.mark.partner
@pytest.mark.negative
def test_an_empty_body_is_not_an_error():
    assert parse_login_otp("", length=4) == ""
