# SprintNXT Escrow Automation

Python + Playwright + pytest suite for the SprintNXT Escrow workflow.
The **admin** (Super Admin) portal creates an Escrow ID; the **partner**
portal searches and verifies it. A **trustee** scaffold is reserved for later.

> **Every change to this suite is logged in [CHANGELOG.md](CHANGELOG.md)**, newest
> at the bottom, with the finding that prompted it and how it was verified. Add
> an entry there as part of any change.

## Layout

```
sprintnxt-escrow-automation/
├── conftest.py               # root fixtures: context args, screenshot-on-fail, artifact store
├── pytest.ini                # markers, pythonpath, HTML report
├── requirements.txt
├── .env.example              # copy to .env
├── config/settings.py        # URLs + credentials (env-driven, UAT defaults baked in)
├── core/
│   ├── base_page.py          # resilient Playwright wrappers
│   ├── errors.py             # console / pageerror / 4xx monitor
│   ├── artifact_store.py     # hands the Escrow ID from admin -> partner
│   └── file_factory.py       # generates sample pdf/png/jpg (+ unsupported/oversize)
├── admin/
│   ├── conftest.py           # admin_page, logged_in_admin_page
│   ├── pages/                # login + one page object per wizard stage + orchestrator
│   ├── data/escrow_data.py   # test payload (random name/account/IFSC, IDFC, pricing, docs)
│   └── tests/                # one file per wizard stage, plus login + e2e:
│       ├── test_login.py
│       ├── test_escrow_details_validations.py   # stage 1 +/-
│       ├── test_service_pricing.py              # stage 2 +/-
│       ├── test_document_upload.py              # stage 3 +/-
│       ├── test_review_submit.py                # stage 4 +/-
│       └── test_create_escrow.py                # full happy path (e2e)
├── partner/
│   ├── conftest.py           # partner_page, logged_in_partner_page
│   ├── pages/                # login, escrow_management (search), escrow_details (verify)
│   └── tests/                # test_login, test_verify_escrow (e2e)
├── trustee/                  # future portal (mirrors admin/partner)
├── scripts/run_e2e.py        # admin create -> partner verify, in one command
├── fixtures/documents/       # generated sample upload files
└── reports/                  # HTML report + failure screenshots
```

## Setup

```bash
python -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r requirements.txt
playwright install                                    # downloads browsers
cp .env.example .env                                  # then edit if needed
```

Python 3.10+.

## Run

```bash
pytest                              # everything (headless)
pytest --headed --slowmo 200        # watch it, slowed down
pytest -m smoke                     # logins + nav only
pytest -m admin                     # admin suite
pytest -m partner                   # partner suite
pytest admin/tests/test_create_escrow.py   # one file
pytest --browser firefox            # chromium | firefox | webkit

# full cross-portal chain (admin creates, partner verifies the same id):
python scripts/run_e2e.py --headed
```

## The full chain, in two separable phases

`scripts/run_chain.py` is the whole workflow end to end, split so each half can
be run on its own:

| Phase | What it does |
|---|---|
| `admin` | Signs in as Super Admin **once** and creates one escrow per create-case — 14 of them: the default payload, all 7 Account Type combinations, and Fixed × Percentage across each charge frequency. |
| `partner` | Searches Gmail for the chain escrow's welcome mail, reads the temporary password out of it, signs the partner in **through the link in that mail**, drives the forced password change, signs in again with the new password, then runs the `partner/` and `partner_ops/` suites as that new account. |

