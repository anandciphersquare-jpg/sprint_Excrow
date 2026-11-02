"""One-time, **headed** Gmail sign-in that re-saves `.auth/gmail_state.json`.

The onboarding flow can only be verified end to end if the welcome email is
readable, because the temporary password exists nowhere else - not in the admin
portal, not in any API response the suite can see. Mailbox access is therefore a
hard dependency of `scripts/run_onboarding_e2e.py`, and this script is how it is
granted.

Google will not complete a password + 2FA challenge headlessly, so this is the
one step in the whole suite that a human must sit through. Everything after it
runs headless off the saved cookies.

    python scripts/save_gmail_session.py

A browser window opens on the Gmail sign-in page. Sign in as the mailbox that
receives the representative aliases (by default the account behind
`REPRESENTATIVE_EMAIL`), complete 2FA, and wait until the inbox renders. The
script watches for the inbox and saves the session automatically; press Enter in
the terminal if it is taking its own view of when you are done.

The address and password can be typed for you, leaving only 2FA:

    $env:GMAIL_ADDRESS = "anand.kumar@paysprint.in"
    $env:GMAIL_PASSWORD = "..."
    python scripts/save_gmail_session.py

Both are read from the environment and never written to disk - not to `.env`,
not to the profile. This runs about once a fortnight, and a Google account
password is worth more than saving that typing.

The saved state expires - roughly a fortnight in practice - so expect to re-run
this occasionally. `core.mailbox.GmailMailbox` fails with a message pointing
here rather than hanging on the sign-in page.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from playwright.sync_api import sync_playwright  # noqa: E402

from config.settings import settings  # noqa: E402
from core.mailbox import INBOX_READY, PROFILE_DIR  # noqa: E402

STATE_PATH = Path(".auth/gmail_state.json")
INBOX_URL = "https://mail.google.com/mail/u/0/#inbox"
SIGNIN_URL = ("https://accounts.google.com/signin/v2/identifier"
              "?service=mail&continue=https://mail.google.com/mail/u/0/")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--timeout", type=int, default=600,
                        help="seconds to wait for the inbox (default 600)")
    parser.add_argument("--state", default=str(STATE_PATH),
                        help=f"snapshot fallback path (default {STATE_PATH})")
    parser.add_argument("--profile", default=str(PROFILE_DIR),
                        help=f"persistent Chrome profile (default {PROFILE_DIR})")
    parser.add_argument("--email", default=os.environ.get("GMAIL_ADDRESS", ""),
                        help="pre-fill the address (or set GMAIL_ADDRESS)")
    return parser.parse_args()


# Calibrated against the live page, because guessing costs a whole sign-in:
#
# * The address box is `<input type="text" name="identifier" id="identifierId">`
#   - **not** `type="email"`, which matches nothing and silently leaves the
#     form blank.
# * The page also carries a decoy `<input type="password" name="hiddenPassword"
#   aria-hidden="true" tabindex="-1">` for password managers. It is present on
#   the *address* step, so `input[type=password]` first-match fills the decoy
#   and the real field never receives anything. Google's real one is
#   `name="Passwd"`; anything aria-hidden is excluded.
EMAIL_BOX = "#identifierId, input[name='identifier'], input[type='email']"
PASSWORD_BOX = ("input[name='Passwd'], "
                "input[type='password']:not([aria-hidden='true'])"
                ":not([name='hiddenPassword'])")
NEXT_BUTTON = re.compile(r"next|continue|sign\s*in", re.I)


def _advance(page) -> None:
    page.get_by_role("button", name=NEXT_BUTTON).first.click()


def prefill(page, email: str, password: str) -> None:
    """Type the address and password so only 2FA is left to do by hand.

    Strictly a convenience. Google reorders and re-skins these steps often, and
    may skip straight to a passkey or a device prompt, so **every** step here is
    best-effort: anything that does not appear is left for the human, who is
    sitting in front of the window anyway. The script never decides the sign-in
    has failed on the strength of this - only the inbox check does that.

    The password is read from the environment and never written anywhere. It is
    not stored in `.env`, because this runs perhaps once a fortnight and a
    Google account password is worth more than that convenience.
    """
    # An expired session shows a chooser rather than the address box.
    try:
        tile = page.get_by_text(email, exact=False).first
        if tile.is_visible(timeout=3_000):
            tile.click()
            page.wait_for_timeout(2_000)
            print(f"  picked {email} from the account chooser")
    except Exception:
        pass

    if not _fill_address(page, email):
        return
    if not password:
        print("  no GMAIL_PASSWORD set - type the password in the window")
        return
    _fill_password(page, password)


def _fill_address(page, email: str) -> bool:
    try:
        box = page.locator(EMAIL_BOX).first
        box.wait_for(state="visible", timeout=20_000)
        box.fill(email)
        _advance(page)
        print(f"  filled the address ({email})")
        return True
    except Exception as exc:
        # Already past this step (chooser, or a remembered account) is fine.
        if page.locator(PASSWORD_BOX).count():
            print("  address step already passed")
            return True
        print(f"  could not fill the address ({type(exc).__name__}); "
              "do it in the window")
        return False


def _fill_password(page, password: str) -> None:
    try:
        box = page.locator(PASSWORD_BOX).first
        box.wait_for(state="visible", timeout=30_000)
        box.fill(password)
        _advance(page)
        print("  filled the password - complete 2FA in the window")
    except Exception as exc:
        print(f"  could not fill the password ({type(exc).__name__}); "
              "do it in the window")


def inbox_reached(page) -> bool:
    """True once we are actually inside Gmail, not on an accounts.google page."""
    if "accounts.google.com" in page.url:
        return False
    if "mail.google.com" not in page.url:
        return False
    try:
        # Shared with core.mailbox so this script and the suite agree on what
        # "the inbox is up" means. They disagreed once - this one waited for a
        # marker Gmail had stopped rendering, so a successful sign-in timed out
        # and saved nothing.
        return page.locator(INBOX_READY).count() > 0
    except Exception:
        return False


def main() -> int:
    args = parse_args()
    state = Path(args.state)
    state.parent.mkdir(parents=True, exist_ok=True)

    print("Opening a browser window for Gmail sign-in.")
    print(f"Sign in as the mailbox that receives {settings.representative_email} "
          "aliases, finish 2FA, and leave it on the inbox.")
    print(f"Waiting up to {args.timeout}s; the session saves itself once the "
          "inbox loads.\n")

    profile = Path(args.profile)
    profile.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        # A **persistent profile**, not a throwaway context. The storage-state
        # snapshot this script used to write is a photograph of cookies at one
        # instant and goes stale in about a fortnight - which is how the suite
        # lost mailbox access in the first place. A real profile renews its own
        # tokens on every open, so this sign-in is meant to be the last one.
        # `channel="chrome"` matters too: Google's password step rejects the
        # bundled Chromium's fingerprint far more often than real Chrome's.
        context = p.chromium.launch_persistent_context(
            user_data_dir=str(profile),
            channel="chrome",
            headless=False,
            args=["--start-maximized"],
            viewport=None,
            locale="en-US",
        )
        page = context.pages[0] if context.pages else context.new_page()
        page.goto(SIGNIN_URL, wait_until="domcontentloaded")
        page.wait_for_timeout(2_000)

        # Already signed in from a previous run - nothing to type.
        if inbox_reached(page):
            print("  the profile is already signed in")
        elif args.email:
            prefill(page, args.email, os.environ.get("GMAIL_PASSWORD", ""))

        deadline = time.monotonic() + args.timeout
        saved = False
        closed = False
        while time.monotonic() < deadline:
            # Closing the window is a normal way to abandon this, so it is
            # reported as such. Letting the TargetClosedError out buried the
            # one line that mattered - which step the sign-in reached - under
            # a twenty-line Playwright traceback.
            try:
                if inbox_reached(page):
                    page.wait_for_timeout(3_000)     # let cookies settle
                    context.storage_state(path=str(state))
                    saved = True
                    break
                page.wait_for_timeout(2_000)
            except Exception:
                closed = True
                break

        if not saved:
            if closed:
                print("\nThe browser window was closed before the inbox "
                      "rendered, so nothing was saved.")
            else:
                print(f"\nTimed out before the inbox rendered. "
                      f"Last URL: {page.url}")
            print(f"The profile at {profile} is kept, so a re-run resumes "
                  "wherever the sign-in got to.")
            try:
                context.close()
            except Exception:
                pass
            return 1

        print(f"\nSigned in. Persistent profile: {profile}")
        print(f"Storage-state snapshot (fallback): {state}")
        print("Verify it, headless:  python -m core.mailbox --check")
        context.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
