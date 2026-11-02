"""Read the SprintNXT onboarding mail out of Gmail, headless.

Why this exists: the partner's **temporary password is only ever shown in the
welcome email**. It is not on any admin screen and not in any API response the
suite can reach, so without a mailbox the onboarding chain stops at "an escrow
was created" and every case from first login onward is untestable. This module
is the missing third leg described in
`docs/post-creation-email-and-partner-login.md` section 6.

It drives the Gmail web UI with a saved Playwright session
(`.auth/gmail_state.json`, written by `scripts/save_gmail_session.py`) rather
than IMAP/API, because the account is a Workspace user with 2FA and no app
password: a browser session is the only credential we actually have.

Two behaviours worth knowing before changing this:

* **Gmail sessions expire** (a fortnight or so). When they do, Gmail answers
  every URL with `accounts.google.com/...`. `open()` detects that and raises
  `MailboxUnavailable` naming the re-auth script, instead of timing out on a
  locator that will never appear.
* **Search is the only reliable way in.** The inbox list is virtualised and the
  mail arrives with a subject that has changed at least once, so messages are
  found by searching for a token that is unique to the run - the Escrow ID or
  the alias address - not by position or subject text.

The parser is deliberately tolerant: labels are matched case-insensitively with
several spellings, and the whole mail body is written to
`reports/mail/<query>.txt` on every read, so a label change is a one-look fix
rather than a debugging session.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from playwright.sync_api import sync_playwright

STATE_PATH = Path(".auth/gmail_state.json")
# A real Chrome profile, signed in once and then reused. Preferred over the
# storage-state snapshot because Chrome keeps the session **refreshed**: a
# `storage_state` JSON is a photograph of cookies at one instant and goes stale
# in a fortnight (which is exactly how this suite lost mailbox access), whereas
# a live profile renews its own tokens every time it is opened.
PROFILE_DIR = Path(".auth/gmail-profile")
MAIL_DUMP_DIR = Path("reports/mail")
INBOX_URL = "https://mail.google.com/mail/u/0/#inbox"
SEARCH_URL = "https://mail.google.com/mail/u/0/#search/{query}"

# What proves the inbox is actually up. Deliberately several markers, because
# the single one used before - `div[gh='cm']`, the Compose button - stopped
# matching when Gmail dropped that attribute, and the failure was *silent and
# misdiagnosed*: a perfectly good session reported "Gmail is signed out" and
# sent everyone off to re-authenticate, which of course changed nothing. The
# `gh` values Gmail now renders are sb, sda, mll, cl, chat, tm, mtb, tl.
#
# None of these depend on interface language; a text match on "Compose" would.
# `div[gh='cm']` is kept last so an older Gmail still satisfies the check.
INBOX_READY = ("form[role='search'], div[gh='mtb'], div[gh='tl'], "
               "input[name='q'], div[gh='cm']")

REAUTH_HINT = (
    "Gmail is signed out. Sign in once, headed:\n"
    "    python scripts/save_gmail_session.py\n"
    "That writes a persistent Chrome profile at .auth/gmail-profile which keeps\n"
    "itself signed in; everything after it is headless again."
)


class MailboxUnavailable(RuntimeError):
    """The mailbox could not be opened - almost always an expired session."""


# --- parsing -------------------------------------------------------------
# The welcome mail is a table of label/value rows. Values are matched up to the
# end of the line, then trimmed, because the rendered text collapses the table
# cells onto one line with variable whitespace.
def _labelled(body: str, *labels: str) -> str:
    for label in labels:
        pattern = rf"{label}\s*[:\-]?\s*(.+)"
        match = re.search(pattern, body, re.I)
        if match:
            value = match.group(1).strip()
            # A label sitting alone on its own line puts the value on the next.
            if not value:
                tail = body[match.end():].lstrip("\n")
                value = tail.split("\n", 1)[0].strip()
            if value:
                return value
    return ""


ESCROW_ID_RE = re.compile(r"\bEXC-\d{4}-\d{6}\b", re.I)
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


def _looks_like_a_credential(value: str) -> bool:
    """A password is one token; a sentence is not.

    The mail's own security footer contains the word "password", so without
    this a label match happily returns half a paragraph.
    """
    if not value or len(value) > 64:
        return False
    return not re.search(r"[\s,;]", value)


def _credential(body: str, *labels: str) -> str:
    """Like `_labelled`, but the label must be punctuated and the value sane."""
    for label in labels:
        for match in re.finditer(rf"{label}\s*[:\-]\s*(\S.*)", body, re.I):
            value = match.group(1).strip()
            if _looks_like_a_credential(value):
                return value
        # A label alone on its line, value on the next.
        for match in re.finditer(rf"{label}\s*$", body, re.I | re.M):
            tail = body[match.end():].lstrip("\n")
            value = tail.split("\n", 1)[0].strip()
            if _looks_like_a_credential(value):
                return value
    return ""

# --- login OTP -----------------------------------------------------------
# The login challenge is a *separate* mail from the welcome one and carries a
# short-lived single-use code, so it is read per attempt and never stored.
#
# Two orderings, because a bare `\d{4}` matches far too much - a year, a
# rupee amount, half a phone number. A code introduced by its own wording is
# taken first; only if none is found does the fallback look for a standalone
# 4-6 digit run, and then only in a mail already known to be an OTP mail.
OTP_LABELLED_RES = (
    re.compile(r"(?:otp|one[\s-]*time[\s-]*(?:password|code)|verification\s*code|"
               r"security\s*code|passcode)\s*(?:is|:|-)?\s*(\d{4,8})", re.I),
    re.compile(r"(\d{4,8})\s*(?:is\s*(?:your|the)\s*)?"
               r"(?:otp|one[\s-]*time\s*(?:password|code)|verification\s*code)", re.I),
)
# The boundaries are asymmetric on purpose. A digit run joined *backwards* by
# `-` `.` `,` is part of something bigger (`EXC-0826-456206`), so any of them
# before the run disqualifies it. Forwards, only a separator that is itself
# followed by a digit does - `1234.56` is an amount, but `4821.` is a code at
# the end of a sentence, and treating those the same made every real mail
# unreadable.
OTP_BARE_RE = re.compile(r"(?<![\d.,\-])(\d{4,6})(?!\d)(?![.,\-]\d)")
# How the live mail actually presents it: the intro sentence ends in a colon
# and the code sits alone on the next line.
#
#     Use the following One-Time Password to finish signing in to your
#     SprintExcrow account:
#
#     686671
#
# No label is adjacent, so the labelled patterns miss it - and the bare
# fallback would happily return "2026" out of "(c) 2026 PaySprint" in the
# footer when asked for four digits. A run alone on its line is how a code is
# shown and how a year in a sentence is not, so it is tried in between.
OTP_STANDALONE_RE = re.compile(r"^[^\S\n]*(\d{4,8})[^\S\n]*$", re.M)
# Words that mean "this mail is about a sign-in code", used to decide whether
# the bare-number fallback is safe to apply at all.
OTP_CONTEXT_RE = re.compile(
    r"otp|one[\s-]*time|verification\s*code|security\s*code|passcode|"
    r"verify\s*(?:it'?s\s*you|your)", re.I)


def parse_login_otp(body: str, *, length: int = 0) -> str:
    """Pull a sign-in code out of a mail body.

    `length` (4 for the partner login, 6 for a payout) filters the result when
    known - the two challenges are different lengths and taking the wrong one
    fills the wrong number of boxes for no visible reason.

    Kept separate from the browser work so it is unit-testable against a saved
    body, exactly like `parse_onboarding_mail`.
    """
    def _ok(code: str) -> bool:
        return bool(code) and (not length or len(code) == length)

    for pattern in OTP_LABELLED_RES:
        for match in pattern.finditer(body):
            if _ok(match.group(1)):
                return match.group(1)

    # Nothing labelled. Only guess if the mail is plainly an OTP mail,
    # otherwise any 4-digit string in any mail would qualify.
    if not OTP_CONTEXT_RE.search(body):
        return ""
    standalone = OTP_STANDALONE_RE.findall(body)
    for code in standalone:
        if _ok(code):
            return code
    # A mail that presents a code on its own line has shown its hand. If none
    # of those is the length asked for, the answer is "no code of that length"
    # - not some other number found elsewhere in the text. Falling through here
    # is how "(c) 2026 PaySprint" becomes a four-digit login OTP.
    if standalone:
        return ""

    for match in OTP_BARE_RE.finditer(body):
        if _ok(match.group(1)):
            return match.group(1)
    return ""


@dataclass
class OnboardingMail:
    """What the welcome/notification mail actually carried."""
    query: str = ""
    subject: str = ""
    greeting: str = ""
    user_id: str = ""
    temp_password: str = ""
    escrow_id: str = ""
    portal_link: str = ""
    body: str = field(default="", repr=False)

    @property
    def has_credentials(self) -> bool:
        """A *new*-partner mail. Existing partners get link-only mail."""
        return bool(self.user_id and self.temp_password)

    def summary(self) -> dict:
        data = asdict(self)
        data.pop("body")
        data["chars"] = len(self.body)
        data["has_credentials"] = self.has_credentials
        return data


def parse_onboarding_mail(body: str, links: list[str] | None = None) -> OnboardingMail:
    """Pull the credential fields out of a rendered mail body.

    Kept separate from the browser work so it is unit-testable against a saved
    body without a mailbox.
    """
    mail = OnboardingMail(body=body)
    mail.user_id = _labelled(body, r"user\s*id", r"login\s*(?:id|email)", r"username")
    # `your password` on its own used to be in this list, and it matched the
    # security footer every SprintNXT mail carries - "will never ask for your
    # password, OTP or reset link over phone or email" - yielding a "temporary
    # password" of ", OTP or reset link over phone or email. (c) 2026 PaySprint."
    # A label now has to be followed by a separator, and the value has to look
    # like a credential rather than a sentence.
    mail.temp_password = _credential(
        body, r"temp(?:orary)?\s*password", r"password\s*\(temporary\)",
        r"your\s*(?:temporary\s*)?password")
    escrow = _labelled(body, r"(?:your\s*)?escrow\s*id", r"(?:your\s*)?excrow\s*id")
    match = ESCROW_ID_RE.search(escrow) or ESCROW_ID_RE.search(body)
    mail.escrow_id = match.group(0).upper() if match else ""

    # The greeting names the company the escrow was filed under - the tell for
    # DEF-MAIL-01, where a new representative silently lands on another partner.
    greeting = re.search(r"^\s*(?:dear|hi|hello)\s+([^,\n]+)", body, re.I | re.M)
    mail.greeting = greeting.group(1).strip() if greeting else ""

    # A user id that came back as a label artefact rather than an address.
    if mail.user_id and not EMAIL_RE.fullmatch(mail.user_id):
        found = EMAIL_RE.search(mail.user_id)
        mail.user_id = found.group(0) if found else ""

    for link in links or []:
        if "EXcrowPartnerFront" in link or "excrow-uat" in link:
            mail.portal_link = link
            break
    return mail


# --- the mailbox ---------------------------------------------------------
class GmailMailbox:
    """A headless Gmail reader scoped to one Playwright browser.

    Used as a context manager so the browser is always torn down:

        with GmailMailbox() as inbox:
            mail = inbox.find_onboarding_mail("EXC-0826-838459")
    """

    def __init__(self, state_path: Path | str = STATE_PATH, *, headless: bool = True,
                 slowmo: int = 0, profile_dir: Path | str = PROFILE_DIR):
        self.state_path = Path(state_path)
        self.profile_dir = Path(profile_dir)
        self.headless = headless
        self.slowmo = slowmo
        self._pw = None
        self.browser = None
        self.context = None
        self.page = None

    @property
    def uses_profile(self) -> bool:
        """Prefer the self-renewing profile; fall back to the JSON snapshot."""
        return self.profile_dir.exists() and any(self.profile_dir.iterdir())

    # ---- lifecycle ------------------------------------------------------
    def __enter__(self) -> "GmailMailbox":
        self.open()
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def open(self) -> "GmailMailbox":
        if not self.uses_profile and not self.state_path.exists():
            raise MailboxUnavailable(
                f"No Gmail profile at {self.profile_dir} and no saved session "
                f"at {self.state_path}.\n{REAUTH_HINT}")

        # Try the profile, then the snapshot. The profile is preferred because
        # it renews its own tokens, but `uses_profile` only knows the directory
        # is **non-empty** - it cannot know whether the session inside is still
        # good. A signed-out profile therefore used to mask a perfectly valid
        # `gmail_state.json` sitting next to it: `scripts/adopt_chrome_gmail_session.py`
        # would report success, and `--check` would still say signed out,
        # because the snapshot it had just written was never read.
        sources = []
        if self.uses_profile:
            sources.append(True)
        if self.state_path.exists():
            sources.append(False)

        last: MailboxUnavailable | None = None
        for use_profile in sources:
            try:
                return self._open_with(use_profile)
            except MailboxUnavailable as exc:
                last = exc
                which = "persistent profile" if use_profile else "saved session"
                if len(sources) > 1:
                    print(f"    the {which} is not signed in; trying the other "
                          f"credential source", flush=True)
        raise last or MailboxUnavailable(REAUTH_HINT)

    def _open_with(self, use_profile: bool) -> "GmailMailbox":
        # Everything after `sync_playwright().start()` must be unwound on
        # failure. An exception raised in here does **not** run `__exit__` -
        # the context manager was never entered - so a failed open used to leave
        # the sync driver running, and the *next* `sync_playwright()` anywhere in
        # the process died with "Sync API inside the asyncio loop". The symptom
        # appeared far from the cause: a signed-out mailbox crashed an unrelated
        # later stage rather than reporting itself.
        try:
            self._pw = sync_playwright().start()
            if use_profile:
                self.context = self._pw.chromium.launch_persistent_context(
                    user_data_dir=str(self.profile_dir),
                    channel="chrome",
                    headless=self.headless,
                    slow_mo=self.slowmo,
                    viewport={"width": 1440, "height": 900},
                    locale="en-US",
                )
                self.page = (self.context.pages[0] if self.context.pages
                             else self.context.new_page())
            else:
                self.browser = self._pw.chromium.launch(headless=self.headless,
                                                        slow_mo=self.slowmo)
                self.context = self.browser.new_context(
                    storage_state=str(self.state_path),
                    viewport={"width": 1440, "height": 900},
                    locale="en-US",
                )
                self.page = self.context.new_page()
            self.context.set_default_timeout(30_000)
            self.page.goto(INBOX_URL, wait_until="domcontentloaded")
            self._wait_for_inbox()
        except BaseException:
            self.close()
            raise
        return self

    def close(self) -> None:
        for closer in (self.context, self.browser):
            try:
                closer and closer.close()
            except Exception:
                pass
        if self._pw:
            try:
                self._pw.stop()
            except Exception:
                pass
        self._pw = self.browser = self.context = self.page = None

    def _wait_for_inbox(self, timeout: int = 45_000) -> None:
        deadline = time.monotonic() + timeout / 1000
        while time.monotonic() < deadline:
            if "accounts.google.com" in self.page.url:
                raise MailboxUnavailable(REAUTH_HINT)
            if self.page.locator(INBOX_READY).count():
                return
            self.page.wait_for_timeout(1_000)

        # Not signed out - we never left Gmail - so do not say so. The title
        # carries the signed-in account, which is the one fact that tells a
        # stale readiness marker apart from a dead session, and getting that
        # wrong is what made this failure so expensive to diagnose.
        try:
            title = self.page.title()
        except Exception:
            title = "<unavailable>"
        raise MailboxUnavailable(
            f"Reached Gmail but no inbox marker appeared within "
            f"{timeout // 1000}s.\n"
            f"  last URL : {self.page.url}\n"
            f"  page title: {title!r}\n"
            f"If that title names a mailbox, the session is fine and "
            f"INBOX_READY needs updating\n"
            f"(Gmail changed its markup); it is not a sign-in problem.\n"
            f"Otherwise:\n{REAUTH_HINT}")

    # ---- reading --------------------------------------------------------
    # Gmail keeps rows in the DOM that are not on screen - other sections, and
    # a render buffer - so `tr.zA` alone resolves to hidden rows and a click on
    # `.first` times out against a list that is plainly visible. Every result
    # lookup goes through the `:visible` form.
    RESULT_ROW = "tr.zA:visible"

    def result_rows(self):
        return self.page.locator(self.RESULT_ROW)

    def search(self, query: str) -> int:
        """Run a Gmail search; returns the number of usable result rows."""
        self.page.goto(SEARCH_URL.format(query=query.replace(" ", "+")),
                       wait_until="domcontentloaded")
        self.page.wait_for_timeout(3_000)
        return self.result_rows().count()

    def wait_for_message(self, query: str, *, timeout: int = 300,
                         poll: int = 15) -> bool:
        """Poll the mailbox until a message matching `query` shows up.

        Mail is not instant - the observed lag is tens of seconds - so a single
        search straight after submitting the escrow reliably finds nothing and
        reports a missing email that arrives moments later.
        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.search(query):
                return True
            remaining = int(deadline - time.monotonic())
            print(f"    no mail yet for {query!r}; {remaining}s left", flush=True)
            self.page.wait_for_timeout(poll * 1_000)
        return bool(self.search(query))

    def open_latest(self, query: str) -> OnboardingMail:
        """Open the newest message matching `query` and parse it."""
        if not self.search(query):
            raise MailboxUnavailable(f"No message found for {query!r}")
        subject = ""
        row = self.result_rows().first
        # The result list is virtualised, so the row the search just counted is
        # not necessarily painted yet - clicking it straight away fails with
        # "element is not visible" on a list that is plainly on screen a second
        # later. Wait for it, and bring it into view before clicking.
        row.wait_for(state="visible", timeout=30_000)
        try:
            row.scroll_into_view_if_needed(timeout=10_000)
        except Exception:
            pass
        try:
            subject = row.locator("span.bog").first.inner_text().strip()
        except Exception:
            pass
        # The subject cell is a reliable click target; the row itself has dead
        # zones (checkbox, star, hover actions) that swallow the click.
        target = row.locator("span.bog").first
        if not target.count():
            target = row
        target.click()
        self.page.wait_for_timeout(3_000)

        body_locator = self.page.locator("div.a3s").last
        body_locator.wait_for(state="visible", timeout=30_000)
        body = body_locator.inner_text()
        links = body_locator.locator("a").evaluate_all(
            "els => els.map(e => e.href)")

        mail = parse_onboarding_mail(body, links)
        mail.query = query
        mail.subject = subject
        self._dump(mail)
        return mail

    def find_onboarding_mail(self, query: str, *, wait: int = 300) -> OnboardingMail:
        """Wait for, open and parse the onboarding mail for a run."""
        if not self.wait_for_message(query, timeout=wait):
            raise MailboxUnavailable(
                f"No onboarding mail for {query!r} within {wait}s")
        return self.open_latest(query)

    # ---- login OTP ------------------------------------------------------
    # Default search: recent mail whose text mentions a code. Narrow enough to
    # skip the inbox at large, wide enough to survive the subject line changing
    # again (it already has once - see the module docstring).
    OTP_QUERY = 'newer_than:1d (OTP OR "verification code" OR "one time")'

    def top_row_id(self, query: str) -> str:
        """Identity of the newest result, used to spot a *new* mail arriving.

        Gmail rows carry a stable per-thread DOM id, so comparing it is a
        reliable "has something new landed" test. Timestamps are not: the list
        renders "12:04 PM" for everything that arrived today, so a code from a
        previous attempt minutes ago is indistinguishable from a fresh one by
        text alone - and re-using a spent code fails the login for what looks
        like a wrong password.
        """
        if not self.search(query):
            return ""
        try:
            return self.result_rows().first.get_attribute("id") or ""
        except Exception:
            return ""

    def find_login_otp(self, *, query: str = "", length: int = 4,
                       wait: int = 120, poll: int = 5,
                       after_row_id: str | None = None) -> str:
        """Wait for a sign-in code to arrive, and return it.

        `after_row_id` is the value `top_row_id()` returned *before* the login
        was submitted. While the newest mail is still that one, no new code has
        arrived and this keeps waiting rather than handing back the previous
        attempt's dead code. Pass `None` to accept whatever is newest.

        Raises `MailboxUnavailable` if nothing readable arrives in `wait`
        seconds - the caller decides whether that is a BLOCK or a failure.
        """
        query = query or self.OTP_QUERY
        deadline = time.monotonic() + wait
        last_seen = ""
        while time.monotonic() < deadline:
            current = self.top_row_id(query)
            if current and current != after_row_id:
                mail = self.open_latest(query)
                code = parse_login_otp(mail.body, length=length)
                if code:
                    return code
                # A new mail that is not the code - keep waiting, but do not
                # re-open the same one every poll.
                last_seen = current
                after_row_id = current
            remaining = int(deadline - time.monotonic())
            print(f"    no login OTP yet; {remaining}s left", flush=True)
            self.page.wait_for_timeout(poll * 1_000)

        raise MailboxUnavailable(
            f"No login OTP mail arrived within {wait}s "
            f"(search {query!r}, newest row {last_seen or 'none'}). "
            f"The body of anything that did arrive is in {MAIL_DUMP_DIR}/.")

    def _dump(self, mail: OnboardingMail) -> Path:
        MAIL_DUMP_DIR.mkdir(parents=True, exist_ok=True)
        safe = re.sub(r"[^A-Za-z0-9_.-]", "_", mail.query or "mail")
        path = MAIL_DUMP_DIR / f"{safe}.txt"
        path.write_text(
            f"subject: {mail.subject}\nquery: {mail.query}\n\n{mail.body}",
            encoding="utf-8")
        return path