```bash
python scripts/run_chain.py --list-variants        # the 14 create cases
python scripts/run_chain.py --phase all            # both halves
python scripts/run_chain.py --phase admin          # create everything, stop
python scripts/run_chain.py --phase partner        # mail -> login -> modules

# validate the wizard first: every positive and negative stage case, the
# Review back/edit round-trip and the dashboard-list edit - none of which
# creates anything - and only then file records against it
python scripts/run_chain.py --phase admin --with-admin-suite --suite-gates-creates

# file the chain escrow under a partner who is already onboarded: no
# credentials are issued and the partner phase never opens Gmail
python scripts/run_chain.py --phase all \
    --representative anand.kumar+new6633@paysprint.in

# one variant, driven to Review and never submitted - leaves no UAT record
python scripts/run_chain.py --phase admin --dry-run --variants happy

# the partner half against an escrow this script never created
python scripts/run_chain.py --phase partner \
    --escrow-id EXC-0826-962517 --email anand.kumar+new4494@paysprint.in
```

The two phases hand over through `shared/chain_state.json`, so the partner half
can run minutes or days after the admin half, as often as you like, without
creating another escrow. Results land in `reports/chain_run.json`, and the
module suites' own report in `reports/chain_modules.html`.

Things worth knowing before changing it:

- **Exactly one escrow gets a brand-new representative** (`--chain-variant`,
  default `happy`). All 14 are created, but only that one is filed under a fresh
  email/company/name/mobile, so only it produces a welcome mail carrying a User
  ID and temporary password — the rest use `REPRESENTATIVE_EMAIL`, which the
  portal auto-fills. A fresh representative per variant would leave 14 new
  partner accounts in shared UAT on every run.
- **The partner phase is resumable.** Once the password has been changed, the
  temporary one is dead and the portal stops forcing the set-password screen, so
  a re-run picks up at the login rather than replaying a change that cannot
  happen twice. `--force-password-change` overrides that.
- **The mailbox is read once per partner, not once per run.** The temporary
  password only ever appears in the welcome mail, so the *first* sign-in for a
  new representative needs Gmail. The password that replaces it is one this
  suite chose, and it is saved per representative in
  `shared/partner_credentials.json` (`core/credentials.py`). Every later run for
  that partner signs straight in with it — no Gmail. `chain_state.json` keeps
  only the most recent escrow, so it alone could not do this: the next
  `--phase admin` run overwrote the previous partner's password, and there was
  no way back in because the temporary one is single-use.
- **The login OTP is UAT's fixed `1234`, not a mail read.** Set
  `PARTNER_OTP_FROM_MAIL=true` to read a real code from Gmail instead; it is off
  by default because it costs a mailbox round-trip per sign-in and makes every
  login depend on a live Gmail session. A mailbox failure falls back to the
  fixed code rather than taking the login down. Because a second Playwright
  driver cannot run inside the first, that read happens in a separate process
  (`python -m core.mailbox --otp --json`).
- **`--dry-run` takes one variant at a time.** An unsubmitted wizard is
  persisted as a draft and "Add New Escrow" *resumes* it (DEF-ADMIN-03), so a
  looping dry run would re-test the first variant's payload every time. The real
  path is unaffected — a submitted escrow leaves no draft.
- **Nothing spends the wallet by default.** `--spend` lifts both
  `PARTNER_OPS_SPEND` and `PARTNER_OPS_CREATE_DATA`.
- **The mail leg needs a live Gmail session.** If `.auth/gmail-profile` has
  expired the mail cases report BLOCKED with a pointer to
  `python scripts/save_gmail_session.py`, rather than failing as though the
  product were broken.

pytest-playwright flags available everywhere: `--headed`, `--slowmo`,
`--browser`, `--tracing on`, `--video on`, `--screenshot on`.

HTML report: `reports/report.html`. Failure screenshots: `reports/screenshots/`.

## How the Escrow ID flows admin -> partner

`test_create_escrow` captures the generated ID from the success screen and
writes it to `shared/artifacts.json` via `ArtifactStore`. `test_verify_escrow`
reads it (or an `ESCROW_ID` env var override) and skips cleanly if neither is
present — so each suite still runs on its own.

## The admin wizard, as the live portal actually builds it

The admin suite is calibrated against UAT. Things worth knowing before
changing it — each one cost a debugging cycle to find:

