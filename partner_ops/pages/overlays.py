"""Clearing the portal's blocking overlays.

The portal layers everything modal into a single `.cdk-overlay-container`, and
each pane brings a `.cdk-overlay-backdrop` that **intercepts pointer events for
the entire page**. Three of them show up unprompted during a normal run:

* `.alert-modal-panel` - "FAILED / Please add bank credentials first",
  "No Prefix found! Please contact to administration", "Invalid OTP!".
* `.session-timeout-modal-panel` - the ~2 minute idle prompt.
* Occasionally both at once, stacked.

An undismissed backdrop turns every subsequent click into a 30-second timeout
whose error names the element being clicked rather than the overlay covering it -
which reads as "the escrow list's Submit button is broken" when nothing is wrong
with it. That misdiagnosis cost real time here, so clearing overlays is a shared,
explicit step rather than something each page object improvises.

`clear_blocking_overlays` is deliberately **non-destructive to state**: it only
presses the affirmative control on alerts (OK/Okay) and *Continue Session* on the
idle prompt. It never presses Logout Now, and it never closes a wizard the test
is driving - `MakePayoutModal` and the Add-Beneficiary form both live in
`.cdk-overlay-pane` too, so it matches on the specific alert/session panel
classes instead of closing panes indiscriminately.
"""
from __future__ import annotations

import re

from playwright.sync_api import Page

ALERT_PANEL = ".cdk-overlay-pane.alert-modal-panel"
SESSION_PANEL = ".cdk-overlay-pane.session-timeout-modal-panel"
BACKDROP = ".cdk-overlay-backdrop"

_AFFIRMATIVE = re.compile(r"^\s*(ok|okay|close)\s*$", re.I)
_CONTINUE = re.compile(r"continue\s*session", re.I)


def alert_text(page: Page) -> str:
    panel = page.locator(ALERT_PANEL).first
    if not panel.count():
        return ""
    try:
        return re.sub(r"\s+", " ", panel.inner_text()).strip()
    except Exception:
        return ""


def clear_blocking_overlays(page: Page, *, attempts: int = 4) -> list[str]:
    """Dismiss alert and idle-session overlays. Returns what they said.

    Loops because they stack: dismissing the alert can leave the session prompt
    (and its backdrop) behind.
    """
    messages: list[str] = []
    for _ in range(attempts):
        cleared_something = False

        session = page.locator(SESSION_PANEL).first
        if session.count():
            try:
                messages.append(re.sub(r"\s+", " ", session.inner_text()).strip())
                button = session.get_by_role("button", name=_CONTINUE).first
                if button.count():
                    button.click(timeout=5_000)
                    cleared_something = True
            except Exception:
                pass

        alert = page.locator(ALERT_PANEL).first
        if alert.count():
            try:
                messages.append(re.sub(r"\s+", " ", alert.inner_text()).strip())
                button = alert.get_by_role("button", name=_AFFIRMATIVE).first
                if button.count():
                    button.click(timeout=5_000)
                    cleared_something = True
            except Exception:
                pass

        if not cleared_something:
            break
        page.wait_for_timeout(700)

    _wait_for_backdrops_to_clear(page)
    return [m for m in messages if m]


def _wait_for_backdrops_to_clear(page: Page, timeout: int = 8_000) -> bool:
    """Poll until no backdrop remains.

    Waiting on `.first` to detach is not enough when two are stacked - the
    second keeps swallowing clicks.
    """
    deadline = timeout
    while deadline > 0:
        try:
            if page.locator(BACKDROP).count() == 0:
                return True
        except Exception:
            return True
        page.wait_for_timeout(300)
        deadline -= 300
    return False


def blocking_overlay_present(page: Page) -> bool:
    try:
        return page.locator(BACKDROP).count() > 0
    except Exception:
        return False
