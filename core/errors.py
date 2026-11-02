"""Console / page-exception / failed-response monitoring.

Attach a monitor to a page, exercise the workflow, then assert.
The workflow spec requires 'No console/network errors' at several stages.

Note: real UAT apps frequently emit benign 4xx (auth probes, analytics
beacons). Assert hard on `page_errors`; treat `console_errors` /
`failed_responses` as review signals or filter known-noisy URLs via
`ignore_url_substrings`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List

from playwright.sync_api import Page

DEFAULT_IGNORE = (
    "google-analytics", "googletagmanager", "clarity.ms",
    "hotjar", "sentry", "favicon.ico",
)


@dataclass
class PageErrorMonitor:
    ignore_url_substrings: tuple = DEFAULT_IGNORE
    console_errors: List[str] = field(default_factory=list)
    page_errors: List[str] = field(default_factory=list)
    failed_responses: List[str] = field(default_factory=list)

    def attach(self, page: Page) -> "PageErrorMonitor":
        page.on("console", self._on_console)
        page.on("pageerror", self._on_pageerror)
        page.on("response", self._on_response)
        return self

    def _ignored(self, url: str) -> bool:
        return any(s in url for s in self.ignore_url_substrings)

    def _on_console(self, msg) -> None:
        if msg.type == "error" and not self._ignored(msg.location.get("url", "")):
            self.console_errors.append(msg.text)

    def _on_pageerror(self, exc) -> None:
        self.page_errors.append(str(exc))

    def _on_response(self, response) -> None:
        if response.status >= 400 and not self._ignored(response.url):
            self.failed_responses.append(f"{response.status} {response.request.method} {response.url}")

    @property
    def has_errors(self) -> bool:
        return bool(self.console_errors or self.page_errors or self.failed_responses)

    def summary(self) -> str:
        return (
            "\n--- PageErrorMonitor ---"
            f"\nconsole_errors ({len(self.console_errors)}): {self.console_errors}"
            f"\npage_errors ({len(self.page_errors)}): {self.page_errors}"
            f"\nfailed_responses ({len(self.failed_responses)}): {self.failed_responses}"
        )