- **Geolocation is mandatory.** Both portals call `navigator.geolocation` on
  login and block with *"Please allow the Location access!"* if it is denied,
  so the root `browser_context_args` grants it. Without that, login fails in a
  way that looks like a selector problem.
- **No PIN step.** The superadmin account goes straight to `#/dashboard`;
  `AdminLoginPage.enter_pin` fills a PIN only if one is actually prompted.
- **The module is spelled "Excrow"** in the UI and in control names
  (`excrowName`, `#/excrow/dashboard`), so label matching allows both spellings.
- **The wizard is four stages**, not ten: Project Information → Service
  Pricing → Documents → Review. Stage 1 carries the escrow details, the company
  representative and the trustee choice together.
- **The Escrow ID is known up front** — stage 1 renders it readonly in
  `excrow_id`, and the pre-submit confirmation dialog echoes it. No need to
  scrape a success screen.
- **Account Type options are loaded per bank** (`ng-multiselect-dropdown`), so
  Bank must be selected first or the list reads "No data available". It is a
  multiselect, and **the combination chosen decides which Service Pricing
  sections appear**: Payout brings IMPS / NEFT / RTGS, VID Collection adds
  "Collection via VID" and "VID Creation". The default payload picks
  Payout + VID Collection.
- **Each transfer mode is charged Fixed or Percentage, and that swaps its
  inputs.** Fixed shows `flat` charges (IMPS has three slabs); Percentage
  replaces them with `percent` plus `min` / `max` charge bounds. VID Creation is
  Fixed-only - its radio is pre-selected *and* disabled, so it must not be
  clicked at.
- **The Expiry Date must outlast one charge cycle.** An expiry two months out
  satisfies Monthly but leaves Quarterly and Annually invalid, so
  `EXPIRY_MONTHS_AHEAD` scales it per frequency.
- **Changing the frequency re-invalidates the dates without clearing them.**
  The boxes keep their text and no field is flagged - only the
  `maintenance_charges` *group* goes invalid - so the dates must be picked
  again. Hence `invalid_controls()` reports groups as `group:<name>`, and
  frequency is always set before dates.
- **Quarterly reveals read-only Q1-Q4 tiles**, computed from the deduction
  date; they are displays, not controls. Annually just relabels the recurrence
  to "Every Year".
- **Datepicker navigation is absolute.** Re-opening a picker on a field that
  already holds a date starts on *that* month, so counting "next" clicks from
  wherever it opened lands on a different month each run; `pick_bs_date` reads
  the calendar header and navigates to a month computed from today.
- **`Next` is disabled, not error-flagged**, until a stage is complete;
  `BasePage.continue_next` reports the still-invalid `formcontrolname`s when it
  refuses to enable.
- **Dates are ngx-bootstrap datepickers.** Typing sets the text but leaves the
  control invalid — selection must go through the calendar
  (`BasePage.pick_bs_date`), and past days are `.disabled` on the inner
  `<span>`.
- **Documents go through a modal.** "Upload Your First Document" opens *Add New
  Document* (file + required name + optional dates) and one saved document is
  required to advance.
- **Submit is two clicks:** "Submit Application" opens an "Are You Sure?"
  dialog whose own Submit actually creates the record. Deleting a document uses
  the same dialog shape, so the two are told apart by their wording.
- **Review does not repeat the Escrow ID** - it reappears only in the
  confirmation dialog.
- **Each review section has its own Edit link**, and it is an `<a>` with no
  `href`. That carries no `link` role, so `get_by_role("button", name="Edit")`
  finds nothing and a test asserting "there is no Edit control" passes for the
  wrong reason. Match the markup (`a:has(.icon-edit)`) instead. Editing reopens
  the form with the entered values intact, and Next walks forward again.
- **A submitted escrow is edited from its dashboard row**, and that row action
  is an `<i class="icon-edit">` — not an anchor, not a button, so it has no role
  either. It routes to `#/excrow/add-excrow/<ESCROW-ID>`: the same four-stage
  wizard reopened on **Review**, with all three sections and their Edit links.
  There is no separate edit screen. The summary is filled from an API call a
  beat *after* the route and the loader have both settled, so reading it
  straight after the click finds an empty page some of the time —
  `open_escrow_for_edit()` waits for the sections. Covered by
  `admin/tests/test_dashboard_list_edit.py`.
