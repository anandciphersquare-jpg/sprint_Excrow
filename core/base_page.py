"""BasePage: thin, resilient wrappers over Playwright locators.

Concrete pages should prefer accessibility-first locators
(`get_by_role`, `get_by_label`, `get_by_placeholder`) and only fall back
to CSS/XPath where the DOM forces it. Every locator marked `TODO(locator)`
must be verified against the live portal — see README ("Calibrating locators").
"""
from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import Iterable

from playwright.sync_api import Locator, Page, expect

from config.settings import settings

# Single source of truth: driven by DEFAULT_TIMEOUT_MS. This used to be a
# separate hard-coded 30_000, so setting DEFAULT_TIMEOUT_MS in .env changed the
# context default but left every explicit wait in this file untouched.
DEFAULT_TIMEOUT = settings.default_timeout_ms

MONTH_NAMES = ("January", "February", "March", "April", "May", "June", "July",
               "August", "September", "October", "November", "December")


def _month_year_after(months: int) -> tuple[str, int]:
    """The month name and year `months` after the current month."""
    today = date.today()
    zero_based = today.month - 1 + months
    return MONTH_NAMES[zero_based % 12], today.year + zero_based // 12


class BasePage:
    def __init__(self, page: Page):
        self.page = page

    # ---- navigation -----------------------------------------------------
    def goto(self, url: str, wait_until: str = "domcontentloaded") -> None:
        self.page.goto(url, wait_until=wait_until, timeout=DEFAULT_TIMEOUT)

    def reload(self, wait_until: str = "domcontentloaded") -> None:
        self.page.reload(wait_until=wait_until)

    # ---- interactions ---------------------------------------------------
    def click(self, locator: Locator, *, timeout: int = DEFAULT_TIMEOUT) -> None:
        locator.wait_for(state="visible", timeout=timeout)
        locator.click()

    def fill(self, locator: Locator, value: str, *, clear: bool = True,
             timeout: int = DEFAULT_TIMEOUT) -> None:
        locator.wait_for(state="visible", timeout=timeout)
        if clear:
            locator.fill("")
        locator.fill(value)

    def type_slow(self, locator: Locator, value: str, delay_ms: int = 40) -> None:
        locator.click()
        locator.press_sequentially(value, delay=delay_ms)

    def select_native(self, locator: Locator, *, label: str | None = None,
                      value: str | None = None) -> None:
        """For real <select> elements."""
        if label is not None:
            locator.select_option(label=label)
        elif value is not None:
            locator.select_option(value=value)

    def select_custom_dropdown(self, trigger: Locator, option_text: str) -> None:
        """Open an Angular/Material/ng-select style dropdown and pick an option.

        Assumes options are exposed with role='option' (true for a11y-correct
        widgets). If the portal renders plain <li>/<div> options, override the
        option locator in the concrete page.
        """
        trigger.click()
        option = self.page.get_by_role("option", name=option_text, exact=False).first
        try:
            option.wait_for(state="visible", timeout=5_000)
            option.click()
            return
        except Exception:
            pass
        # fallback: match by visible text within an open overlay
        fallback = self.page.locator(
            ".cdk-overlay-container, .ng-dropdown-panel, .dropdown-menu"
        ).get_by_text(option_text, exact=False).first
        fallback.wait_for(state="visible", timeout=5_000)
        fallback.click()

    @property
    def next_button(self) -> Locator:
        return self.page.get_by_role(
            "button", name=re.compile(r"next|save\s*&?\s*next|continue|proceed", re.I)
        ).first

    def invalid_controls(self) -> list[str]:
        """Angular controls currently considered invalid.

        The wizard keeps 'Next' disabled ("Please complete all required
        fields") without saying which field is at fault, so surface it. Groups
        and arrays are included as `group:<name>` because a stage can be
        invalid without any single input being flagged.
        """
        return self.page.evaluate(
            """() => {
                 const named = [...document.querySelectorAll(
                   '.ng-invalid[formcontrolname]')]
                   .map(e => e.getAttribute('formcontrolname'));
                 const groups = [...document.querySelectorAll(
                   '.ng-invalid[formgroupname], .ng-invalid[formarrayname]')]
                   .map(e => 'group:' + (e.getAttribute('formgroupname')
                                      || e.getAttribute('formarrayname')));
                 return [...named, ...groups];
               }"""
        )

    def next_is_enabled(self, timeout: int = 15_000) -> bool:
        """Whether 'Next' becomes enabled within `timeout`.

        Angular re-runs the stage's validators asynchronously, so the button can
        still be disabled for a moment after the last field is filled - reading
        `is_disabled()` the instant a stage is completed is a race.
        """
        try:
            expect(self.next_button).to_be_enabled(timeout=timeout)
            return True
        except AssertionError:
            return False

    def continue_next(self, *, timeout: int = 15_000) -> None:
        """Advance the wizard, waiting for 'Next' to become enabled."""
        btn = self.next_button
        btn.wait_for(state="visible", timeout=timeout)
        try:
            expect(btn).to_be_enabled(timeout=timeout)
        except AssertionError:
            raise AssertionError(
                "'Next' stayed disabled; still-invalid controls: "
                f"{self.invalid_controls()}"
            ) from None
        btn.click()

    # ---- ngx-bootstrap datepicker ---------------------------------------
    def pick_bs_date(self, field: Locator, *, months_ahead: int = 0,
                     day_index: int = 3) -> str:
        """Choose a date `months_ahead` from today and return the value.

        Typing into these inputs sets the text but leaves the Angular control
        invalid, so selection must go through the calendar. Out-of-range days
        carry `.disabled` on the inner <span> (not the <td>), so only enabled
        cells are eligible.

        The target month is navigated to **absolutely**, by reading the
        calendar header: a picker re-opened on a field that already holds a
        date starts on *that* month, so counting "next" clicks from wherever it
        opened would silently land on a different month each time.
        """
        field.click()
        panel = self.page.locator(".bs-datepicker").last
        panel.wait_for(state="visible", timeout=10_000)

        wanted_month, wanted_year = _month_year_after(months_ahead)
        header = panel.locator("button.current")
        for _ in range(months_ahead + 36):
            shown_month = header.nth(0).inner_text().strip().lower()
            shown_year = header.nth(1).inner_text().strip()
            if shown_month.startswith(wanted_month[:3].lower()) \
                    and shown_year == str(wanted_year):
                break
            panel.locator("button.next").first.click()
            self.page.wait_for_timeout(250)
        else:
            raise AssertionError(
                f"could not reach {wanted_month} {wanted_year} in the datepicker"
            )

        days = panel.locator(
            "table.days tbody td span:not(.disabled):not(.is-other-month)"
        )
        days.first.wait_for(state="visible", timeout=10_000)
        days.nth(min(day_index, days.count() - 1)).click()
        expect(field).not_to_have_value("", timeout=10_000)
        return field.input_value()

    def wait_for_loader(self, timeout: int = 30_000) -> None:
        """Wait out the app-wide spinner.

        The portal covers the page with `.global-loading-overlay` during API
        calls; it swallows clicks, so anything driving a control right after a
        navigation should let it clear first.
        """
        loader = self.page.locator(".global-loading-overlay").first
        if loader.count():
            try:
                loader.wait_for(state="detached", timeout=timeout)
            except Exception:
                pass          # a stuck spinner is the caller's problem to report

    # ---- reads / waits --------------------------------------------------
    def wait_visible(self, locator: Locator, timeout: int = DEFAULT_TIMEOUT) -> Locator:
        locator.wait_for(state="visible", timeout=timeout)
        return locator

    def is_visible(self, locator: Locator, timeout: int = 3_000) -> bool:
        try:
            locator.wait_for(state="visible", timeout=timeout)
            return True
        except Exception:
            return False

    def text_of(self, locator: Locator) -> str:
        return (locator.text_content() or "").strip()

    def form_group_text(self, field: Locator) -> str:
        """Label + any validation message rendered around a field.

        The portal puts inline errors inside the field's `.form-group` without a
        dedicated error class, so reading the group is more reliable than
        guessing at one.
        """
        return field.evaluate(
            "e => { const g = e.closest('.form-group');"
            " return g ? g.innerText : e.parentElement.innerText; }"
        )

    def body_text(self) -> str:
        return self.page.locator("body").inner_text()

    # ---- files ----------------------------------------------------------
    def upload(self, file_input: Locator, paths: str | Iterable[str]) -> None:
        file_input.set_input_files(paths)

    # ---- diagnostics ----------------------------------------------------
    def screenshot(self, name: str, directory: str = "reports/screenshots") -> Path:
        Path(directory).mkdir(parents=True, exist_ok=True)
        safe = re.sub(r"[^A-Za-z0-9_.-]", "_", name)
        path = Path(directory) / f"{safe}.png"
        self.page.screenshot(path=str(path), full_page=True)
        return path
