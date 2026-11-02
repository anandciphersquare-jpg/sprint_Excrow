"""Test Execution Report - a self-contained dark dashboard built from a real run.

Collected by the hooks in the root `conftest.py` and written to
`reports/dashboard.html` at the end of every session. Nothing here invents
data: every row comes from a pytest report object.

Design notes (why it looks the way it does):

* Pass/Fail/Skip/Blocked are **status** colours, not a categorical series, so
  they use the reserved status palette. Green and red are only ~4 ΔE apart
  under deuteranopia - unavoidable for a pass/fail pair - so status is never
  carried by colour alone: every mark ships an icon, a text label and a direct
  value, there is a full table view, and print / forced-colors swap in 45°/135°
  hatching.
* Non-status tiles (Total, Duration, Modules) deliberately do NOT wear status
  hues, so a colour never impersonates a result.
* A single-status run degrades the donut to a hero number - a 1-slice pie is
  not a chart.
"""
from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import List

REPORT_PATH = Path("reports/dashboard.html")

# file stem -> (friendly module name, wizard order)
MODULE_NAMES = {
    "test_login": ("Login", 0),
    "test_escrow_details_validations": ("Stage 1 - Project Information", 1),
    "test_service_pricing": ("Stage 2 - Service Pricing", 2),
    "test_document_upload": ("Stage 3 - Documents", 3),
    "test_review_submit": ("Stage 4 - Review & Submit", 4),
    "test_create_escrow": ("End-to-End Creation", 5),
    "test_verify_escrow": ("Partner - Verify Escrow", 7),
    # Partner operations suite (partner_ops), ordered as the workflow runs.
    "test_login_and_session": ("Partner Ops - Login & Session", 8),
    "test_escrow_and_pricing": ("Partner Ops - Escrow & Service Pricing", 9),
    "test_beneficiary": ("Partner Ops - Beneficiary", 10),
    "test_payout": ("Partner Ops - Payout", 11),
    "test_reconciliation": ("Partner Ops - Wallet Reconciliation", 12),
    "test_virtual_id": ("Partner Ops - Virtual ID", 13),
    "test_bulk_and_collection": ("Partner Ops - Bulk & Collection", 14),
    "test_security": ("Partner Ops - Security", 15),
}

# marker -> display type, most specific first
TYPE_ORDER = ("security", "e2e", "create", "negative", "smoke", "regression")
TYPE_LABEL = {
    "security": "Security",
    "e2e": "End-to-End",
    "create": "Creates Data",
    "negative": "Negative",
    "smoke": "Smoke",
    "regression": "Functional",
}

ERROR_RULES = (
    (r"ConfigError|Required setting", "Configuration"),
    (r"Timeout.*exceeded|TimeoutError|timed out", "Timeout"),
    (r"strict mode violation|waiting for locator|not visible|no element", "Element Not Found"),
    (r"AssertionError|^assert ", "Assertion Failure"),
    (r"ConnectionError|ERR_|net::|DNS", "Network / Environment"),
)


def classify_error(text: str) -> str:
    for pattern, label in ERROR_RULES:
        if re.search(pattern, text, re.I | re.M):
            return label
    return "Other Error"


# =========================================================================
# Plain-English failure explanations
# =========================================================================
# A Playwright stack trace tells a developer almost nothing about *what the
# app did wrong*. Each rule below turns one recognised failure signature into
# language a developer can act on, plus a triage verdict for who should look
# first. These are **heuristics matched on the error text** - the report labels
# them as a best guess, never as a diagnosis.

# Angular control names -> what a human calls that field on screen.
FIELD_NAMES = {
    "excrowName": "Escrow Name",
    "excrow_id": "Escrow ID",
    "industry": "Industry",
    "bank_id": "Bank",
    "account_type": "Account Type",
    "accountNumber": "Account Number",
    "ifscCode": "IFSC Code",
    "email": "Representative Email",
    "fname": "Representative First Name",
    "lname": "Representative Last Name",
    "companyName": "Company Name",
    "mobileNumber": "Mobile Number",
    "flat": "Fixed charge amount",
    "flatCharge": "VID Creation fixed charge",
    "percent": "Percentage rate",
    "min": "Minimum charge limit",
    "max": "Maximum charge limit",
    "amount": "Maintenance charge amount",
    "Deductiondate": "Date of Deduction",
    "date": "Expiry Date",
    "name": "Document Name",
    "group:representatives": "the whole Company Representative section",
    "group:maintenance_charges": "the whole Bank & TSP maintenance charges section",
}

# owner: product | test | environment  -> drives the triage badge
OWNER_TEXT = {
    "product": ("Likely a PRODUCT bug", "The app behaved differently than the "
                "business rules say it should. A developer should look first."),
    "test": ("Likely a TEST / automation issue", "The app may be fine - the "
             "automation probably looked in the wrong place or moved too fast. "
             "QA should look first."),
    "environment": ("Likely an ENVIRONMENT / setup issue", "Nothing is wrong "
                    "with the code. The test could not reach or configure the "
                    "environment properly."),
    "unknown": ("Needs a human to triage", "The failure does not match a known "
                "pattern, so read the raw error below."),
}


def _friendly_fields(raw: str) -> List[str]:
    """`['ifscCode', 'group:maintenance_charges']` -> readable names."""
    out = []
    for item in re.findall(r"'([^']+)'", raw):
        pretty = FIELD_NAMES.get(item)
        if pretty:
            out.append(f"{pretty}  ({item})")
        else:
            out.append(item)
    return out