# --- CLI -----------------------------------------------------------------
def main() -> int:
    parser = argparse.ArgumentParser(description="Check or query the mailbox.")
    parser.add_argument("--check", action="store_true",
                        help="verify the saved session still opens the inbox")
    parser.add_argument("--find", metavar="QUERY",
                        help="find and parse the newest mail matching QUERY")
    parser.add_argument("--otp", action="store_true",
                        help="wait for and print the newest login OTP")
    parser.add_argument("--otp-snapshot", action="store_true",
                        help="print the newest OTP-mail row id and exit - take "
                             "this *before* submitting the login, then pass it "
                             "back as --after-row-id")
    parser.add_argument("--after-row-id", default=None,
                        help="ignore mail already present under this row id")
    parser.add_argument("--otp-length", type=int, default=4,
                        help="4 for the login challenge, 6 for a payout")
    parser.add_argument("--wait", type=int, default=120,
                        help="seconds to wait for the OTP mail")
    parser.add_argument("--json", action="store_true",
                        help="machine-readable output, for callers that shell "
                             "out to this module (see run_chain.py - a second "
                             "Playwright driver cannot run inside the first, so "
                             "reading mail mid-login must be another process)")
    parser.add_argument("--headed", action="store_true")
    args = parser.parse_args()

    doing_otp = args.otp or args.otp_snapshot
    try:
        with GmailMailbox(headless=not args.headed) as inbox:
            if args.check or not (args.find or doing_otp):
                print("Gmail session is valid - inbox opened headless.")
            if args.find:
                mail = inbox.find_onboarding_mail(args.find, wait=30)
                print(json.dumps(mail.summary(), indent=2))
            if args.otp_snapshot:
                row = inbox.top_row_id(inbox.OTP_QUERY)
                print(json.dumps({"row_id": row}) if args.json
                      else f"newest OTP row: {row or 'none'}")
            elif args.otp:
                code = inbox.find_login_otp(length=args.otp_length,
                                            wait=args.wait,
                                            after_row_id=args.after_row_id)
                print(json.dumps({"otp": code}) if args.json
                      else f"login OTP: {code}")
    except MailboxUnavailable as exc:
        if args.json:
            print(json.dumps({"error": str(exc).splitlines()[0]}))
        else:
            print(f"Mailbox unavailable.\n{exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
