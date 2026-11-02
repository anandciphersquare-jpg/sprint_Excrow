"""Per-partner credential store - so the mailbox is read once, not every run.

The temporary password is only ever shown in the welcome email, so the *first*
sign-in for a brand-new representative always needs the mailbox. Once that
password has been replaced, though, the new one is something this suite chose
itself, and re-reading the inbox to find it is both unnecessary and impossible:
the temporary password is dead and the mail carrying it is stale.

`shared/chain_state.json` already remembered a password, but only **one** - it
holds a single `chain` slot that the next `--phase admin` run overwrites. Every
new escrow therefore lost the previous partner's credentials, and the only way
back in was another onboarding that could not be replayed (the temporary
password is single-use). This store keeps one record per representative, keyed
by the address the escrow was filed under, and accumulates across runs.

So the rule the flow now follows:

* **No record, or no `new_password` yet** -> the mailbox is required: read the
  welcome mail, take the temporary password, force the change, then save.
* **A record with a `new_password`** -> sign straight in with it. No Gmail.

A login OTP is a different matter and is *not* covered by this: codes are
short-lived and single-use, so they are fetched per attempt (see
`core.mailbox.GmailMailbox.find_login_otp`), never stored.

This file holds real credentials for real UAT accounts. It is written to
`shared/` and is git-ignored for the same reason `.env` is.
"""
from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

STORE_PATH = Path(__file__).resolve().parent.parent / "shared" / "partner_credentials.json"


@dataclass
class PartnerCredentials:
    """Everything needed to sign a partner in without touching the mailbox."""
    email: str = ""
    user_id: str = ""
    company: str = ""
    portal_link: str = ""
    temp_password: str = ""
    new_password: str = ""
    password_changed_at: str = ""
    escrow_ids: list[str] = field(default_factory=list)

    @property
    def is_onboarded(self) -> bool:
        """Has the forced change already happened for this partner?

        The one question the flow branches on: True means sign in with
        `new_password` and skip the mailbox entirely.
        """
        return bool(self.new_password)

    @property
    def login_id(self) -> str:
        """What to type in the email box - the mail's User ID wins if present."""
        return self.user_id or self.email

    def summary(self) -> dict:
        """Loggable: says whether a password exists, never what it is."""
        return {
            "email": self.email,
            "user_id": self.user_id,
            "company": self.company,
            "escrow_ids": list(self.escrow_ids),
            "onboarded": self.is_onboarded,
            "password_changed_at": self.password_changed_at,
            "has_temp_password": bool(self.temp_password),
        }


class CredentialStore:
    """A JSON file of `PartnerCredentials`, keyed by representative address.

        store = CredentialStore()
        creds = store.get("anand.kumar+new6633@paysprint.in")
        if creds and creds.is_onboarded:
            sign_in(creds.login_id, creds.new_password)    # no mailbox
    """

    def __init__(self, path: Path | str = STORE_PATH):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    # ---- persistence -----------------------------------------------------
    def _read(self) -> dict:
        if not self.path.exists():
            return {"partners": {}}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8") or "{}")
        except json.JSONDecodeError:
            # A half-written file must not take a run down; the worst case is
            # one extra mailbox read, which is the un-stored behaviour anyway.
            return {"partners": {}}
        data.setdefault("partners", {})
        return data

    def _write(self, data: dict) -> None:
        self.path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    @staticmethod
    def _key(email: str) -> str:
        return (email or "").strip().lower()

    # ---- reading ---------------------------------------------------------
    def get(self, email: str) -> PartnerCredentials | None:
        raw = self._read()["partners"].get(self._key(email))
        if not raw:
            return None
        known = {f for f in PartnerCredentials.__dataclass_fields__}
        return PartnerCredentials(**{k: v for k, v in raw.items() if k in known})

    def password_for(self, email: str) -> str:
        """The live password, or "" when the mailbox is still needed."""
        creds = self.get(email)
        return creds.new_password if creds else ""

    def needs_mailbox(self, email: str) -> bool:
        """True while the temporary password has not yet been replaced."""
        creds = self.get(email)
        return not (creds and creds.is_onboarded)

    def all(self) -> list[PartnerCredentials]:
        return [self.get(key) for key in sorted(self._read()["partners"])]

    # ---- writing ---------------------------------------------------------
    def _save(self, creds: PartnerCredentials) -> PartnerCredentials:
        data = self._read()
        data["partners"][self._key(creds.email)] = asdict(creds)
        self._write(data)
        return creds

    def record_onboarding(self, email: str, *, user_id: str = "",
                          company: str = "", portal_link: str = "",
                          temp_password: str = "",
                          escrow_id: str = "") -> PartnerCredentials:
        """Record what the welcome mail carried. Does not mark it onboarded.

        Merged into any existing record rather than replacing it: a second
        escrow for the same representative must not wipe the password the first
        one established, which is the whole point of this store.
        """
        creds = self.get(email) or PartnerCredentials(email=email)
        creds.email = email or creds.email
        creds.user_id = user_id or creds.user_id
        creds.company = company or creds.company
        creds.portal_link = portal_link or creds.portal_link
        creds.temp_password = temp_password or creds.temp_password
        if escrow_id and escrow_id not in creds.escrow_ids:
            creds.escrow_ids.append(escrow_id)
        return self._save(creds)

    def record_password_change(self, email: str,
                               new_password: str) -> PartnerCredentials:
        """The moment the mailbox stops being needed for this partner."""
        if not new_password:
            raise ValueError("refusing to record an empty password - the "
                             "mailbox would be skipped with nothing to sign in "
                             "with")
        creds = self.get(email) or PartnerCredentials(email=email)
        creds.email = email or creds.email
        creds.new_password = new_password
        creds.password_changed_at = time.strftime("%Y-%m-%d %H:%M:%S")
        # The temporary password is single-use and now dead. Kept only so
        # TC-PWD-03 ("the old one no longer works") still has something to try.
        return self._save(creds)

    def forget(self, email: str) -> bool:
        """Drop a record - e.g. a partner whose password was reset elsewhere."""
        data = self._read()
        existed = data["partners"].pop(self._key(email), None) is not None
        if existed:
            self._write(data)
        return existed