def explain(error: str, *, status: str, name: str, module: str,
            phase: str = "call") -> dict:
    """Turn a raw failure into a plain-English explanation."""
    if not error or status not in ("failed", "blocked"):
        return {}
    e = error

    def box(headline, meaning, causes, fixes, owner, detail=None):
        title, sub = OWNER_TEXT[owner]
        return {"headline": headline, "meaning": meaning, "causes": causes,
                "fixes": fixes, "owner": owner, "ownerTitle": title,
                "ownerNote": sub, "detail": detail or []}

    # ---- 1. the wizard refused to let us continue -----------------------
    m = re.search(r"'Next' stayed disabled.*?\[(.*?)\]", e, re.S)
    if m or "Please complete all required fields" in e:
        fields = _friendly_fields(m.group(1)) if m else []
        maintenance = any("maintenance_charges" in f for f in fields)
        causes = []
        if maintenance:
            causes += [
                "The Expiry Date is not far enough after the Date of Deduction. "
                "The app requires the escrow to outlast at least one full charge "
                "cycle - 1 month for Monthly, 3 for Quarterly, 12 for Annually.",
                "The charge frequency was changed *after* the dates were picked. "
                "That silently invalidates both dates while still showing the old "
                "text on screen, so the form looks complete but is not.",
            ]
        causes += [
            "A required field really is empty or holds a value the app rejects.",
            "A date was typed instead of picked from the calendar. Typing fills "
            "the box but leaves the field invalid.",
            "The app added a new required field that the automation does not fill "
            "yet (a product change, not a bug).",
        ]
        return box(
            "The wizard would not let the test move to the next stage - the "
            "'Next' button stayed greyed out.",
            "The test had filled everything it believes is required, so 'Next' "
            "should have become clickable. The app disagreed. Note the app never "
            "shows an error message here; it only greys out the button, which is "
            "why the test has to report the field names itself.",
            causes,
            ["Open the app and repeat the same steps by hand up to this stage.",
             "Check each field listed above - is it empty, or does it show a red "
             "message underneath?",
             "If it is the maintenance charges section: re-pick the Date of "
             "Deduction and Expiry Date *after* setting the frequency, and make "
             "sure the expiry is at least one full cycle later.",
             "If the field looks correctly filled on screen but is still "
             "reported invalid, that is a product bug - the app's validation is "
             "rejecting a valid value."],
            "product" if maintenance else "unknown",
            detail=fields)

    # ---- 2. an element never turned up ----------------------------------
    m = re.search(r'waiting for (locator\(.*?\)|.*?)(?:\n|$)', e)
    if re.search(r"Timeout \d+ms exceeded|TimeoutError", e) and m:
        target = m.group(1).strip()
        return box(
            "The test waited for something on the page and it never appeared.",
            f"It was waiting for: {target}. After the timeout it gave up. Either "
            "the element never rendered, or it rendered somewhere the test was not "
            "looking.",
            ["The page was slower than the timeout - UAT under load, or a slow API call.",
             "The app changed: the element was renamed, moved, or removed. The "
             "automation is now looking for something that no longer exists.",
             "An earlier step silently failed, so the app is on a different screen "
             "than the test thinks.",
             "A loading overlay or leftover dialog is covering the page and "
             "swallowing clicks."],
            ["Open the failure screenshot for this test (reports/screenshots/) - "
             "it shows exactly what was on screen when the test gave up.",
             "Compare the screenshot to what you expect at this stage.",
             "If the screen looks correct, the element was probably renamed - "
             "that is a locator update for QA, not a product bug.",
             "If the screen looks wrong or empty, work backwards: the real failure "
             "happened earlier."],
            "test")

    # ---- 3. selector matched more than one thing ------------------------
    if "strict mode violation" in e:
        n = re.search(r"resolved to (\d+) elements", e)
        return box(
            "The test's description of an element matched several elements at "
            "once, so it refused to guess which one to use.",
            f"{n.group(1) if n else 'Several'} elements matched. Playwright "
            "deliberately fails rather than picking one, because picking the "
            "wrong one would produce a confusing failure later.",
            ["The page now shows more than one control with the same label - "
             "often because a previous screen or dialog was left open.",
             "The app duplicated a section (for example two charge blocks) and "
             "the automation assumed one.",
             "The locator was always too loose and only worked by luck."],
            ["This is almost always an automation fix: make the locator specific "
             "(scope it to its card, section, or dialog).",
             "But first check the screenshot - if the app really is rendering "
             "the same field twice, that is a product bug."],
            "test")

    # ---- 4. the Escrow ID changed between form and confirmation ---------
    if "Escrow ID mismatch" in e or "dialog quoted" in e:
        return box(
            "The Escrow ID shown on the form did not match the ID the app "
            "confirmed at submit.",
            "Stage 1 displays an Escrow ID that the server generated. The "
            "confirmation dialog is supposed to repeat that same ID. It reported "
            "a different one, so it is unclear which ID the record was actually "
            "saved under.",
            ["The server issued a new ID at submit instead of using the reserved one.",
             "The reserved ID expired or was taken by another session in between.",
             "Two escrows were created and the dialog is showing the second one."],
            ["Treat this as a HIGH priority product issue - it affects which "
             "record the customer's money is tracked against.",
             "Check the server logs for both IDs and confirm how many records "
             "were actually created.",
             "Confirm whether the Stage 1 ID is genuinely reserved server-side, "
             "or only a suggestion the client sends back."],
            "product")

    # ---- 5. submit never confirmed --------------------------------------
    if "no creation confirmation" in e:
        return box(
            "The test submitted the escrow but the app never confirmed it was created.",
            "After the final Submit, the app should show a success message or "
            "return to the escrow dashboard. Neither happened within the timeout, "
            "so the test cannot tell whether the escrow was created or not.",
            ["The create request failed on the server (check for a 4xx/5xx response).",
             "The request succeeded but the success message never rendered.",
             "The session expired at the moment of submit."],
            ["Check the server logs for the create request at this timestamp.",
             "IMPORTANT: check whether a record was created anyway. A silent "
             "success with no confirmation is worse than a clean failure, because "
             "a real user would click Submit again and create a duplicate.",
             "Look at the failure screenshot to see what the user would have seen."],
            "product")

    # ---- 6. Account Type opened before a Bank was chosen ----------------
    if "Account Type has no options" in e or "No data available" in e:
        return box(
            "The Account Type list was empty.",
            "Account Type options are loaded from whichever Bank is selected, so "
            "the list reads 'No data available' until a Bank is chosen.",
            ["The Bank was not selected before Account Type was opened.",
             "The Bank was selected but the API call that loads the account types "
             "failed or was slow.",
             "The selected Bank genuinely has no account types configured in UAT."],
            ["Check that Bank is set before Account Type is touched.",
             "Open the browser network tab on the same steps and confirm the "
             "account-type call returns data for that bank.",
             "If the call returns an error or an empty list, it is a product or "
             "test-data problem, not an automation one."],
            "unknown")

    # ---- 7. datepicker could not reach the month ------------------------
    if "could not reach" in e and "datepicker" in e:
        return box(
            "The test could not navigate the calendar to the month it needed.",
            "It kept clicking the calendar's 'next month' arrow but never arrived "
            "at the target month, so it gave up.",
            ["The calendar has a maximum selectable date that blocks going that "
             "far ahead - likely for Annually, which needs about 14 months.",
             "The 'next' arrow became disabled or moved.",
             "The calendar opened in a different view (months or years instead of days)."],
            ["Try the same date by hand: can a real user reach that month at all?",
             "If not, the app's date limit conflicts with its own charge-cycle "
             "rule, which is a product bug worth reporting.",
             "If a user can reach it, the calendar navigation needs a fix in the "
             "automation."],
            "unknown")

    # ---- 8. configuration / credentials --------------------------------
    if re.search(r"ConfigError|Required setting", e):
        m = re.search(r"Required setting (\w+)", e)
        return box(
            f"The test could not start because a required setting "
            f"({m.group(1) if m else 'unknown'}) is missing.",
            "Credentials and URLs are read from a local .env file. One of them "
            "is missing or blank, so the run stopped immediately rather than "
            "failing later at the login screen.",
            ["`.env` does not exist yet - copy `.env.example` and fill it in.",
             "The value exists but is blank.",
             "CI is not supplying this value from its secret store."],
            ["Copy `.env.example` to `.env` and fill in the missing value.",
             "In CI, add the value to the pipeline's secrets.",
             "No code change is needed - this is purely setup."],
            "environment")

    # ---- 9. environment unreachable ------------------------------------
    if re.search(r"net::ERR_|ECONNREFUSED|ENOTFOUND|DNS|ERR_CONNECTION", e):
        return box(
            "The test could not reach the application at all.",
            "The browser failed to load the page - this is a connectivity problem, "
            "not a problem with the app's behaviour.",
            ["UAT is down or restarting.",
             "VPN or network access is not available from this machine.",
             "The URL in `.env` is wrong."],
            ["Open the portal URL in a normal browser from the same machine.",
             "If it does not load, stop the test run - every remaining test will "
             "fail for the same reason and tell you nothing new.",
             "No developer action needed until the environment is back."],
            "environment")

    # ---- 10. location permission ---------------------------------------
    if "Location access" in e or "geolocation" in e.lower():
        return box(
            "The app blocked the test with a 'Please allow the Location access!' message.",
            "Both portals ask the browser for the user's location at login and "
            "refuse to continue if it is denied.",
            ["The browser session did not grant the location permission.",
             "The app started asking for location on a screen where it did not before."],
            ["The test setup is supposed to grant location automatically - check "
             "that the browser context still sets the geolocation permission.",
             "This is an automation setup fix, not a product bug."],
            "test")

    # ---- 11. a plain expected-vs-actual comparison ----------------------
    m = re.search(r"^E?\s*assert\s+(.+?)\s*==\s*(.+?)$", e, re.M)
    if m or re.search(r"AssertionError", e):
        msg = ""
        am = re.search(r"AssertionError:\s*(.+)", e)
        if am:
            msg = am.group(1).strip()[:300]
        detail = []
        if m:
            detail = [f"The test got:      {m.group(1).strip()[:200]}",
                      f"It expected:       {m.group(2).strip()[:200]}"]
        return box(
            "The app produced a different result than the test expected.",
            (msg or "A value on screen did not match what the business rules say "
                    "it should be.") +
            " The test itself ran fine - it simply disagreed with what it found.",
            ["The app's behaviour genuinely changed (a bug, or an intended change "
             "nobody told QA about).",
             "The expected value in the test is out of date - for example the "
             "list of banks or industries changed.",
             "The test read the value too early, before the app finished updating it."],
            ["Compare the two values above. Which one is correct per the "
             "requirements?",
             "If the app is wrong -> product bug, assign to a developer.",
             "If the test's expectation is out of date -> update the test, and "
             "note the product change.",
             "If the values differ only by timing (blank vs filled), it is a "
             "wait problem in the test."],
            "unknown", detail=detail)

    # ---- 12. setup / fixture failure ------------------------------------
    if phase == "setup":
        return box(
            "The test never ran - the setup steps that get it to the right screen failed.",
            f"To test '{module}', the automation first has to log in and drive the "
            "wizard to that stage. Something broke during that preparation, so "
            "this test was never actually attempted. Do not read this as a "
            "failure of the thing it was meant to check.",
            ["An earlier wizard stage could not be completed - fix that first.",
             "Login failed or landed somewhere unexpected.",
             "UAT was slow or unavailable during setup."],
            ["Look for the FIRST failing test in this run - setup failures cascade, "
             "and usually one root cause explains many of them.",
             "Fix that root cause and re-run before investigating individually."],
            "environment")

    return box(
        "The test failed with an error that does not match any known pattern.",
        "There is no plain-English translation for this one yet - the raw error "
        "below is the best source.",
        ["Read the raw error and the failure screenshot."],
        ["If this error turns out to be common, add a rule for it in "
         "core/dashboard.py -> explain(), so the next person gets a clear "
         "explanation instead of a stack trace."],
        "unknown")


