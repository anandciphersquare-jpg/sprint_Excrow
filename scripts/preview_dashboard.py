#!/usr/bin/env python3
"""Render a LAYOUT PREVIEW of the Test Execution Report.

    python scripts/preview_dashboard.py

Why this exists: a full run takes ~85 minutes, needs a live UAT and creates 15
permanent escrow records, so there is no cheap way to see the dashboard fully
populated. This collects the suite's **real** test names, modules, markers and
IDs, then attaches **placeholder** statuses/durations purely so the layout can
be reviewed.

The output is written to reports/dashboard_preview.html and carries a permanent,
non-dismissible banner saying it is not a real run. Nothing here should ever be
quoted as a result. The real report is reports/dashboard.html, written by
conftest.py at the end of an actual pytest session.
"""
from __future__ import annotations

import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from core import dashboard

OUT = Path("reports/dashboard_preview.html")

# Deterministic, so re-running does not churn the file.
RNG = random.Random(20260805)


class _Collector:
    """Grabs the real collected items without executing any of them."""

    def __init__(self) -> None:
        self.items: list = []

    def pytest_collection_modifyitems(self, items):
        self.items.extend(items)


def collect() -> list:
    plugin = _Collector()
    code = pytest.main(
        ["--collect-only", "-q", "--no-header", "-p", "no:cacheprovider",
         "--html=" + str(Path("reports/_preview_collect.html"))],
        plugins=[plugin],
    )
    if code not in (0, 5):
        raise SystemExit(f"collection failed (exit {code})")
    Path("reports/_preview_collect.html").unlink(missing_ok=True)
    return plugin.items


# Placeholder outcome weights - shaped to exercise every visual state
# (pass, fail, skip, blocked, xfail) so the layout can be judged. These are
# NOT predictions and NOT results.
def placeholder_status(kind: str, name: str) -> str:
    if "unsupported file type" in name.lower() or "oversize file" in name.lower():
        return "xfailed"                       # the suite's two real xfail tests
    roll = RNG.random()
    if kind == "Creates Data":
        return "passed" if roll < 0.80 else ("failed" if roll < 0.93 else "blocked")
    if kind in ("Negative", "Security"):
        return "passed" if roll < 0.88 else "failed"
    if roll < 0.86:
        return "passed"
    if roll < 0.95:
        return "failed"
    return "skipped" if roll < 0.98 else "blocked"


SAMPLE_ERRORS = {
    "failed": (
        "AssertionError: 'Next' stayed disabled; still-invalid controls: "
        "['group:maintenance_charges']\n"
        "  [PLACEHOLDER TEXT - this is a layout preview, not a real failure]"),
    "blocked": (
        "playwright._impl._errors.TimeoutError: Timeout 30000ms exceeded.\n"
        "  waiting for locator(\"input[formcontrolname='excrow_id']\")\n"
        "  [PLACEHOLDER TEXT - this is a layout preview, not a real failure]"),
}


def main() -> int:
    items = collect()
    if not items:
        print("No tests collected.")
        return 1

    results: list[dashboard.TestResult] = []
    for i, item in enumerate(items, start=1):
        stem = item.nodeid.split("::")[0].rsplit("/", 1)[-1].removesuffix(".py")
        module, order = dashboard.MODULE_NAMES.get(stem, (stem, 99))
        if "partner/" in item.nodeid and stem == "test_login":
            module, order = "Partner - Login", 6

        markers = {m.name for m in item.iter_markers()}
        kind = next((dashboard.TYPE_LABEL[t] for t in dashboard.TYPE_ORDER
                     if t in markers), "Functional")
        name = dashboard.humanise(item.nodeid)
        status = placeholder_status(kind, name)

        base = {"Creates Data": 100.0, "End-to-End": 95.0}.get(kind, 34.0)
        results.append(dashboard.TestResult(
            nodeid=item.nodeid, module=module, order=order, name=name, type=kind,
            status=status,
            duration=0.0 if status == "not_run" else round(RNG.uniform(.55, 1.5) * base, 1),
            error=SAMPLE_ERRORS.get(status, ""),
            error_category=(
                "Setup / Fixture Error" if status == "blocked"
                else dashboard.classify_error(SAMPLE_ERRORS["failed"]) if status == "failed"
                else ""),
            retries=1 if status == "failed" and RNG.random() < 0.3 else 0,
        ))
        results[-1].explain = dashboard.explain(
            results[-1].error, status=status, name=name, module=module,
            phase="setup" if status == "blocked" else "call")

    results.sort(key=lambda r: (r.order, r.nodeid))
    for i, r in enumerate(results, start=1):
        r.tc_id = f"TC-{i:03d}"

    total = sum(r.duration for r in results)
    meta = dashboard.RunMeta(
        suite="LAYOUT PREVIEW - not a real run",
        module_label="SprintNXT Escrow - Admin & Partner",
        browser="Chromium", env="UAT", by="QA Team",
        started="—", ended="—", duration_s=total,
        generated=dashboard._stamp(__import__("datetime").datetime.now()),
        demo=True,
    )
    path = dashboard.write(results, meta, OUT)
    print(f"Layout preview ({len(results)} real test names, placeholder statuses): "
          f"{path.resolve().as_uri()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
