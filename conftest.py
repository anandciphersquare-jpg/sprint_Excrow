"""Root pytest configuration shared by every suite.

Layers our own context/error monitoring on top of pytest-playwright, whose
CLI flags remain available: --headed, --browser {chromium|firefox|webkit},
--slowmo, --tracing, --video, --screenshot, etc.
"""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

import pytest
from playwright.sync_api import Page

from config.settings import settings
from core.artifact_store import ArtifactStore
from core import dashboard

SCREENSHOT_DIR = Path("reports/screenshots")
REPORT_PATH = Path("reports/report.html")

# --- Test Execution Report (reports/dashboard.html) -----------------------
_results: dict = {}
_session_start = datetime.now()


def pytest_configure(config):
    """Archive the previous HTML report before this run overwrites it.

    `--html` is a fixed filename, so every invocation - including a bare
    `--collect-only` - used to destroy the last run's report and with it any
    record of what passed. Rename it instead, so history survives.
    """
    if REPORT_PATH.exists():
        stamp = datetime.fromtimestamp(REPORT_PATH.stat().st_mtime)
        base = f"report_{stamp:%Y%m%d_%H%M%S}"
        archived = REPORT_PATH.with_name(f"{base}.html")
        # Two runs inside the same second would collide; never silently drop a
        # report just because its timestamp is already taken.
        seq = 1
        while archived.exists():
            archived = REPORT_PATH.with_name(f"{base}_{seq}.html")
            seq += 1
        try:
            REPORT_PATH.rename(archived)
        except OSError:
            pass              # never block a run over report housekeeping


@pytest.fixture(scope="session")
def browser_context_args(browser_context_args):
    """Override pytest-playwright defaults for all contexts."""
    return {
        **browser_context_args,
        "ignore_https_errors": True,          # UAT certs are often self-signed
        "viewport": {"width": 1440, "height": 900},
        "accept_downloads": True,
        # Both portals call navigator.geolocation on login and hard-block with
        # "Please allow the Location access!" if it is denied or unanswered.
        "permissions": ["geolocation"],
        "geolocation": {"latitude": 28.6139, "longitude": 77.2090},
        "locale": "en-IN",
    }


@pytest.fixture
def artifact_store() -> ArtifactStore:
    """JSON-backed hand-off (e.g. the Escrow ID) between admin and partner."""
    return ArtifactStore()


# --- screenshot-on-failure for our custom *_page fixtures -----------------
def _find_page(item) -> Page | None:
    for value in getattr(item, "funcargs", {}).values():
        if isinstance(value, Page):
            return value
        inner = getattr(value, "page", None)
        if isinstance(inner, Page):
            return inner
    return None


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()

    # Screenshot on failure. Setup is included deliberately: this suite drives
    # the whole wizard inside the pricing/documents/review fixtures, so a
    # call-phase-only hook missed the failures most in need of evidence.
    if report.when in ("setup", "call") and report.failed:
        page = _find_page(item)
        if page is not None:
            SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
            name = re.sub(r"[^A-Za-z0-9_.-]", "_", item.nodeid)[:150]
            try:
                page.screenshot(path=str(SCREENSHOT_DIR / f"FAIL_{name}.png"),
                                full_page=True)
            except Exception:
                pass

    _record(item, report)


def _record(item, report) -> None:
    """Accumulate one pytest report into the dashboard's result set."""
    stem = item.nodeid.split("::")[0].rsplit("/", 1)[-1].removesuffix(".py")
    module, order = dashboard.MODULE_NAMES.get(stem, (stem, 99))
    if "partner/" in item.nodeid and stem == "test_login":
        module, order = "Partner - Login", 6

    entry = _results.get(item.nodeid)
    if entry is None:
        markers = {m.name for m in item.iter_markers()}
        kind = next((dashboard.TYPE_LABEL[t] for t in dashboard.TYPE_ORDER
                     if t in markers), "Functional")
        entry = dashboard.TestResult(
            nodeid=item.nodeid, module=module, order=order,
            name=dashboard.humanise(item.nodeid), type=kind)
        _results[item.nodeid] = entry

    entry.duration += getattr(report, "duration", 0.0) or 0.0

    if report.failed:
        # A setup failure means the test never ran - that is "blocked", not
        # "failed", and conflating them hides environment problems.
        entry.status = "blocked" if report.when == "setup" else "failed"
        entry.error = str(getattr(report, "longreprtext", "") or report.longrepr or "")[:4000]
        entry.error_category = dashboard.classify_error(entry.error)
        if report.when == "setup":
            entry.error_category = "Setup / Fixture Error"
        entry.explain = dashboard.explain(
            entry.error, status=entry.status, name=entry.name,
            module=entry.module, phase=report.when)
    elif report.skipped:
        entry.status = "xfailed" if hasattr(report, "wasxfail") else "skipped"
        if not entry.error:
            entry.error = str(getattr(report, "longreprtext", "") or "")[:1000]
    elif report.when == "call":
        entry.status = "xpassed" if hasattr(report, "wasxfail") else "passed"


def pytest_sessionfinish(session, exitstatus):
    """Write the Test Execution Report from whatever actually ran."""
    if not _results:
        return
    ended = datetime.now()
    results = sorted(_results.values(), key=lambda r: (r.order, r.nodeid))
    for i, r in enumerate(results, start=1):
        r.tc_id = f"TC-{i:03d}"

    cfg = session.config
    browsers = cfg.getoption("browser", None) or []
    meta = dashboard.RunMeta(
        suite=" ".join(cfg.invocation_params.args) or "full_suite",
        module_label="SprintNXT Escrow - Admin & Partner",
        browser=", ".join(b.capitalize() for b in browsers) or "Chromium",
        env="UAT",
        by="QA Team",
        started=dashboard._stamp(_session_start),
        ended=dashboard._stamp(ended),
        duration_s=(ended - _session_start).total_seconds(),
        generated=dashboard._stamp(ended),
    )
    try:
        written = dashboard.write(results, meta)
        print(f"\nTest Execution Report: {written.resolve().as_uri()}")
    except Exception as exc:                      # never fail a run over a report
        print(f"\nCould not write dashboard: {exc!r}")