def humanise(nodeid: str) -> str:
    """`test_ifsc_is_upper_cased[chromium]` -> `IFSC is upper cased [chromium]`."""
    name = nodeid.split("::")[-1]
    params = ""
    if "[" in name:
        name, _, rest = name.partition("[")
        params = " [" + rest
    name = re.sub(r"^test_", "", name).replace("_", " ").strip()
    return (name[:1].upper() + name[1:]) + params


@dataclass
class TestResult:
    tc_id: str = ""
    nodeid: str = ""
    module: str = "Other"
    order: int = 99
    name: str = ""
    type: str = "Functional"
    status: str = "not_run"          # passed|failed|skipped|xfailed|xpassed|blocked|not_run
    duration: float = 0.0
    error: str = ""
    error_category: str = ""
    retries: int = 0
    explain: dict = field(default_factory=dict)


@dataclass
class RunMeta:
    suite: str = "full_suite"
    module_label: str = "Full Suite (End-to-End Pipeline)"
    browser: str = "Chromium"
    env: str = "UAT"
    by: str = "QA Team"
    started: str = ""
    ended: str = ""
    duration_s: float = 0.0
    generated: str = ""
    note: str = ""
    demo: bool = False


# --------------------------------------------------------------------------
def _fmt_duration(seconds: float) -> str:
    seconds = max(0.0, float(seconds))
    if seconds < 60:
        return f"{seconds:.0f}s" if seconds >= 1 else f"{seconds*1000:.0f}ms"
    m, s = divmod(int(round(seconds)), 60)
    if m < 60:
        return f"{m}m {s}s"
    h, m = divmod(m, 60)
    return f"{h}h {m}m"


def _stamp(dt: datetime) -> str:
    return dt.strftime("%d %b %Y, %I:%M:%S %p").replace(" 0", " ").lstrip("0")


def build_payload(results: List[TestResult], meta: RunMeta) -> dict:
    counts = {k: 0 for k in
              ("passed", "failed", "skipped", "blocked", "xfailed", "xpassed", "not_run")}
    for r in results:
        counts[r.status] = counts.get(r.status, 0) + 1

    executed = counts["passed"] + counts["failed"] + counts["xfailed"] + counts["xpassed"]
    pass_rate = (100.0 * (counts["passed"] + counts["xfailed"]) / executed) if executed else 0.0

    modules: dict = {}
    for r in results:
        m = modules.setdefault(r.module, {"name": r.module, "order": r.order,
                                          "passed": 0, "failed": 0, "skipped": 0,
                                          "blocked": 0, "xfailed": 0, "xpassed": 0,
                                          "not_run": 0, "total": 0, "duration": 0.0})
        m[r.status] = m.get(r.status, 0) + 1
        m["total"] += 1
        m["duration"] += r.duration

    failure_categories: dict = {}
    for r in results:
        if r.status == "failed" and r.error_category:
            failure_categories[r.error_category] = failure_categories.get(r.error_category, 0) + 1

    return {
        "meta": asdict(meta),
        "counts": counts,
        "executed": executed,
        "total": len(results),
        "passRate": round(pass_rate, 1),
        "modules": sorted(modules.values(), key=lambda m: (m["order"], m["name"])),
        "failureCategories": sorted(failure_categories.items(), key=lambda kv: -kv[1]),
        "tests": [asdict(r) for r in results],
    }


def render(results: List[TestResult], meta: RunMeta) -> str:
    payload = build_payload(results, meta)
    # This suite's own test IDs contain a literal `<script>alert('xss')</script>`
    # (the XSS parametrisation on stage 1), so a raw json.dumps would close this
    # page's <script> element and spill the payload into the document. `<\/` is
    # a valid JS string escape for `/`, and `<\!--` prevents the comment state.
    data = (json.dumps(payload)
            .replace("</", "<\\/")
            .replace("<!--", "<\\!--")
            .replace("\u2028", "\\u2028").replace("\u2029", "\\u2029"))
    return _TEMPLATE.replace("/*__DATA__*/", data)


