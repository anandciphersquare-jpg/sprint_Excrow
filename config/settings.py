"""Central configuration.

Reads from environment (via a local `.env`). Import `settings` everywhere;
never hard-code URLs/credentials in page objects or tests.

**Credentials are NOT defaulted.** They used to be baked in as fallbacks so the
suite ran out of the box, which meant a missing or stale `.env` silently
authenticated with a secret committed to shared source. A required value that
is missing or blank now raises immediately, naming the key, rather than failing
85 tests at login ninety minutes later.

Non-secret values (URLs, timeouts) keep their UAT defaults - they are not
sensitive and a wrong URL fails obviously.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

load_dotenv()  # picks up ./.env if present; no-op otherwise

_SETUP_HINT = (
    "Copy .env.example to .env and fill it in "
    "(see 'Setup' in README.md). Credentials are deliberately not defaulted."
)


class ConfigError(RuntimeError):
    """Raised when a required setting is missing, so it fails at import."""


def _get(key: str, default: str = "") -> str:
    return os.getenv(key, default)


def _require(key: str) -> str:
    """A setting with no safe default - missing or blank is a hard error."""
    value = os.getenv(key, "").strip()
    if not value:
        raise ConfigError(f"Required setting {key} is missing or blank. {_SETUP_HINT}")
    return value


def _flag(key: str, default: bool = False) -> bool:
    return _get(key, str(default)).strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class Settings:
    # --- Super Admin portal ---
    admin_base_url: str = field(default_factory=lambda: _get(
        "ADMIN_BASE_URL",
        "https://uat.sprintnxt.in/NexGenAdminFront/#/auth/login",
    ))
    admin_username: str = field(default_factory=lambda: _require("ADMIN_USERNAME"))
    admin_password: str = field(default_factory=lambda: _require("ADMIN_PASSWORD"))
    # Optional: the superadmin account currently goes straight to the dashboard.
    # Set ADMIN_PIN_REQUIRED=true to make a missing PIN prompt a failure instead
    # of something `AdminLoginPage.enter_pin` silently skips past.
    admin_pin: str = field(default_factory=lambda: _get("ADMIN_PIN"))
    admin_pin_required: bool = field(
        default_factory=lambda: _flag("ADMIN_PIN_REQUIRED", False))

    # --- Partner portal ---
    partner_base_url: str = field(default_factory=lambda: _get(
        "PARTNER_BASE_URL",
        "https://excrow-uat.sprintnxt.in/EXcrowPartnerFrontV2/#/auth/login",
    ))
    partner_username: str = field(default_factory=lambda: _require("PARTNER_USERNAME"))
    partner_password: str = field(default_factory=lambda: _require("PARTNER_PASSWORD"))
    # Partner login is two-step: email+password only reaches an OTP challenge,
    # and the 4-digit code is emailed. Tests needing a real partner session
    # skip unless a code is supplied, e.g.
    #   PARTNER_OTP=1234 pytest -m partner
    # Codes are short-lived, so this is a per-run value, not a stored secret.
    partner_otp: str = field(default_factory=lambda: _get("PARTNER_OTP"))
    # UAT fixed challenge codes. These are environment constants, not secrets:
    # the login challenge is 4 digits and the payout challenge is 6, and using
    # one where the other belongs silently fills the wrong number of boxes.
    # Overridable so a real code can be supplied when UAT stops accepting the
    # fixed ones, or when the suite is pointed at another environment.
    partner_login_otp: str = field(
        default_factory=lambda: _get("PARTNER_LOGIN_OTP", "1234"))
    partner_payout_otp: str = field(
        default_factory=lambda: _get("PARTNER_PAYOUT_OTP", "123456"))
    # Read the login code out of Gmail instead of using the fixed one above.
    # Off by default: the fixed code is what UAT currently accepts, it costs no
    # mailbox round-trip, and turning this on makes every partner sign-in depend
    # on a live Gmail session. Turn it on for an environment that mails a real
    # code - the fixed code is then wrong and every login fails identically.
    partner_otp_from_mail: bool = field(
        default_factory=lambda: _flag("PARTNER_OTP_FROM_MAIL", False))
    # Seconds to wait for that code to arrive before giving up.
    partner_otp_mail_wait: int = field(
        default_factory=lambda: int(_get("PARTNER_OTP_MAIL_WAIT", "120")))

    # --- Company Representative (admin stage-1 auto-fill email) ---
    representative_email: str = field(
        default_factory=lambda: _require("REPRESENTATIVE_EMAIL"))

    # --- Runtime ---
    # Single source of truth for waits: core.base_page reads this rather than
    # keeping its own hard-coded constant.
    default_timeout_ms: int = field(
        default_factory=lambda: int(_get("DEFAULT_TIMEOUT_MS", "30000")))


settings = Settings()