- **Selects and multiselects populate asynchronously.** Industry and Bank
  render with just their placeholder and are filled from an API call a moment
  later, so read or set them via `wait_for_options` / `choose_bank`.
- **Clicking the sidebar can be blocked.** The add-escrow form and a dismissed
  confirmation dialog both leave a `cdk-overlay-backdrop` that swallows
  clicks; `goto_escrow_dashboard()` navigates by URL to sidestep it. A
  `.global-loading-overlay` spinner does the same thing transiently
  (`wait_for_loader`).

`EscrowData()` randomises escrow name, account number and IFSC per instance, so
every run creates a distinct record. The representative email is deliberately an
existing partner (`REPRESENTATIVE_EMAIL`) — the portal then auto-fills first/last
name, company and mobile as readonly fields, so tests must not type them.

To capture selectors for the parts still uncalibrated:

```bash
playwright codegen https://uat.sprintnxt.in/NexGenAdminFront/#/auth/login
playwright codegen https://excrow-uat.sprintnxt.in/EXcrowPartnerFrontV2/#/auth/login
```

## Validation behaviour the suite pins down

Verified against UAT, and asserted in the stage suites:

| Field | Bad input | What the portal does |
|---|---|---|
| IFSC Code | `12345`, `IDFB!!21234` | "Please enter a valid IFSC code (e.g., SBIN0001234)" |
| IFSC Code | lowercase, >11 chars | upper-cases it; truncates at 11 (both valid) |
| Account Number | `12ab!@` | "Must contain only digits" |
| Account Number | `12`, 30 digits | "Must be 9-18 digits" |
| Email | `not-an-email` | "Please enter a valid email", no lookup fired |
| Email | valid but unregistered | blocking "User not found!" alert, no auto-fill |
| Account Type | opened before a bank | "No data available" |
| Percentage charges | rate without min/max | `min` and `max` flagged invalid |
| Bank/TSP charges | expiry inside the first cycle | `maintenance_charges` group invalid |
| Bank/TSP charges | frequency changed after dates set | group invalid, dates unchanged on screen |
| Any incomplete stage | — | `Next` disabled, `title="Please complete all required fields"` |

Inline messages have **no error class of their own** — they are plain text
inside the field's `.form-group`, so read them with `BasePage.form_group_text`
rather than guessing at a selector.

Two gaps are covered by `xfail` tests rather than asserted as correct, so they
report the day they are fixed:

- **No file-type validation.** The modal advertises PDF/JPG/JPEG/PNG/DOC/DOCX,
  but a `.txt` saves without complaint from either the client or the server.
- **No file-size cap.** A 12MB upload saves. (Save Document *is* briefly
  disabled while a large file is read — enough to make a naive test flap, so
  the test waits for the button to settle before judging.)

The escrow name also stores `<script>`/SQL payloads verbatim, with no
client-side sanitising. That is not wrong by itself, so the security test
asserts what actually matters: the payload is never executed and raises no JS
error.

## Which tests leave records behind

The end-to-end paths in `test_create_escrow.py` each submit the wizard and stop
the moment the Escrow ID comes back, so every run of them adds a real record in
UAT. They are marked `create`:

```bash
pytest -m "admin and not create"   # 70 tests, leaves nothing behind
pytest -m "admin and create"       # 15 tests, each creates one escrow
```

The `create` set covers the happy path, dashboard persistence, every Account
Type alone and in combination (7 forms - the combination decides which pricing
sections render), and every pricing model x charge frequency (Fixed and
Percentage x Monthly / Quarterly / Annually).

Everything else stops short of submitting: the review tests that need the
confirmation dialog cancel out of it, and the Edit round-trip walks back to
Review without submitting.
