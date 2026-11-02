"""Parser tests against mail bodies captured from the live mailbox.

Everything here is a saved body, so it runs with no browser and no Gmail
session. The point is that these are the *actual* mails SprintNXT sends -
`reports/mail/` is written on every read - rather than bodies invented to match
the parser, which is how both bugs below survived until a real mail was read.
"""
import pytest

from core.mailbox import parse_login_otp, parse_onboarding_mail

# Captured 2026-09-23 from anand.kumar@paysprint.in.
# Subject: "SprintNXT || One-Time Password (OTP)"
LIVE_OTP_MAIL = """\


Verify your login

Hi Nestleap,

Use the following One-Time Password to finish signing in to your SprintExcrow account:

686671

 Tap and hold the code to copy it

Valid for 5 minutes.

 If you did not attempt to sign in, you can ignore this email - no one can access your account without this code.



SprintExcrow - Escrow & Payments, by PaySprint

Need help? support@paysprint.in  .  +91 700-000-0000

This is a security-sensitive email. SprintExcrow will never ask for your \
password, OTP or reset link over phone or email. (c) 2026 PaySprint. \
All rights reserved.
"""


@pytest.mark.partner
def test_the_live_otp_mail_yields_its_code():
    """The code is alone on its own line, with no label beside it."""
    assert parse_login_otp(LIVE_OTP_MAIL, length=6) == "686671"


@pytest.mark.partner
@pytest.mark.negative
def test_the_copyright_year_is_not_mistaken_for_a_four_digit_code():
    """`(c) 2026 PaySprint` is the trap in this footer.

    Asked for four digits, the bare-number fallback would return 2026 - a
    perfectly plausible-looking OTP that is simply the year. This mail's real
    code is six digits, so the right answer for four is nothing at all.
    """
    assert parse_login_otp(LIVE_OTP_MAIL, length=4) != "2026"
    assert parse_login_otp(LIVE_OTP_MAIL, length=4) == ""


@pytest.mark.partner
@pytest.mark.negative
def test_the_security_footer_is_not_read_as_a_temporary_password():
    """Every SprintNXT mail carries "will never ask for your password, OTP …".

    A `your password` label match against that returned
    ", OTP or reset link over phone or email. (c) 2026 PaySprint." as the
    temporary password - which then went to the portal as a credential.
    """
    mail = parse_onboarding_mail(LIVE_OTP_MAIL)
    assert mail.temp_password == "", \
        f"security boilerplate parsed as a password: {mail.temp_password!r}"
    assert not mail.has_credentials, \
        "an OTP mail must never look like a credential-bearing welcome mail"


@pytest.mark.partner
def test_a_real_temporary_password_is_still_read():
    """The fix must not cost the case it exists for."""
    body = ("Dear QAXIETR Ltd,\n\n"
            "User ID: anand.kumar+new6633@paysprint.in\n"
            "Temporary Password: Xy7#kQ2m\n"
            "Escrow ID: EXC-0826-456206\n")
    mail = parse_onboarding_mail(body)
    assert mail.temp_password == "Xy7#kQ2m"
    assert mail.user_id == "anand.kumar+new6633@paysprint.in"
    assert mail.escrow_id == "EXC-0826-456206"
    assert mail.has_credentials


@pytest.mark.partner
def test_a_label_on_its_own_line_still_works():
    body = ("Temporary Password\nXy7#kQ2m\n"
            "User ID\nrep@paysprint.in\n")
    mail = parse_onboarding_mail(body)
    assert mail.temp_password == "Xy7#kQ2m"
