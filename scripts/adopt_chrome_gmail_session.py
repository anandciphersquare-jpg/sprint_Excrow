"""Adopt the Gmail session from the local Chrome profile - no typing required.

`scripts/save_gmail_session.py` needs a human to complete Google's password and
2FA challenge. When that is impractical (the window is not reachable, nobody is
at the machine), this script takes the session that the user's own Chrome
already holds.

It **copies** the few files that carry the session into a scratch profile and
drives that copy, so the live browser is never touched and does not have to be
closed:

* `Local State`            - holds the DPAPI-wrapped key the cookie file needs
* `Default/Network/Cookies` - the cookies themselves
* `Default/Preferences`    - keeps Chrome from treating the copy as a first run

Decryption works because the copy runs as the **same Windows user**: the cookie
encryption key is wrapped with DPAPI bound to that account. Run it as anyone
else and the cookies are unreadable, which is the intended protection.

    python scripts/adopt_chrome_gmail_session.py

On success it writes `.auth/gmail_state.json` in exactly the format
`core.mailbox.GmailMailbox` expects, and everything downstream is headless
again. The scratch copy is deleted afterwards.
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from playwright.sync_api import sync_playwright  # noqa: E402

from core.mailbox import INBOX_READY  # noqa: E402

STATE_PATH = Path(".auth/gmail_state.json")
INBOX_URL = "https://mail.google.com/mail/u/0/#inbox"

# Relative to the Chrome "User Data" directory.
SESSION_FILES = ("Local State",)
PROFILE_FILES = ("Network/Cookies", "Preferences", "Secure Preferences")


def chrome_user_data() -> Path:
    local = os.environ.get("LOCALAPPDATA", "")
    return Path(local) / "Google" / "Chrome" / "User Data"


def _copy_open_file(src: Path, dest: Path) -> bool:
    """Copy a file a running Chrome still holds open.

    `shutil.copy2` uses `CopyFile2`, which asks for a share mode Chrome refuses
    on its live `Cookies` database - so it fails with WinError 32 whenever the
    browser is open, which is exactly when this script is useful. A plain
    buffered read opens with the CRT's permissive share mode and succeeds.
    """
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        with open(src, "rb") as reader, open(dest, "wb") as writer:
            shutil.copyfileobj(reader, writer)
        return True
    except OSError as exc:
        print(f"  could not copy {src.name}: {exc}")
        return False


def stage_profile(source: Path, profile: str, dest: Path) -> list[str]:
    """Copy just the session-bearing files into a scratch user-data-dir."""
    copied: list[str] = []
    (dest / profile).mkdir(parents=True, exist_ok=True)
    for name in SESSION_FILES:
        src = source / name
        if src.exists() and _copy_open_file(src, dest / name):
            copied.append(name)
    for name in PROFILE_FILES:
        src = source / profile / name
        if src.exists() and _copy_open_file(src, dest / profile / name):
            copied.append(f"{profile}/{name}")
    return copied


def signed_in(page) -> bool:
    if "accounts.google.com" in page.url:
        return False
    try:
        # Shared with core.mailbox - see INBOX_READY for why this is not the
        # Compose button on its own.
        return page.locator(INBOX_READY).count() > 0
    except Exception:
        return False


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", default="Default",
                        help="Chrome profile directory (default: Default)")
    parser.add_argument("--headed", action="store_true",
                        help="show the browser while adopting the session")
    parser.add_argument("--state", default=str(STATE_PATH))
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    source = chrome_user_data()
    if not source.exists():
        print(f"No Chrome profile at {source}", file=sys.stderr)
        return 1

    scratch = Path(tempfile.mkdtemp(prefix="gmail-adopt-"))
    try:
        copied = stage_profile(source, args.profile, scratch)
        if not copied:
            print(f"Nothing to copy from {source / args.profile}", file=sys.stderr)
            return 1
        print(f"Staged {len(copied)} file(s) from the {args.profile} profile:")
        for name in copied:
            print(f"  {name}")

        # Without the cookie database there is no session to adopt, and the
        # browser would land on the sign-in page for that reason alone. Saying
        # "Chrome is not signed in" there would be a wrong diagnosis of a
        # locked file, so stop and name the real cause instead.
        if not any(name.endswith("Cookies") for name in copied):
            print("\nChrome's cookie database is locked by the running browser, "
                  "so there is no session to adopt.")
            print("Close Chrome completely (check the tray) and re-run this "
                  "script, or use: python scripts/save_gmail_session.py")
            return 1

        with sync_playwright() as p:
            context = p.chromium.launch_persistent_context(
                user_data_dir=str(scratch),
                channel="chrome",
                headless=not args.headed,
                viewport={"width": 1440, "height": 900},
                locale="en-US",
                args=[f"--profile-directory={args.profile}"],
            )
            page = context.pages[0] if context.pages else context.new_page()
            page.goto(INBOX_URL, wait_until="domcontentloaded")

            deadline = time.monotonic() + 60
            while time.monotonic() < deadline and not signed_in(page):
                page.wait_for_timeout(2_000)

            if not signed_in(page):
                print(f"\nChrome's profile is not signed in to Gmail either "
                      f"(landed on {page.url}).")
                print("Use the interactive route instead: "
                      "python scripts/save_gmail_session.py")
                context.close()
                return 1

            state = Path(args.state)
            state.parent.mkdir(parents=True, exist_ok=True)
            context.storage_state(path=str(state))
            print(f"\nAdopted the Chrome Gmail session -> {state}")
            print("Verify headless:  python -m core.mailbox --check")
            context.close()
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