def write(results: List[TestResult], meta: RunMeta,
          path: Path = REPORT_PATH) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render(results, meta), encoding="utf-8")
    return path


# --------------------------------------------------------------------------
_TEMPLATE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Test Execution Report - SprintNXT Escrow</title>
<style>
:root{
  color-scheme: dark;
  --plane:#0b1220; --surface:#161f2e; --surface-2:#1c2739; --raise:#223047;
  --ink:#f1f5f9; --ink-2:#a9b6c9; --muted:#6f7f96;
  --hair:rgba(255,255,255,.09); --hair-2:rgba(255,255,255,.16);
  /* status - reserved, never used for a non-status value */
  --pass:#0ca30c; --fail:#d03b3b; --skip:#fab219; --blocked:#9085e9; --xfail:#8b95a7;
  /* neutral metric accents - deliberately NOT status hues */
  --info:#3987e5; --time:#d55181; --mod:#199e70;
  --r:12px;
}
*{box-sizing:border-box}
body{margin:0;background:var(--plane);color:var(--ink);
  font:14px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif;
  -webkit-font-smoothing:antialiased}
.wrap{max-width:1280px;margin:0 auto;padding:0 20px 56px}
a{color:#6aa9f0;text-decoration:none}

/* ---------- header ---------- */
.hdr{background:linear-gradient(135deg,#152238 0%,#0f1a2b 60%,#122033 100%);
  border-bottom:1px solid var(--hair);margin-bottom:26px}
.hdr-in{max-width:1280px;margin:0 auto;padding:26px 20px 24px;
  display:flex;gap:24px;justify-content:space-between;flex-wrap:wrap}
h1{margin:0 0 6px;font-size:26px;letter-spacing:-.4px}
.spec{font-size:13px;color:#6aa9f0;font-family:ui-monospace,Consolas,monospace}
.chips{display:flex;gap:8px;flex-wrap:wrap;margin-top:14px}
.chip{display:inline-flex;align-items:center;gap:7px;background:var(--surface-2);
  border:1px solid var(--hair);border-radius:999px;padding:5px 12px;font-size:11.5px}
.chip b{color:var(--muted);font-weight:700;letter-spacing:.6px;text-transform:uppercase;font-size:10px}
.meta{background:var(--surface);border:1px solid var(--hair);border-radius:var(--r);
  padding:12px 16px;min-width:290px;align-self:flex-start}
.meta div{display:flex;justify-content:space-between;gap:24px;padding:4px 0;font-size:12px}
.meta span:first-child{color:var(--muted);letter-spacing:.6px;font-size:10px;
  text-transform:uppercase;font-weight:700;padding-top:2px}
.meta span:last-child{font-variant-numeric:tabular-nums}
.meta .hl{color:var(--pass);font-weight:700}

/* ---------- banner ---------- */
.banner{border-radius:var(--r);padding:13px 18px;margin-bottom:22px;font-size:13px;
  display:flex;gap:11px;align-items:flex-start;line-height:1.55}
.banner.warn{background:rgba(250,178,25,.10);border:1px solid rgba(250,178,25,.42);color:#f6ddab}
.banner.info{background:rgba(57,135,229,.10);border:1px solid rgba(57,135,229,.34);color:#bcd6f6}
.banner b{color:#fff}

/* ---------- tiles ---------- */
.tiles{display:grid;grid-template-columns:repeat(auto-fill,minmax(178px,1fr));gap:14px;margin-bottom:22px}
.tile{background:var(--surface);border:1px solid var(--hair);border-left:3px solid var(--c,var(--info));
  border-radius:var(--r);padding:15px 16px;display:flex;align-items:center;gap:13px}
.tile .ic{width:36px;height:36px;flex:0 0 36px;border-radius:9px;display:grid;place-items:center;
  background:color-mix(in srgb,var(--c,var(--info)) 16%,transparent);font-size:17px}
.tile .v{font-size:25px;font-weight:700;color:var(--c,var(--info));line-height:1.1}
.tile .l{font-size:10px;letter-spacing:.85px;text-transform:uppercase;color:var(--muted);
  font-weight:700;margin-top:3px}

/* ---------- panels ---------- */
.panels{display:grid;grid-template-columns:repeat(auto-fit,minmax(330px,1fr));gap:16px;margin-bottom:22px}
.panel{background:var(--surface);border:1px solid var(--hair);border-radius:var(--r);
  padding:16px 18px 18px;min-width:0}
.panel h2{margin:0;font-size:11px;letter-spacing:1px;text-transform:uppercase;
  color:var(--ink-2);font-weight:700;padding-bottom:11px;border-bottom:1px solid var(--hair)}
.plot{padding-top:16px;position:relative;overflow-x:auto}
.hero{text-align:center;padding:30px 0 18px}
.hero .n{font-size:42px;font-weight:700;line-height:1}
.hero .c{font-size:12px;color:var(--muted);margin-top:6px}
.legend{display:flex;gap:16px;flex-wrap:wrap;padding-top:14px;margin-top:12px;
  border-top:1px solid var(--hair);font-size:12px;color:var(--ink-2)}
.legend i{width:9px;height:9px;border-radius:2px;display:inline-block;margin-right:6px}
.empty{color:var(--muted);font-size:12.5px;padding:34px 0;text-align:center}
svg{display:block;max-width:100%}
/* A chart that must stay legible scrolls inside its card rather than
   shrinking its axis labels into unreadability. */
.plot svg.wide{max-width:none}
svg text{font:11px system-ui,-apple-system,"Segoe UI",sans-serif}
.tick{fill:var(--muted);font-variant-numeric:tabular-nums}
.grid{stroke:rgba(255,255,255,.07);stroke-width:1}
.axis{stroke:rgba(255,255,255,.16);stroke-width:1}
.vlab{fill:var(--ink);font-weight:600;font-variant-numeric:tabular-nums}
.mark{transition:opacity .12s}
.plot:hover .mark{opacity:.5}
.plot .mark:hover{opacity:1}

/* ---------- tooltip ---------- */
#tip{position:fixed;z-index:60;pointer-events:none;opacity:0;transition:opacity .1s;
  background:#0a1120;border:1px solid var(--hair-2);border-radius:9px;padding:9px 12px;
  font-size:12px;box-shadow:0 10px 30px rgba(0,0,0,.6);max-width:270px}
#tip .t{font-weight:700;margin-bottom:5px}
#tip .r{display:flex;justify-content:space-between;gap:18px;color:var(--ink-2)}
#tip .r b{color:var(--ink);font-variant-numeric:tabular-nums}

/* ---------- controls ---------- */
.controls{background:var(--surface);border:1px solid var(--hair);border-radius:var(--r);
  padding:13px 15px;display:flex;gap:11px;align-items:center;flex-wrap:wrap;margin-bottom:14px}
.search{flex:1 1 260px;position:relative;min-width:200px}
.search input{width:100%;background:var(--plane);border:1px solid var(--hair);border-radius:9px;
  padding:9px 12px 9px 34px;color:var(--ink);font-size:13px}
.search::before{content:"⌕";position:absolute;left:12px;top:7px;color:var(--muted);font-size:15px}
select,.btn{background:var(--plane);border:1px solid var(--hair);border-radius:9px;
  padding:9px 12px;color:var(--ink);font-size:12.5px;cursor:pointer}
.count{color:var(--muted);font-size:12px;margin-left:auto}
.sorts{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-bottom:16px;
  font-size:11px;color:var(--muted)}
.sorts b{letter-spacing:.8px;text-transform:uppercase;font-weight:700}
.sb{background:var(--surface-2);border:1px solid var(--hair);border-radius:999px;
  padding:5px 13px;font-size:12px;color:var(--ink-2);cursor:pointer}
.sb[aria-pressed=true]{background:var(--info);border-color:var(--info);color:#fff;font-weight:600}
input:focus-visible,select:focus-visible,button:focus-visible,summary:focus-visible{
  outline:2px solid var(--info);outline-offset:2px}

/* ---------- module groups ---------- */
.grp{background:var(--surface);border:1px solid var(--hair);border-left:3px solid var(--gc,var(--pass));
  border-radius:var(--r);margin-bottom:11px;overflow:hidden}
.grp>summary{list-style:none;cursor:pointer;padding:13px 16px;display:flex;align-items:center;
  gap:11px;flex-wrap:wrap}
.grp>summary::-webkit-details-marker{display:none}
.grp>summary::before{content:"▸";color:var(--muted);font-size:12px;transition:transform .15s}
.grp[open]>summary::before{transform:rotate(90deg)}
.gname{font-weight:700;font-size:14px}
.badges{display:flex;gap:7px;margin-left:auto;flex-wrap:wrap}
.b{border-radius:999px;padding:3px 10px;font-size:11px;border:1px solid var(--hair);
  background:var(--surface-2);color:var(--ink-2);font-variant-numeric:tabular-nums}
.b.p{color:var(--pass);border-color:color-mix(in srgb,var(--pass) 45%,transparent)}
.b.f{color:var(--fail);border-color:color-mix(in srgb,var(--fail) 45%,transparent)}

/* ---------- rows ---------- */
.row{display:flex;align-items:center;gap:11px;padding:11px 16px;border-top:1px solid var(--hair);
  flex-wrap:wrap}
.row:hover{background:var(--surface-2)}
.tcid{font-family:ui-monospace,Consolas,monospace;font-size:11px;color:var(--ink-2);
  background:var(--plane);border:1px solid var(--hair);border-radius:6px;padding:3px 8px;flex:0 0 auto}
.tname{flex:1 1 300px;font-size:13px;min-width:220px}
.tag{font-size:11px;border-radius:999px;padding:3px 10px;background:var(--surface-2);
  border:1px solid var(--hair);color:var(--ink-2);flex:0 0 auto}
.dur{font-size:11px;color:var(--muted);font-variant-numeric:tabular-nums;flex:0 0 auto}
.st{font-size:11px;font-weight:700;border-radius:999px;padding:3px 11px;flex:0 0 auto;
  display:inline-flex;align-items:center;gap:5px;letter-spacing:.4px}
.st.passed  {color:var(--pass);   background:color-mix(in srgb,var(--pass) 15%,transparent);   border:1px solid color-mix(in srgb,var(--pass) 45%,transparent)}
.st.failed  {color:var(--fail);   background:color-mix(in srgb,var(--fail) 15%,transparent);   border:1px solid color-mix(in srgb,var(--fail) 45%,transparent)}
.st.skipped {color:var(--skip);   background:color-mix(in srgb,var(--skip) 15%,transparent);   border:1px solid color-mix(in srgb,var(--skip) 45%,transparent)}
.st.blocked {color:var(--blocked);background:color-mix(in srgb,var(--blocked) 15%,transparent);border:1px solid color-mix(in srgb,var(--blocked) 45%,transparent)}
.st.xfailed,.st.xpassed,.st.not_run{color:var(--xfail);background:rgba(139,149,167,.13);
  border:1px solid rgba(139,149,167,.36)}
.err{margin-top:9px;background:var(--plane);border:1px solid var(--hair);
  border-left:3px solid var(--fail);border-radius:8px;padding:10px 13px;
  font-family:ui-monospace,Consolas,monospace;font-size:11.5px;color:#f0b8b8;
  white-space:pre-wrap;word-break:break-word;max-height:230px;overflow:auto}

/* ---------- plain-English failure explanation ---------- */
.why{flex:1 1 100%;margin-top:10px;background:var(--plane);border:1px solid var(--hair);
  border-radius:10px;padding:0;overflow:hidden}
.why-hd{display:flex;align-items:center;gap:9px;padding:10px 14px;
  border-bottom:1px solid var(--hair);flex-wrap:wrap}
.own{font-size:11px;font-weight:700;border-radius:999px;padding:3px 11px;letter-spacing:.3px}
.own.product    {color:var(--fail);   background:color-mix(in srgb,var(--fail) 15%,transparent);   border:1px solid color-mix(in srgb,var(--fail) 45%,transparent)}
.own.test       {color:var(--skip);   background:color-mix(in srgb,var(--skip) 15%,transparent);   border:1px solid color-mix(in srgb,var(--skip) 45%,transparent)}
.own.environment{color:var(--info);   background:color-mix(in srgb,var(--info) 15%,transparent);   border:1px solid color-mix(in srgb,var(--info) 42%,transparent)}
.own.unknown    {color:var(--xfail);  background:rgba(139,149,167,.14);border:1px solid rgba(139,149,167,.4)}
.own-note{font-size:11.5px;color:var(--muted)}
.why-bd{padding:13px 15px 15px;font-size:13px;line-height:1.62}
.why h4{margin:15px 0 6px;font-size:10px;letter-spacing:.9px;text-transform:uppercase;
  color:var(--muted);font-weight:700}
.why h4:first-child{margin-top:0}
.why p{margin:0;color:var(--ink)}
.why .lead{font-size:14px;font-weight:600;color:#fff;line-height:1.5}
.why ol,.why ul{margin:0;padding-left:20px;color:var(--ink-2)}
.why li{margin:4px 0}
.why .fields{list-style:none;padding:0;display:flex;flex-direction:column;gap:5px}
.why .fields li{background:var(--surface-2);border:1px solid var(--hair);border-radius:7px;
  padding:6px 11px;font-size:12.5px;color:var(--ink);font-family:ui-monospace,Consolas,monospace}
.why details{margin-top:14px;border-top:1px solid var(--hair);padding-top:11px}
.why summary{cursor:pointer;font-size:11.5px;color:var(--muted);list-style:none}
.why summary::-webkit-details-marker{display:none}
.why summary::before{content:"▸ ";}
.why details[open] summary::before{content:"▾ ";}
.why .err{margin-top:9px}

/* ---------- table view ---------- */
table{width:100%;border-collapse:collapse;font-size:12.5px}
th,td{text-align:left;padding:8px 11px;border-bottom:1px solid var(--hair)}
th{color:var(--muted);font-size:10px;letter-spacing:.7px;text-transform:uppercase}
td{font-variant-numeric:tabular-nums}
#tableview{background:var(--surface);border:1px solid var(--hair);border-radius:var(--r);
  padding:6px 10px 10px;margin-bottom:14px}
.foot{color:var(--muted);font-size:11.5px;text-align:center;margin-top:26px;line-height:1.7}
[hidden]{display:none !important}

/* print / forced-colors: hatching so status never rides on hue alone */
@media print,(forced-colors:active){
  body{background:#fff;color:#000}
  .panel,.tile,.grp,.controls,#tableview{background:#fff;border:1px solid #999}
  .mark[data-status=failed]{fill:url(#hatchA)}
  .mark[data-status=skipped]{fill:url(#hatchB)}
  #tip,.sorts{display:none}
}
</style></head><body>
<div id="tip" role="status" aria-live="polite"></div>

<svg width="0" height="0" style="position:absolute" aria-hidden="true"><defs>
  <pattern id="hatchA" patternUnits="userSpaceOnUse" width="6" height="6" patternTransform="rotate(45)">
    <rect width="6" height="6" fill="#fff"/><line x1="0" y1="0" x2="0" y2="6" stroke="#000" stroke-width="2.4"/></pattern>
  <pattern id="hatchB" patternUnits="userSpaceOnUse" width="6" height="6" patternTransform="rotate(135)">
    <rect width="6" height="6" fill="#fff"/><line x1="0" y1="0" x2="0" y2="6" stroke="#000" stroke-width="2.4"/></pattern>
</defs></svg>

<header class="hdr"><div class="hdr-in">
  <div>
    <h1>Test Execution Report</h1>
    <div class="spec" id="specName"></div>
    <div class="chips" id="chips"></div>
  </div>
  <div class="meta" id="metaBox"></div>
</div></header>

<div class="wrap">
  <div id="banners"></div>
  <div class="tiles" id="tiles"></div>

  <div class="panels">
    <section class="panel"><h2>Pass / Fail Distribution</h2><div class="plot" id="donut"></div></section>
    <section class="panel"><h2>Module-wise Results</h2><div class="plot" id="bars"></div></section>
    <section class="panel"><h2>Failure Categories</h2><div class="plot" id="cats"></div></section>
  </div>

  <div class="controls">
    <div class="search"><input id="q" type="search" placeholder="Search tests, IDs, features…" aria-label="Search tests"></div>
    <select id="fStatus" aria-label="Filter by status"><option value="">All Statuses</option></select>
    <select id="fModule" aria-label="Filter by module"><option value="">All Modules</option></select>
    <select id="fType"   aria-label="Filter by type"><option value="">All Types</option></select>
    <button class="btn" id="reset">Reset</button>
    <button class="btn" id="toggleTable" aria-pressed="false">Table view</button>
    <span class="count" id="count"></span>
  </div>

  <div class="sorts"><b>Sort by:</b>
    <button class="sb" data-sort="module"   aria-pressed="true">Module</button>
    <button class="sb" data-sort="status"   aria-pressed="false">Status</button>
    <button class="sb" data-sort="duration" aria-pressed="false">Duration</button>
    <button class="sb" data-sort="name"     aria-pressed="false">Name</button>
  </div>

  <div id="tableview" hidden></div>
  <div id="list"></div>

  <p class="foot" id="foot"></p>
</div>

<script>
const D = /*__DATA__*/;
const esc = s => String(s??"").replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
const C = {passed:"var(--pass)",failed:"var(--fail)",skipped:"var(--skip)",blocked:"var(--blocked)",
           xfailed:"var(--xfail)",xpassed:"var(--xfail)",not_run:"var(--xfail)"};
const ICON = {passed:"✓",failed:"✕",skipped:"⏭",blocked:"⊘",xfailed:"~",xpassed:"!",not_run:"·"};
const LABEL= {passed:"PASS",failed:"FAIL",skipped:"SKIP",blocked:"BLOCKED",
              xfailed:"XFAIL",xpassed:"XPASS",not_run:"NOT RUN"};
const dur = s => s<1 ? Math.round(s*1000)+"ms"
                : s<60 ? (s<10?s.toFixed(1):Math.round(s))+"s"
                : Math.floor(s/60)+"m "+String(Math.round(s%60)).padStart(2,"0")+"s";

/* ---------- header ---------- */
const M = D.meta;
specName.textContent = M.suite;
chips.innerHTML = [["Module",M.module_label],["Browser",M.browser],["Env",M.env],["By",M.by]]
  .map(([k,v])=>`<span class="chip"><b>${esc(k)}</b>${esc(v)}</span>`).join("");
metaBox.innerHTML = [["Started",M.started],["Ended",M.ended],
  ["Duration",`<span class="hl">${dur(M.duration_s)}</span>`],["Generated",M.generated]]
  .map(([k,v])=>`<div><span>${k}</span><span>${v}</span></div>`).join("");

/* ---------- banners ---------- */
const bn=[];
if(M.demo) bn.push(`<div class="banner warn"><span>⚠</span><div><b>LAYOUT PREVIEW - SAMPLE DATA. This is NOT a real test run.</b><br>
  Test names, IDs and module structure are the suite's genuine 87 tests, but every status, duration and error is placeholder
  content generated to show the layout populated. Do not quote any number on this page. The real report is
  <code>reports/dashboard.html</code>, written by an actual pytest run.</div></div>`);
if(M.note) bn.push(`<div class="banner info"><span>ℹ</span><div>${M.note}</div></div>`);
banners.innerHTML = bn.join("");

/* ---------- tiles ---------- */
const c=D.counts, rate=D.passRate;
const rateColor = D.executed===0 ? "var(--xfail)" : rate>=90?"var(--pass)":rate>=60?"var(--skip)":"var(--fail)";
const TILES=[
  ["📋",D.total,"Total Tests","var(--info)"],
  ["✓",c.passed,"Passed","var(--pass)"],
  ["✕",c.failed,"Failed","var(--fail)"],
  ["⏭",c.skipped,"Skipped","var(--skip)"],
  ["▦",(D.executed?rate.toFixed(1)+"%":"n/a"),"Pass Rate",rateColor],
  ["◷",dur(M.duration_s),"Duration","var(--time)"],
  ["🗀",D.modules.length,"Modules","var(--mod)"],
  ["⊘",c.blocked+c.not_run,"Blocked","var(--blocked)"],
];
tiles.innerHTML = TILES.map(([i,v,l,col])=>
 `<div class="tile" style="--c:${col}"><div class="ic">${i}</div>
   <div><div class="v">${esc(v)}</div><div class="l">${esc(l)}</div></div></div>`).join("");

/* ---------- tooltip ---------- */
const tip=document.getElementById("tip");
function bindTip(el,title,rows){
  const show=e=>{tip.innerHTML=`<div class="t">${title}</div>`+
      rows.map(([k,v])=>`<div class="r"><span>${k}</span><b>${v}</b></div>`).join("");
    tip.style.opacity=1;move(e);};
  const move=e=>{const p=e.touches?e.touches[0]:e, w=tip.offsetWidth,h=tip.offsetHeight;
    tip.style.left=Math.min(p.clientX+14,innerWidth-w-10)+"px";
    tip.style.top=Math.max(8,p.clientY-h-12)+"px";};
  el.addEventListener("mouseenter",show); el.addEventListener("mousemove",move);
  el.addEventListener("mouseleave",()=>tip.style.opacity=0);
  el.addEventListener("focus",e=>{const r=el.getBoundingClientRect();
    show({clientX:r.left+r.width/2,clientY:r.top});});
  el.addEventListener("blur",()=>tip.style.opacity=0);
  el.setAttribute("tabindex","0");
}

/* ---------- donut (or hero, when a pie would be degenerate) ---------- */
(function(){
  const segs=["passed","failed","skipped","blocked","xfailed","xpassed","not_run"]
    .map(k=>({k,v:c[k]||0})).filter(s=>s.v>0);
  const total=segs.reduce((a,s)=>a+s.v,0);
  if(!total){donut.innerHTML='<div class="empty">No tests were executed.</div>';return;}
  // A 1-slice pie is not a chart - show the number instead.
  if(segs.length===1){
    const s=segs[0];
    donut.innerHTML=`<div class="hero"><div class="n" style="color:${C[s.k]}">${s.v}</div>
      <div class="c">${ICON[s.k]} ${LABEL[s.k]} — all ${total} test${total>1?"s":""}</div></div>`;
    return;
  }
  const R=64,r=43,cx=92,cy=86;let a0=-Math.PI/2,paths="";
  segs.forEach(s=>{
    const a1=a0+2*Math.PI*s.v/total, big=(a1-a0)>Math.PI?1:0, gap=0.014;
    const b0=a0+gap,b1=Math.max(b0+0.001,a1-gap);
    const p=(rad,a)=>[cx+rad*Math.cos(a),cy+rad*Math.sin(a)];
    const [x1,y1]=p(R,b0),[x2,y2]=p(R,b1),[x3,y3]=p(r,b1),[x4,y4]=p(r,b0);
    paths+=`<path class="mark" data-status="${s.k}" data-i="${s.k}" fill="${C[s.k]}"
      d="M${x1},${y1} A${R},${R} 0 ${big} 1 ${x2},${y2} L${x3},${y3} A${r},${r} 0 ${big} 0 ${x4},${y4} Z"/>`;
    a0=a1;});
  const headline = D.executed? D.passRate.toFixed(1)+"%" : "—";
  donut.innerHTML=`<svg viewBox="0 0 184 172" style="margin:0 auto;max-width:230px" role="img"
      aria-label="Result distribution: ${segs.map(s=>LABEL[s.k]+" "+s.v).join(", ")}">
    ${paths}<text x="${cx}" y="${cy-1}" text-anchor="middle" fill="var(--ink)"
      style="font-size:21px;font-weight:700">${headline}</text>
    <text x="${cx}" y="${cy+15}" text-anchor="middle" class="tick">Pass Rate</text></svg>
   <div class="legend">${segs.map(s=>
      `<span><i style="background:${C[s.k]}"></i>${ICON[s.k]} ${LABEL[s.k]} (${s.v})</span>`).join("")}</div>`;
  donut.querySelectorAll("path.mark").forEach(p=>{
    const s=segs.find(x=>x.k===p.dataset.i);
    bindTip(p,`${ICON[s.k]} ${LABEL[s.k]}`,[["Tests",s.v],["Share",(100*s.v/total).toFixed(1)+"%"]]);});
})();

/* ---------- module-wise grouped bars ---------- */
(function(){
  const mods=D.modules; if(!mods.length){bars.innerHTML='<div class="empty">No modules.</div>';return;}
  const keys=["passed","failed","skipped","blocked","not_run"].filter(k=>mods.some(m=>m[k]>0));
  const W=Math.max(340,mods.length*Math.max(78,keys.length*26)),H=210,L=34,B=52,T=12;
  const max=Math.max(1,...mods.map(m=>Math.max(...keys.map(k=>m[k]))));
  const pw=(W-L-8)/mods.length, bw=Math.min(20,(pw-14)/keys.length);
  let g="",bs="";
  const ticks=max<=2?[0,1,2]:[0,Math.round(max/2),max];
  [...new Set(ticks)].forEach(t=>{const y=T+(H-T-B)*(1-t/max);
    g+=`<line class="grid" x1="${L}" y1="${y}" x2="${W-6}" y2="${y}"/>
        <text class="tick" x="${L-7}" y="${y+4}" text-anchor="end">${t}</text>`;});
  mods.forEach((m,i)=>{
    const x0=L+i*pw+(pw-bw*keys.length-2*(keys.length-1))/2;
    keys.forEach((k,j)=>{
      const v=m[k]||0, h=(H-T-B)*v/max, x=x0+j*(bw+2), y=T+(H-T-B)-h;
      if(v>0) bs+=`<rect class="mark" data-status="${k}" data-m="${i}" data-k="${k}"
          x="${x}" y="${y}" width="${bw}" height="${Math.max(2,h)}" rx="4" fill="${C[k]}"/>
        <text class="vlab" x="${x+bw/2}" y="${y-5}" text-anchor="middle">${v}</text>`;});
    const label=m.name.length>17?m.name.slice(0,16)+"…":m.name;
    bs+=`<text class="tick" x="${L+i*pw+pw/2}" y="${H-B+18}" text-anchor="middle">${esc(label)}</text>
         <text class="tick" x="${L+i*pw+pw/2}" y="${H-B+32}" text-anchor="middle"
           style="fill:var(--ink-2)">${m.total}</text>`;});
  bars.innerHTML=`<svg class="wide" viewBox="0 0 ${W} ${H}" width="${W}" height="${H}" role="img"
      aria-label="Results per module">${g}
    <line class="axis" x1="${L}" y1="${T+(H-T-B)}" x2="${W-6}" y2="${T+(H-T-B)}"/>${bs}</svg>
   <div class="legend">${keys.map(k=>
      `<span><i style="background:${C[k]}"></i>${ICON[k]} ${LABEL[k]}</span>`).join("")}</div>`;
  bars.querySelectorAll("rect.mark").forEach(rc=>{
    const m=mods[+rc.dataset.m],k=rc.dataset.k;
    bindTip(rc,esc(m.name),[[`${ICON[k]} ${LABEL[k]}`,m[k]],["Module total",m.total],
                            ["Module time",dur(m.duration)]]);});
})();

/* ---------- failure categories: one series, one colour ---------- */
(function(){
  const f=D.failureCategories;
  if(!f.length){cats.innerHTML='<div class="empty">✓ No failures recorded.</div>';return;}
  const max=Math.max(...f.map(x=>x[1])),rowH=34,L=132,W=420,H=f.length*rowH+16;
  let b="";
  f.forEach(([name,n],i)=>{
    const y=i*rowH+8,w=Math.max(3,(W-L-46)*n/max);
    const label=name.length>19?name.slice(0,18)+"…":name;
    b+=`<text class="tick" x="${L-9}" y="${y+17}" text-anchor="end" style="fill:var(--ink-2)">${esc(label)}</text>
      <rect class="mark" data-status="failed" data-i="${i}" x="${L}" y="${y+3}" width="${w}"
        height="19" rx="4" fill="var(--fail)"/>
      <text class="vlab" x="${L+w+8}" y="${y+17}">${n}</text>`;});
  cats.innerHTML=`<svg viewBox="0 0 ${W} ${H}" width="${W}" role="img"
      aria-label="Failures by category">${b}</svg>`;
  const tot=f.reduce((a,x)=>a+x[1],0);
  cats.querySelectorAll("rect.mark").forEach(rc=>{
    const [n,v]=f[+rc.dataset.i];
    bindTip(rc,esc(n),[["Failures",v],["Share of failures",(100*v/tot).toFixed(0)+"%"]]);});
})();

/* ---------- filters, sort, list ---------- */
const T=D.tests;
const uniq=(k)=>[...new Set(T.map(t=>t[k]))].sort();
uniq("status").forEach(s=>fStatus.insertAdjacentHTML("beforeend",`<option value="${s}">${LABEL[s]||s}</option>`));
D.modules.forEach(m=>fModule.insertAdjacentHTML("beforeend",`<option value="${esc(m.name)}">${esc(m.name)}</option>`));
uniq("type").forEach(s=>fType.insertAdjacentHTML("beforeend",`<option value="${esc(s)}">${esc(s)}</option>`));
let sortKey="module";

function current(){
  const q=document.getElementById("q").value.trim().toLowerCase();
  let rows=T.filter(t=>
    (!q || (t.name+" "+t.tc_id+" "+t.module+" "+t.nodeid).toLowerCase().includes(q)) &&
    (!fStatus.value||t.status===fStatus.value) &&
    (!fModule.value||t.module===fModule.value) &&
    (!fType.value  ||t.type===fType.value));
  const rank={failed:0,blocked:1,skipped:2,not_run:3,xpassed:4,xfailed:5,passed:6};
  if(sortKey==="status")   rows.sort((a,b)=>(rank[a.status]??9)-(rank[b.status]??9));
  if(sortKey==="duration") rows.sort((a,b)=>b.duration-a.duration);
  if(sortKey==="name")     rows.sort((a,b)=>a.name.localeCompare(b.name));
  if(sortKey==="module")   rows.sort((a,b)=>a.order-b.order||a.tc_id.localeCompare(b.tc_id));
  return rows;
}
function whyHTML(t){
  const x=t.explain;
  if(!x||!x.headline) return t.error?`<pre class="err" style="flex:1 1 100%">${esc(t.error)}</pre>`:"";
  const list=(arr,tag)=>`<${tag}>${arr.map(i=>`<li>${esc(i)}</li>`).join("")}</${tag}>`;
  return `<div class="why">
    <div class="why-hd">
      <span class="own ${x.owner}">${esc(x.ownerTitle)}</span>
      <span class="own-note">${esc(x.ownerNote)}</span>
    </div>
    <div class="why-bd">
      <h4>What happened</h4><p class="lead">${esc(x.headline)}</p>
      <h4>What this means</h4><p>${esc(x.meaning)}</p>
      ${x.detail&&x.detail.length?`<h4>The app flagged these as still invalid</h4>
        ${list(x.detail,"ul").replace('<ul>','<ul class="fields">')}`:""}
      <h4>Most likely causes — check in this order</h4>${list(x.causes,"ol")}
      <h4>How to check &amp; fix</h4>${list(x.fixes,"ol")}
      <details><summary>Show the raw error the test produced</summary>
        <pre class="err">${esc(t.error)}</pre></details>
    </div></div>`;
}
function rowHTML(t){
  return `<div class="row">
    <span class="tcid">${esc(t.tc_id)}</span>
    <span class="tname">${esc(t.name)}</span>
    ${t.retries?`<span class="tag" title="Retries">↻${t.retries}</span>`:""}
    <span class="tag">${esc(t.type)}</span>
    <span class="dur">${dur(t.duration)}</span>
    <span class="st ${t.status}">${ICON[t.status]} ${LABEL[t.status]||t.status}</span>
    ${whyHTML(t)}</div>`;
}
function draw(){
  const rows=current();
  count.textContent=`Showing ${rows.length} of ${T.length} tests`;
  const groups=new Map();
  rows.forEach(t=>{(groups.get(t.module)||groups.set(t.module,[]).get(t.module)).push(t);});
  list.innerHTML=[...groups.entries()].map(([name,items])=>{
    const p=items.filter(t=>t.status==="passed").length;
    const f=items.filter(t=>t.status==="failed").length;
    const ex=items.filter(t=>["passed","failed","xfailed","xpassed"].includes(t.status)).length;
    const d=items.reduce((a,t)=>a+t.duration,0);
    return `<details class="grp" ${f?"open":""} style="--gc:${f?"var(--fail)":p?"var(--pass)":"var(--xfail)"}">
      <summary><span class="gname">${esc(name)}</span><div class="badges">
        <span class="b">${items.length} Tests</span>
        <span class="b p">${p} Pass</span>
        <span class="b f">${f} Fail</span>
        <span class="b">${ex?Math.round(100*p/ex):0}%</span>
        <span class="b">${dur(d)}</span></div></summary>
      ${items.map(rowHTML).join("")}</details>`;}).join("")
    || `<div class="panel"><div class="empty">No tests match these filters.</div></div>`;
  tableview.innerHTML=`<table><caption class="empty" style="padding:8px 0;text-align:left">
      Table view — every value on this page, without relying on colour.</caption>
    <thead><tr><th>ID</th><th>Module</th><th>Test</th><th>Type</th><th>Status</th><th>Duration</th></tr></thead>
    <tbody>${rows.map(t=>`<tr><td>${esc(t.tc_id)}</td><td>${esc(t.module)}</td><td>${esc(t.name)}</td>
      <td>${esc(t.type)}</td><td>${ICON[t.status]} ${LABEL[t.status]||t.status}</td>
      <td>${dur(t.duration)}</td></tr>`).join("")}</tbody></table>`;
}
["q","fStatus","fModule","fType"].forEach(id=>{
  const el=document.getElementById(id);
  el.addEventListener(id==="q"?"input":"change",draw);});
reset.onclick=()=>{q.value="";fStatus.value="";fModule.value="";fType.value="";draw();};
toggleTable.onclick=()=>{const on=tableview.hasAttribute("hidden");
  tableview.toggleAttribute("hidden",!on);list.toggleAttribute("hidden",on);
  toggleTable.setAttribute("aria-pressed",String(on));};
document.querySelectorAll(".sb").forEach(b=>b.onclick=()=>{
  sortKey=b.dataset.sort;
  document.querySelectorAll(".sb").forEach(x=>x.setAttribute("aria-pressed",String(x===b)));
  draw();});
draw();
foot.innerHTML = `${D.total} tests · ${D.modules.length} modules · generated ${esc(M.generated)}`
  + `<br>SprintNXT Escrow automation — reports/dashboard.html`;
</script></body></html>
"""
