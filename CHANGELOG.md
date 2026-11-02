# Change Log

Every change to this suite is recorded here. **Newest entries go at the
bottom**, so the file reads top-to-bottom as the project's history.

Each entry states *what* changed, *why* (usually a live-DOM or live-API finding
that made the previous code wrong), and *how it was verified*. Findings that
turned out to be wrong are corrected in place with a later entry rather than
edited out - the wrong turn is part of the history.

Format:

```
## YYYY-MM-DD HH:MM - short title
**Changed** - files and what happened
**Why** - the finding that forced it
**Verified** - the command and its result
```

---

## 2026-08-12 - Baseline

Framework as inherited: Python + Playwright + pytest, page objects for admin and
partner portals, all selectors accessibility-first *guesses* marked
`TODO(locator)`. Never executed against the live portals.

Status at baseline: `pytest -m admin` = 1 failed, 6 errors. Login could not
complete.

---

## 2026-08-12 - Admin login made to work

**Changed**
- `conftest.py` - `browser_context_args` now grants `permissions=["geolocation"]`
  with Delhi coordinates and `locale="en-IN"`.
- `admin/pages/login_page.py` - calibrated to the real DOM
  (`formcontrolname="email"` / `"password"`, `button[type=submit]`); `enter_pin`
  became best-effort.

**Why**
- Both portals call `navigator.geolocation` on submit and hard-block with
  *"Please allow the Location access! and You May Not Connected To Internet"*.
  This looked exactly like a selector failure and was the real cause.
- The form uses `email`, not `username`.
- The superadmin account has **no PIN step** - it goes straight to
  `#/dashboard`, so waiting 30s for a PIN widget failed every run.

**Verified** - `pytest admin/tests/test_login.py --headed` → 1 passed.

---

## 2026-08-12 - Escrow module navigation

**Changed** - `admin/pages/escrow_nav_page.py`: label matching tolerates both
spellings via `e[sx]crow`; the "Switch Escrow" click became optional; waits on
controls instead of URLs; falls back to the sidebar "Excrow Dashboard" link.

**Why** - the portal spells the module **"Excrow"** (sic) in the UI and in route
names, so `^escrow` never matched. Hash-only route changes fire no `load`
event, so `wait_for_url` hung. Entering via the header mode-pill can land on an
empty shell route.

**Verified** - navigation reached `#/excrow/add-excrow` in the e2e run.

---

## 2026-08-12 - Shared helpers for the wizard's two recurring widgets

**Changed** - `core/base_page.py` gained `next_button`, `invalid_controls()`,
a `continue_next()` that waits for enablement and reports the still-invalid
controls, `pick_bs_date()`, `wait_for_loader()` and `form_group_text()`.
Removed `field_error` / `upload_error`, whose selectors guessed at error
classes that do not exist.

**Why**
- The wizard signals incompleteness only by keeping `Next` disabled
  (`title="Please complete all required fields"`) - it never shows a form-level
  error, so a failing test could not say which field was at fault.
- Dates are ngx-bootstrap `bsDatepicker`s: typing sets the text but leaves the
  control invalid, so selection must go through the calendar. Out-of-range days
  carry `.disabled` on the inner `<span>`, not the `<td>` - filtering on the
  `<td>` silently clicked nothing.
- Inline validation messages have no error class; they are plain text inside the
  field's `.form-group`.

---

## 2026-08-12 - Wizard page objects rewritten for the real 4-stage flow

**Changed** - `escrow_details_page.py`, `company_rep_page.py`,
`trustee_page.py`, `service_pricing_page.py`, `document_upload_page.py`,
`review_page.py`, `escrow_creation_flow.py` (stage-by-stage entry points),
plus stage fixtures in `admin/conftest.py`.

**Why** - the live wizard has **four** stages (Project Information → Service
Pricing → Documents → Review), not the ten the framework assumed, and stage 1
carries the escrow details, the representative and the trustee choice together.
Also found:
- `excrow_id` is generated server-side and rendered **readonly** on stage 1, so
  the Escrow ID is known before submit.
- Industry/Bank are plain `<select>`s populated by a later API call.
- Account Type is an `ng-multiselect-dropdown` whose options load **per bank**.
- Documents go through an "Add New Document" modal; saved documents render as
  `.card`s, not table rows.
- Submitting is two clicks: "Submit Application" then an "Are You Sure?" dialog
  that echoes the Escrow ID.

**Verified** - `pytest admin/tests/test_create_escrow.py --headed` → passed,
creating `EXC-0826-934758`.

---

## 2026-08-12 - Randomised, self-consistent test data

**Changed** - `admin/data/escrow_data.py`: escrow name, account number and IFSC
randomised **once at construction**; `account_types` became a list; added
pricing fields (`rate_type`, `percent`, `min_charge`, `max_charge`,
`charge_frequency`) and `document_dates`.

**Why** - `details` was a property that re-randomised on every read, so the name
a test asserted afterwards was never the name it typed. IFSC is generated
format-valid and bank-correct (`IDFB0` + 6 digits, 11 chars).

**Verified** - three consecutive runs produced `EXC-0826-934758`,
`EXC-0826-811550`, `EXC-0826-879909` with distinct names.

---

## 2026-08-12 - Positive/negative suites per stage

**Changed** - rewrote `test_escrow_details_validations.py` (removing its
`CALIBRATED = False` guard) and added `test_service_pricing.py`,
`test_document_upload.py`, `test_review_submit.py`; extended
`test_create_escrow.py`.

**Why** - validation wording was verified against UAT and is now asserted:
"Please enter a valid IFSC code (e.g., SBIN0001234)", "Must contain only
digits", "Must be 9-18 digits", "Please enter a valid email", "User not found!",
"No data available". IFSC auto-upper-cases and truncates at 11.

Two gaps are `xfail` rather than asserted as correct, so they report when fixed:
- **no file-type validation** - a `.txt` saves despite the modal advertising
  PDF/JPG/JPEG/PNG/DOC/DOCX;
- **no file-size cap** - a 12MB upload saves.

**Verified** - `pytest -m "admin and create"` → 15 passed;
`pytest -m "admin and not create"` → 66 passed, 2 xfailed.

---

## 2026-08-12 - Percentage pricing, frequencies, account-type combinations

**Changed** - `service_pricing_page.py` discovers its sections from the rendered
`<mode>Fixed` radios instead of a hard-coded list; added percentage support
(`percent` + `min` + `max`) and quarter-tile reads. `escrow_details_page.py`
takes multiple account types.

**Why** - **which pricing sections exist depends on the Account Types chosen**:
Payout brings IMPS/NEFT/RTGS, VID Collection adds "Collection via VID" and
"VID Creation" (Fixed-only, and its radio is *disabled*). Percentage swaps the
flat charges for a rate plus min/max bounds. Changing the charge frequency
**clears both dates**, so frequency must be set before them; Quarterly reveals
read-only Q1-Q4 tiles.

**Verified** - full pricing suite passed, including every rate type × frequency.

---

## 2026-08-12 - Partner portal login calibrated; two-step OTP found

**Changed** - `partner/pages/login_page.py` rewritten (`#email`, an unnamed
password input, "Login", a 4-box OTP challenge, "Verify & sign in");
`partner/conftest.py` skips with an actionable message when no OTP is supplied;
`config/settings.py` gained `partner_otp`; `partner/tests/test_login.py`
rewritten.

**Why** - partner login is **two-factor**: valid credentials return
`{"allowTwoStep": true, "message": "OTP send successfully."}` and the portal
shows *"Verify it's you"* against a masked address. The old
`assert_logged_in()` URL check could never pass.

**Verified** - `pytest -m partner` → 6 passed, 1 xfailed, 1 skipped.

---

## 2026-08-12 - Mailbox access

**Changed** - added `.auth/` and `shared/mail_credentials.json` to
`.gitignore`; mail-reading scripts kept in the scratchpad.

**Why** - every post-creation check needs the inbox. A Playwright session saved
to `.auth/gmail_state.json` after one assisted 2FA makes it readable on demand.

Traps worth remembering: Google's identifier field is `type=text` (not
`email`); the sign-in URL embeds `continue=https://mail.google.com/...` so a
substring check for the mailbox host gives a false positive; a `#search/` hash
is ignored by the loaded Gmail SPA (drive the search box instead); Gmail keeps
hidden `tr.zA` rows, so rows must be filtered with `:visible`; and all welcome
mails share a subject, so Gmail threads them - each message body must be read
separately.

---

## 2026-08-12 - New-representative onboarding path

**Changed** - `company_rep_page.py` gained `dismiss_user_not_found()` and
`enter_new_representative()`; `escrow_data.py` gained `NewRepresentative` and
`alias_email()`; `escrow_creation_flow.py` picks the path automatically; added
`scripts/onboard_new_partner.py`.

**Why** - **correcting an earlier wrong conclusion.** I had reported that a
new representative could not be onboarded through the wizard, based on a probe
against a fake domain. In fact the "User not found!" alert is *informational*:
dismiss it and the name/company/mobile fields stay **editable**, so the partner
is created as part of the escrow.

**Verified** - `EXC-0826-147683` created with a new address.

---

## 2026-08-12 - Representative identity must be fully unique

**Changed** - `NewRepresentative` now randomises company name, first/last name
**and** mobile number, not just the email.

**Why** - **correcting a second wrong conclusion.** I had blamed `+tag` address
normalisation for missing credentials. The real cause: the representative is
matched on its identifying fields, and **reusing the existing partner's mobile
(`9643063234`) was enough** to attach the escrow to that partner - so the portal
sent the existing-partner mail (link, no password) and greeted the *other*
company's name (`EXCROW432226`).

Evidence: 3 runs reusing the mobile (`+new2`, `+new3`, `+new4` - the last with a
unique company) produced no credentials; 2 runs with a fully distinct identity
(`+new21`, `+new22`) produced correct credential mails.

This is filed as **DEF-MAIL-01**: the wizard accepts the typed details and
Review displays them, yet the escrow is filed under a different company, with no
warning. A data-integrity fault, not just a mail fault.

**Verified** - `EXC-0826-838459` (`+new22`, QAQQOCS Ltd) produced a mail with
User ID `anand.kumar+new22@paysprint.in` and a temporary password.

---

## 2026-08-12 - New-partner flow verified end to end

**Verified** on `+new22` / `EXC-0826-838459`, with UAT's fixed OTP `1234`:

| Step | Result |
|---|---|
| Escrow created and listed | pass |
| Mail found by company name and Escrow ID | pass |
| Mail carries User ID, temporary password, Escrow ID, link | pass |
| Escrow ID in mail == portal | pass |
| Login with emailed credentials | pass (`allowTwoStep: true`) |
| OTP `1234` (auto-submits on the 4th digit) | pass |
| `#/auth/set-password` forced on first sign-in | pass |
| `changePassword` → "Password updated successfully" | pass |
| Re-login with the new password, `firstLogin: 0` | pass |
| Re-login reaches the dashboard | **FAIL** |

**New defect - DEF-PARTNER-04 (Critical).** After the first login, a correct
password returns 200 with `allowTwoStep: false`, `firstLogin: 0` and a valid
`authtoken`, and the UI **stays on `#/auth/login`** - no navigation, no modal,
no error, empty `localStorage`; `#/dashboard` bounces back. Reproduced on
`+new21` and `+new22`, observed for 35s. The front end appears to handle only
the two-step branch, so a direct-login response is never consumed. A partner can
onboard once and is then locked out.

Existing-representative escrows behave as specified: mail with the link and User
ID, **no** new password, previously set password still valid.

**Docs** - full write-up, test-case matrix and all defects in
`docs/post-creation-email-and-partner-login.md`.

---

## 2026-08-12 11:46 - This change log added

**Changed** - added `CHANGELOG.md` (this file) and a pointer to it from
`README.md`.

**Why** - requested: every change from here on is appended at the bottom of this
file so the history stays traceable.

---

## 2026-08-14 - Partner operations suite: payout, Virtual ID & wallet reconciliation

**Added** - `partner_ops/`, a new suite for everything a Partner does *after*
the escrow exists: beneficiaries, payouts, Virtual IDs, reports, service pricing
and end-to-end wallet reconciliation. 111 tests across 8 modules, plus
`docs/partner-operations-test-cases.md` (design, execution record, defect log)
and `partner_ops/README.md`. Registered in `pytest.ini` (`-m partner_ops`) and in
the report dashboard's module map.

Kept separate from `partner/` deliberately: that suite owns *getting in* (first
login, temporary password, forced change); this one owns *operating* once in, and
needs a different shape - a shared authenticated session, money-aware fencing and
a reconciliation engine.

**Every locator was calibrated against live UAT**, and one real payout and one
real Virtual ID were executed to measure the money movement rather than infer it.

### Two Critical defects

**DEF-OPS-01 - a partner is locked out after their first login.** This is the
root cause of DEF-PARTNER-04, and it is not about new partners at all. The
backend applies **trusted-location 2FA**; the Angular client implements only the
OTP branch. Proven by three consecutive `POST /auth/login` calls:

| Coordinates | `allowTwoStep` | Token issued |
|---|---|---|
| 28.6139/77.2090 (already used) | `false` | yes |
| 19.0760/72.8777 (new)          | `true`  | no  |
| 28.6139/77.2090 (already used) | `false` | yes |

On the trusted branch the server returns a **valid `authtoken`** which the UI
discards, leaving the user on `#/auth/login` with no error and empty storage.
Clearing cookies and storage does not help - the state is server-side. The suite
works around it by sending unused coordinates so the OTP branch is taken;
without that, no authenticated screen is reachable.

**DEF-OPS-02 - a failed payout keeps its charge.** A ₹10 IMPS payout reported to
the user as *"Your request has been failed."* still debited **₹14.75**
(₹12.50 + GST ₹2.25) with **no reversal entry**: wallet ₹180.15 → ₹165.40,
`TXN 1786693437161`. Historical `Failure` rows carry full charges too, so it is
systematic. The ₹10 attempt cost 147% of the transaction value.

### Ten further defects

DEF-OPS-03 charge never disclosed before authorisation · DEF-OPS-04 per-mode
amount limits enforced only *after* the OTP is spent · DEF-OPS-05 dashboard
balance stale until manually refreshed · DEF-OPS-06 maintenance charges debit the
wallet though the UI says escrow · DEF-OPS-07 "TSP" vs "Tcp" naming ·
**DEF-OPS-08** UI says failed, report says **Processed** for the same txn ·
DEF-OPS-09 inconsistent account-number masking · **DEF-OPS-10** Rows Per Page
"All" fails with `Unsupported operand types: string * int` and poisons the next
request · DEF-OPS-11 that raw PHP error is shown to the user · DEF-OPS-12
rejections return HTTP 201.

### What the ledger proved

The plumbing is sound where it was measurable: the running balance chains to the
paisa across 40 entries, GST is a consistent 18%, VID creation debited exactly
the configured price + GST (₹165.40 → ₹151.24), and the payout report agrees with
the wallet statement. **The wallet pays charges + GST only** - the transferred
amount leaves the escrow account, so the brief's `amount + charges` formula is
the *combined* cost, not the wallet leg.

**Why** - two corrections worth recording, both caught by checking before filing:

* The "Active" status filter looked broken (returned Inactive rows). It is not.
  Selecting Rows Per Page = "All" fails server-side and poisons the *following*
  request, so the filter silently returned stale rows - DEF-OPS-10. Numeric page
  sizes work: 500 → 31 rows, then Active → 6.
* The dashboard and statement disagreed on the balance (₹180.15 vs ₹165.40).
  That is a stale cached stat, not a ledger fault: the card's own refresh
  reconciles them. Filed as Low (DEF-OPS-05), not as a reconciliation break.

**Not done, and why** - stated rather than papered over: bulk payment and the
broad min/normal/max mode sweeps are **written but fenced** behind
`PARTNER_OPS_SPEND=1`, because DEF-OPS-02 makes every attempt an unrefundable
charge against a ₹151 wallet. VID payout structure and Pay Now (PARTS 17-20) have
**no reachable entry point** on any escrow this account can see - a test fails the
moment one appears, and no locators were invented for a screen nobody has seen.
Cross-tenant isolation needs a second provisioned partner.

---

## 2026-08-17 11:30 - Onboarding chain automated end to end; mailbox made a first-class dependency

**Changed**
* `core/mailbox.py` (new) - headless Gmail reader. Searches by a token unique to
  the run (Escrow ID or alias address), opens the newest match, and parses the
  onboarding mail into `OnboardingMail` (`user_id`, `temp_password`,
  `escrow_id`, `greeting`, `portal_link`). `has_credentials` is the property
  that separates a new-partner mail from an existing-partner one. Every read
  dumps the raw body to `reports/mail/<query>.txt`.
* `scripts/save_gmail_session.py` (new) - the one-time **headed** sign-in that
  writes `.auth/gmail_state.json`. The only step in the suite a human must sit
  through; everything after it is headless.
* `partner/pages/set_password_page.py` (new) - the forced first-login change
  screen (`#/auth/set-password`): the three boxes, the policy probe
  (`try_password`), `changePassword` response capture, and `open_direct()` for
  testing the route is guarded.
* `scripts/run_onboarding_e2e.py` (new) - performs the whole chain headless and
  scores it case by case against `docs/post-creation-email-and-partner-login.md`,
  writing `reports/onboarding_e2e.json`.
* `partner/tests/test_new_partner_onboarding.py` (new, 33 tests) - re-asserts
  the recorded run, plus the standalone parts: mail parsing, route guarding, and
  the set-password screen's own reachability.
* `partner_ops/pages/login_page.py` - added `visible_error()` /
  `message_visible()` over the full list of places this portal could show a
  failure, so DEF-PARTNER-02's "nothing at all" is an assertion rather than an
  impression.
* `pytest.ini` - `onboarding` marker.

**Why** - the temporary password exists **only** in the welcome email. Section 6
of the onboarding doc called the email leg "the missing third", and without it
every case from first login onward was NOT RUN. Reading the mailbox is therefore
not a convenience: it is the precondition for TC-N-05 … TC-N-10 existing at all.

The saved Gmail session from 11 Aug had **expired** - `mail.google.com` answered
with `accounts.google.com/v3/signin/accountchooser` ("Signed out"), and clicking
the stored account went straight to a password challenge. That is why the
re-auth script exists and why `GmailMailbox.open()` raises `MailboxUnavailable`
naming it, instead of timing out on a locator that will never appear.

Two deliberate design choices, both about not overstating what was seen:
* A stage that cannot run is recorded **BLOCKED**, not FAILED. With no temporary
  password there is nothing to sign in with, and calling that a failed login
  would misreport the product.
* The pytest suite re-asserts a recorded run rather than performing one per
  test. The chain creates a real UAT escrow and burns a real temporary password;
  making each `TC-N-*` independent would mean ~15 records per run for no extra
  coverage. With no run on disk the tests skip with the command that produces one.

**Verified**
* `pytest partner/tests/test_new_partner_onboarding.py -k "parser or guarded or
  set_password_screen"` - **7 passed** headless. `#/auth/set-password` is
  confirmed guarded: unauthenticated it redirects to `#/auth/login` and renders
  no password fields.
* `python -m core.mailbox --check` against the stale session reproduced the
  expiry, which is what prompted the re-auth script.

## 2026-08-17 12:05 - `--dry-run` for the onboarding runner; admin leg re-confirmed

**Changed** - `scripts/run_onboarding_e2e.py` grew `--dry-run`: fill wizard
stage 1, assert it, and stop before submitting. Adds TC-A-02-dry, TC-A-04
(representative fields stay editable after "User not found!") and TC-A-05
(stage 1 completes, `Next` enables). Everything downstream is recorded BLOCKED
with the reason "no escrow submitted, no mail sent" rather than silently absent.

**Why** - the headed Gmail sign-in timed out with nothing typed, so the mailbox
is still unreadable. A full run would have created a real UAT escrow and burned
a real temporary password, then BLOCKED at PART 2 with the credentials
unreadable and the record already spent. A dry run answers the useful question -
*is the admin half still working today?* - at no cost, so the eventual live run
is not the first time the wizard is exercised.

**Verified** - `python scripts/run_onboarding_e2e.py --dry-run`:
**8 passed, 0 failed, 11 blocked**. Admin signs in; the wizard reserved
`EXC-0826-287226`; the unregistered address `anand.kumar+new2849@paysprint.in`
raised "User not found!" and left Company (`QAOAWSR Ltd`) and the rest editable;
stage 1 validated with no invalid controls. All four partner routes - including
`#/auth/set-password` - redirect to login when signed out. Nothing was submitted,
so `EXC-0826-287226` was reserved but never created.

## 2026-08-17 12:40 - Escrow EXC-0826-962517 created; mailbox still signed out; a driver leak fixed

**Changed**
* `core/mailbox.py` - `open()` now unwinds its own partially-started Playwright
  driver on failure.
* `scripts/run_onboarding_e2e.py` - new `open_mailbox()` retries the mailbox for
  the same budget already allowed for the mail itself, so a sign-in finishing 40
  seconds late is a wait rather than a wasted escrow.

**Why** - the first live headed run created the escrow fine and then died in an
unrelated stage with `Playwright Sync API inside the asyncio loop`. Cause: a
`MailboxUnavailable` raised *inside* `GmailMailbox.open()` never runs `__exit__`
- the context manager was never entered - so the sync driver stayed running and
poisoned the next `sync_playwright()` in the process. The symptom appeared four
stages away from the cause: a signed-out mailbox crashed PART 7 (unauthenticated
route checks) instead of reporting itself. Worth recording because the class of
bug is general: any `__enter__` that acquires before it can fail must release.

**Verified**
* `python scripts/run_onboarding_e2e.py --headed --slowmo 150 --mail-wait 600`:
  PART 1 **passed** - Super Admin signed in, **`EXC-0826-962517`** created for
  the unregistered representative `anand.kumar+new4494@paysprint.in`
  (QaPqhk TestPQHK / QAOUTKS Ltd / 8588409427, all four identifiers fresh per
  DEF-MAIL-01), and the record was listed on the admin dashboard.
  PARTS 2-6 **BLOCKED**: `.auth/gmail_state.json` is still the 11 Aug file and
  Gmail answers "signed out", so no temporary password could be read.
* Leak fix proven directly: a failed `GmailMailbox().open()` followed by a fresh
  `sync_playwright()` now succeeds where it previously raised.

**Outstanding** - `EXC-0826-962517` is a real UAT record whose welcome mail is
sitting unread. Once the mailbox is readable the partner half can be driven
against it **without creating a second escrow**:

```
python scripts/run_onboarding_e2e.py --headed \
    --escrow-id EXC-0826-962517 \
    --email anand.kumar+new4494@paysprint.in \
    --company "QAOUTKS Ltd"
```

## 2026-08-17 13:05 - Second route to the mailbox: adopt Chrome's own Gmail session

**Changed** - `scripts/adopt_chrome_gmail_session.py` (new). Copies the three
session-bearing files out of the local Chrome profile (`Local State`,
`Default/Network/Cookies`, `Default/Preferences`) into a scratch user-data-dir,
drives that copy, and saves `.auth/gmail_state.json` from it. The live browser is
never touched. Cookie decryption works because the copy runs as the same Windows
user - the key is DPAPI-wrapped to that account.

**Why** - `save_gmail_session.py` needs a human at the keyboard, and two
consecutive 7- and 15-minute windows expired with nothing typed (the URL never
left Google's identifier page). A route that needs no typing was the obvious
next thing to try, since Chrome was running and presumably already signed in.

**Verified - and it does not work while Chrome is open.**
`Default/Network/Cookies` is held under an exclusive lock by the live browser:
`shutil.copy2` fails `WinError 32`, and a permissive buffered read still fails
`Errno 13`. Two corrections came out of that, both now in the script:

* `copy2` was replaced by a buffered read, which *does* defeat the lock on
  `Local State` and `Preferences` - so the fallback is worth keeping even though
  it is not sufficient on its own.
* The first version then reported **"Chrome's profile is not signed in to
  Gmail either"**, which was a **wrong diagnosis**: the browser landed on the
  sign-in page because no cookies had been copied, not because the profile was
  signed out. It now stops as soon as the cookie file is missing and names the
  lock. Recorded because the wrong message was the more damaging failure - it
  would have sent the next person to re-authenticate an account that was fine.

**Status of the onboarding cases: still NOT RUN.** TC-N-01 … TC-N-10, the
password-policy set and the DEF-PARTNER-01/02/04 re-checks have never executed,
because no temporary password has ever been read. `EXC-0826-962517` exists in
UAT with its welcome mail unread. Nothing in this suite records those cases as
passing, and nothing should until a run produces the evidence.

**Two ways to unblock, either one is enough**
1. Close Chrome completely, then `python scripts/adopt_chrome_gmail_session.py`
   - no typing at all.
2. `python scripts/save_gmail_session.py` and complete the sign-in in the window
   that opens.

## 2026-08-17 13:25 - Mailbox taken off the critical path; Escrow Management added as the flow's real end

**Changed** - `scripts/run_onboarding_e2e.py`
* `--temp-password` / `--user-id` / `--portal-link`: supply the credentials read
  from the welcome mail **by hand** and the run skips the mailbox entirely,
  executing every case from TC-N-05 onward. TC-N-01/TC-N-02 are then recorded
  `INFO - read from the mailbox by hand, not by this run`, never PASS: this run
  did not observe them, and saying otherwise would launder a manual step into an
  automated assertion.
* New **TC-E-08** at the end of PART 6: after the second sign-in, open Escrow
  Management and confirm the new Escrow ID is listed for that partner. Navigated
  by URL, not by clicking the sidebar - a leftover `cdk-overlay-backdrop`
  swallows sidebar clicks and would report "the escrow is missing" when the menu
  simply never opened.

**Why** - three attempts to automate Gmail have now failed (two interactive
windows expired untouched; the profile-adoption route is blocked by Chrome's
exclusive lock on its cookie database). The temporary password only exists in
the mailbox, but **it does not have to be this process that reads it** - a human
reading their own inbox unblocks TC-N-05 … TC-N-10 immediately. The escrow
`EXC-0826-962517` is already created and its mail already delivered, so the only
missing input is two strings.

TC-E-08 was added because reaching the dashboard is not the end of the flow. The
chain is only meaningful if the partner the wizard created can actually see the
escrow it was created for; a session that lands on a dashboard showing somebody
else's data, or nothing, would otherwise pass every existing case.

**Verified** - `--help` lists the new options; the module parses; no behaviour
change when the flags are absent (the mailbox path is unchanged).

**Still NOT RUN** - TC-N-05 … TC-N-10, the policy set and TC-E-08 await the two
strings from the mail for `EXC-0826-962517`.

## 2026-08-17 14:00 - Mailbox made permanent (persistent Chrome profile); operations leg added

**Changed**
* `core/mailbox.py` - `GmailMailbox` now prefers a **persistent Chrome profile**
  at `.auth/gmail-profile` (`launch_persistent_context`, `channel="chrome"`) and
  falls back to the `gmail_state.json` snapshot only if no profile exists.
* `scripts/save_gmail_session.py` - signs in *into that profile* rather than
  writing a throwaway snapshot, and keeps the profile on timeout so a re-run
  resumes wherever the sign-in got to.
* `scripts/run_onboarding_e2e.py` - **PART 8**: Escrow Management -> escrow
  detail -> activation check -> Add Beneficiary -> Verify Beneficiary
  (TC-OPS-01 … TC-OPS-06), running inside the PART 6 session.

**Why the profile, not the snapshot** - `storage_state` is a photograph of
cookies at one instant. That is precisely how mailbox access was lost: the
11 Aug snapshot expired and Gmail answered "signed out" for every run since. A
real Chrome profile renews its own tokens on each open, so the sign-in is meant
to be the last one rather than a fortnightly chore. `channel="chrome"` is part
of it - Google's password step rejects the bundled Chromium fingerprint far more
readily than real Chrome's. `.auth/` is already gitignored, so no profile is
committed.

**Why activation is reported and not performed** - the brief asks the *partner*
to activate the escrow, but the partner-side detail view is read-only: no
Next/Submit/Confirm chain exists, and activation is a Super Admin flow. This is
already established, not a new guess - `test_escrow_and_pricing.py` asserts the
read-only view, and PART 2 is recorded "N/A" in
`docs/partner-operations-test-cases.md`. TC-OPS-04 therefore *looks* for an
activation control and records what it finds: **absence is the documented
behaviour and is logged INFO; a partner-side Activate control appearing would be
a privilege finding and is logged FAIL.** Writing it the other way round would
have made a correct product look broken every run.

A consequence worth stating plainly: **an Inactive escrow exposes no Beneficiary
or Payout tab**, so if `EXC-0826-962517` is not activated by a Super Admin
first, TC-OPS-05/06 will report BLOCKED with the tab list as evidence rather
than inventing a route to a screen that is not there.

**Beneficiary payload** - display name and account number are randomised per
run, because the portal refuses a duplicate account: a fixed payload passes once
and then fails forever. IFSC/bank are a real pair (`UBIN0438583` / Union), since
the bank must be chosen from the type-ahead and an invented one cannot be.

**Verified** - module parses, `--help` lists every option, `partner_ops` page
objects import and their signatures match the calls. The live chain still awaits
the mailbox.

## 2026-08-17 14:35 - Escrow activation has no UI owner; two admin-side findings

**Investigated** (headless probes against UAT, `EXC-0826-962517`) - where a
Super Admin activates an escrow. Answer: **nowhere reachable.** Checked, in
order:

| Where | What is actually offered |
|---|---|
| Partner escrow detail | read-only view, no Next/Submit/Confirm (already asserted) |
| Admin Excrow Dashboard row | status `Inactive`; the **only** action is an `icon-edit` |
| Admin edit view | reopens the wizard at **Review**, buttons `Previous / Save Draft / Submit Application` - no activate/approve/status control (searched for all four words) |
| Admin **Bank Credentials** (`#/user-credentials`) | a search-only screen for partner credentials; no escrow list, no activation |

So the brief's "activate the escrow" step has **no owner in either portal**.
The status is set elsewhere - most likely when the bank actually provisions the
account, which in UAT does not appear to happen for a freshly created escrow.

**DEF-ADMIN-01 - a submitted escrow reopens at Review with Submit Application
live again.** Severity Medium. Clicking the dashboard's edit icon on the
already-created `EXC-0826-962517` lands on
`#/excrow/add-excrow/EXC-0826-962517` showing the completed Review with
**Submit Application** enabled. Nothing marks the record as already submitted.
Whether that re-submits, duplicates or silently no-ops is untested here - it was
**not clicked**, because doing so on a live record could create a duplicate.
Worth a product answer before it is exercised.

**DEF-ADMIN-02 - document dates render as "Invalid Date" on Review.** Severity
Medium. All three uploaded documents show `SIGNING DATE: Invalid Date` and
`EFFECTIVE DATE: Invalid Date`, while `EXPIRY DATE: 11/5/2026` on the same
document renders correctly. The wizard accepted all three dates through the
datepicker, so this is a display/parse fault on the review of a stored record,
not bad input. Visible to the operator reviewing what they just created.

**Consequence for the run** - `EXC-0826-962517` is `Inactive`, and an Inactive
escrow exposes no Beneficiary or Payout tab. TC-OPS-05/06 (Add / Verify
Beneficiary) therefore cannot execute against it, and the runner will record
them BLOCKED with the actual tab list as evidence rather than inventing a route.

**Not done, and why** - the investigation stopped at Bank Credentials. Going
further (searching partner credentials, Manage Bank Service) means changing
shared UAT configuration to force a status the product does not expose, which is
a bigger decision than a test run should take on its own.

## 2026-08-18 - `scripts/run_chain.py`: the whole chain, in two separable phases

**Asked for** - create an Escrow ID as Super Admin for every test case; then for
each successful creation read the welcome mail in Gmail, change the password,
sign the partner in through the emailed link, and run the module tests - with
the admin and partner halves runnable **separately**.

`scripts/run_onboarding_e2e.py` already drove that chain, but for exactly one
escrow and as one unsplittable run. The new `scripts/run_chain.py` adds the two
missing pieces and reuses that script's helpers (`Results`, `sign_in`,
`open_mailbox`, `partner_context`, `strong_password`) rather than copying them.

**The create matrix** - 14 variants, mirroring `admin/tests/test_create_escrow.py`:
the default payload, all 7 Account Type combinations, and Fixed x Percentage
across Monthly / Quarterly / Annually. `--list-variants` prints them.

**Only one escrow gets a brand-new representative** (`--chain-variant`, default
`happy`). All 14 are created, but only that one is filed under a fresh
email/company/name/mobile, so only it produces a welcome mail carrying a User ID
and temporary password; the other 13 use the existing `REPRESENTATIVE_EMAIL`.
A fresh representative per variant would leave 14 new partner accounts in shared
UAT on every run, and one proves the mail -> password -> login chain just as well.

**The two phases hand over through `shared/chain_state.json`**, so
`--phase partner` can run minutes or days after `--phase admin`, repeatedly, or
entirely standalone via `--escrow-id` / `--email`.

Three defects in the runner itself, each found by running it against UAT:

**Navigating between variants by URL does not work.** The add-escrow and
dashboard routes differ only in the hash, so `EscrowNavPage.goto_escrow_dashboard`
performs a *same-document* navigation that Angular is free to refuse - and does,
when the wizard holds unsaved changes. Observed directly: variant 2 timed out
waiting for a blank `excrow_id` field while the browser sat on variant 1's fully
populated Review (screenshot `reports/screenshots/chain_create_acct-payout.png`).
`reset_to_escrow_dashboard()` bounces through `about:blank` first, forcing a real
cross-document load. Storage - and with it the admin session - survives that.

**DEF-ADMIN-03 - an unsubmitted wizard is persisted as a draft, and "Add New
Escrow" resumes it instead of opening a blank form.** Severity Low (it is
arguably a feature), but it has a hard consequence here: the resumed Review
survives a full page reload, so it is the server's copy and not stale client
state, and there is no discard control on the dashboard (the only row action is
an edit icon). A looping `--dry-run` would therefore re-test variant 1's payload
for every variant. `--dry-run` is now restricted to a single variant, with an
error that says why. **The real path is unaffected** - a submitted escrow leaves
no draft - which is what made the loop work once `--dry-run` was dropped.

**A resumed partner phase crashed on the dead-temp-password check.** With no
temporary password in the state file, `sign_in` submits a blank password, the
login button stays disabled and `submit_credentials` raises - taking the whole
phase down over a case that simply cannot be judged. TC-PWD-03 is now BLOCKED
with that reason instead.

Also added: `EscrowCreationFlow.open_new_escrow_form(from_dashboard=...)` and
`create(..., from_dashboard=...)`, so one session can create several escrows in
a row without the sidebar click being swallowed by a leftover overlay backdrop.
Both default to the previous behaviour.

**Nothing that spends the wallet runs by default** - `--spend` lifts both
`PARTNER_OPS_SPEND` and `PARTNER_OPS_CREATE_DATA`. State is written after every
single create, so a crash on variant 9 cannot lose the eight real records
already made, and a failed variant is recorded FAIL and stepped over rather than
ending the phase.

**Verified against live UAT:**

| What | Result |
|---|---|
| `--list-variants`, unknown-variant and chain-variant-not-selected guards | all correct |
| Full suite still collects after the page-object change | 239 tests |
| `--phase admin --variants happy,acct-payout` (real creates) | 5 passed, 0 failed - `EXC-0826-456206`, `EXC-0826-160897`, both listed |
| `--phase partner` resumable branch + module handoff | 4 passed, 1 blocked, 4 info; injected credentials reached pytest, `partner/tests -m smoke` 6 passed, exit 0 |
| `--phase partner` mail leg | **BLOCKED - the saved Gmail session has expired** |

**Not verified, and why** - the Gmail leg. `.auth/gmail-profile` is signed out,
so `--phase partner` correctly reported TC-MAIL-01..04 as BLOCKED with the
re-auth pointer rather than as failures. Restoring it needs an interactive
headed sign-in (`python scripts/save_gmail_session.py`), which a non-interactive
session cannot perform. Everything downstream of the mail was exercised by
injecting known-good credentials into the state file, which were removed
afterwards; the parse itself is covered by
`partner/tests/test_new_partner_onboarding.py` against a saved body.


---

## 2026-09-22 - Super Admin login now has a mandatory OTP step; the console was redesigned

**Finding.** `python scripts/run_chain.py --phase admin --headed` reported all
14 creates FAILED. The first one timed out after 30s waiting for the `Excrow`
sidebar link; the other 13 failed instantly with
`TargetClosedError('Page.goto: Target page, context or browser has been closed')`.
Only the first is real - the rest are a cascade.

**Root cause.** The Nex-Gen Admin Console has been rebuilt and `POST /auth/login`
no longer lands on `#/dashboard`. It routes to a new `#/auth/otp-verify` screen -
*"Let's verify it's you - We've sent a 4-digit code to your registered
email/phone. It'll auto-verify once entered."* - and only a successful
`POST /auth/verifyOtp` reaches the dashboard. Confirmed by probing live UAT:
`POST /auth/login` 200 -> `POST /auth/verifyOtp` 200 -> `#/dashboard`.

Two separate defects in this suite turned that into the observed failure:

**1. The OTP boxes must be typed into, not filled.** They render as
`input[type=text][inputmode=numeric][autocomplete=one-time-code][maxlength=1].digit`
and carry Angular directives `appotpmask` and `appfocustonextinput`, which
advance the focus box to box and fire the auto-verify off **real key events**.
`AdminLoginPage.enter_pin` used `locator.fill()` per box: all four digits
appeared on screen - the failure screenshot shows `1 2 3 4` sitting there - and
nothing was ever submitted. `keyboard.type()` into the first box works.
There is no Verify button on this build; the fourth digit submits. The button
lookup is kept only as a fallback for other builds.

**2. `assert_dashboard_loaded` asserted the wrong thing.** It checked only that
the URL was no longer `#/auth/login`. `#/auth/otp-verify` satisfies that, so a
login still parked on the OTP screen was recorded as TC-A-01 PASS, and the
failure surfaced 30 seconds later as a missing sidebar - naming a locator
instead of the login. It now asserts the URL has left `#/auth/` entirely, and
`enter_pin` waits for the challenge to clear and fails with what actually
happened ("the OTP challenge was not accepted - still on <url>") rather than
walking past it. An empty `ADMIN_PIN` against a present challenge is now a
named error instead of four blank boxes.

**3. A dead browser was reported as 14 product failures.** `phase_admin` caught
every per-variant exception, recorded FAIL and carried on, so once the context
was closed the remaining 13 variants each recorded an identical
`TargetClosedError` as though their payloads had been rejected. New
`browser_is_gone(page, exc)` tells "this payload failed" apart from "the browser
died"; the latter now BLOCKS the remaining variants with
`"browser or context was closed"` and stops the loop. The failing variant's own
record is still persisted by the existing `finally`.

**Verified against live UAT:**

| What | Result |
|---|---|
| Probe of the live login | `#/auth/login` -> `#/auth/otp-verify` -> `#/dashboard`; 4 boxes, no Verify button |
| `.fill()` per box | digits on screen, page stays on `#/auth/otp-verify` - reproduced the failure |
| `keyboard.type()` into the first box | `POST /auth/verifyOtp` 200, lands on `#/dashboard` |
| Patched `AdminLoginPage` + `assert_dashboard_loaded` | passes; `#/dashboard` |
| The exact locator that timed out for all 14 variants | `Excrow` nav visible |
| `pytest admin/tests/test_login.py` | 1 passed in 17.92s |
| Full suite still collects | 239 tests, 0 errors |

**Not verified, and why** - no escrow was created. Confirming the 14 creates end
to end means leaving 14 real records in shared UAT, which was not asked for. The
failure was entirely upstream of the wizard (no session, so no sidebar), and the
login that blocked it is now proven to reach the dashboard and the `Excrow` nav
the creates start from. `--phase admin` is the command that closes this out.


---

## 2026-09-22 - The mailbox is read once per partner; the login OTP can come from mail

**Asked for.** Every new escrow means reading the welcome mail for the
representative's temporary password and forcing a change. After that change,
signing that partner in again must **not** need Gmail - the new password should
be saved. A login OTP, however, is mailed per attempt, so that one does need the
mailbox.

**What was already true.** The welcome-mail read and the forced change already
worked. `shared/chain_state.json` did remember a `new_password` - but only one.
It holds a single `chain` slot that the next `--phase admin` run overwrites, so
every new escrow lost the previous partner's credentials, and there was no way
back into that account: the temporary password is single-use and the mail
carrying it is stale. The login OTP was not read from mail at all - it was the
fixed `PARTNER_LOGIN_OTP` (`1234`).

### `core/credentials.py` - a per-representative credential store

`shared/partner_credentials.json`, keyed by the address the escrow was filed
under, accumulating across runs. The flow now branches on one question:

* no record, or no `new_password` -> the mailbox is required;
* a record with a `new_password` -> sign straight in, no Gmail.

`record_onboarding()` **merges** rather than replaces, so a second escrow for the
same representative appends its Escrow ID without wiping the working password -
the exact failure the store exists to prevent. `record_password_change()`
refuses an empty password, because recording `""` would mark a partner onboarded
with nothing to sign in with and skip the mailbox forever. A corrupt file
degrades to "needs the mailbox" rather than raising: the worst case must be one
extra mail read, not a dead run. `summary()` reports *whether* a password exists,
never its value.

`phase_partner` now consults the store before deciding the partner needs
onboarding, and writes to it at both points - what the mail carried, and the
change itself. The dead temporary password is kept so TC-PWD-03 ("the old one no
longer works") still has something to try.

### Reading the login OTP from Gmail

`parse_login_otp(body, length=...)` and `GmailMailbox.find_login_otp(...)`, off
by default behind `PARTNER_OTP_FROM_MAIL`. Three things shaped it:

**A second Playwright driver cannot run inside the first.** The code has to be
read *while* a partner browser sits on the challenge, and
`sync_playwright().start()` inside an open `sync_playwright()` raises *"It looks
like you are using Playwright Sync API inside the asyncio loop"* - measured, not
assumed. So the mailbox is driven as a **separate process** via
`python -m core.mailbox --json`. The welcome mail has no such problem: it is read
before the partner browser opens.

**A code from a previous attempt is indistinguishable from a fresh one by text.**
Gmail renders "12:04 PM" for everything that arrived today, and a spent code
fails the login looking exactly like a wrong password. `top_row_id()` snapshots
the newest OTP-mail row id *before* the login is submitted; `find_login_otp`
waits until the newest row differs from it.

**`\d{4}` matches far too much** - a year, an amount, half a phone number, an
Escrow ID. Labelled codes are matched first; a bare run is only accepted in a
mail whose wording already marks it an OTP mail, and `length` (4 for login, 6 for
payout) filters the result, because filling the wrong number of boxes fails for
no visible reason.

**A parser bug the new tests caught before it shipped.** The bare-number
boundaries were symmetric - `(?<![\d.,-])(\d{4,6})(?![\d.,-])` - which meant a
code ending a sentence (`...is 4821.`) was rejected for the trailing full stop,
and so was every real mail. The boundaries are now asymmetric: any of `- . ,`
*before* the run disqualifies it (`EXC-0826-456206`), but after it only a
separator that is itself followed by a digit does (`1234.56` is an amount,
`4821.` is a code).

A mailbox failure falls back to the fixed code and says so, rather than taking
the login down.

**Verified:**

| What | Result |
|---|---|
| `partner/tests/test_credential_store.py` (new) | 21 passed |
| Store: welcome mail alone does **not** end the mailbox requirement | asserted |
| Store: a second escrow does not wipe the working password | asserted |
| Store: empty password refused; corrupt file degrades to "needs mailbox" | asserted |
| Store: `summary()` carries no password | asserted |
| OTP parse: labelled, bare-with-context, 4-vs-6 length, Escrow ID not mistaken | asserted |
| Nested `sync_playwright()` | fails as described - drove the subprocess design |
| `PARTNER_OTP_FROM_MAIL=false` | returns `1234` in 0.00s, opens no browser |
| `PARTNER_OTP_FROM_MAIL=true`, Gmail signed out | falls back to `1234`, no crash |
| `python -m core.mailbox --json --otp-snapshot` | `{"error": "Gmail is signed out..."}`, exit 1 |
| Full suite collects | 260 tests (was 239) |

**Not verified, and why** - anything that needs a live inbox. `.auth/gmail-profile`
is signed out (`python -m core.mailbox --check` fails), so the OTP **search query
and mail format are uncalibrated**: `find_login_otp` has never seen a real
SprintNXT OTP mail, and `OTP_QUERY` is a reasonable guess, not a measured one.
Restoring the session needs an interactive headed Google sign-in
(`python scripts/save_gmail_session.py`) which a non-interactive session cannot
perform. The parser is tested against hand-written bodies covering the formats
seen in the wild, and every mail body is dumped to `reports/mail/` on read, so
calibrating it is a one-look fix once the mailbox is back. The default path does
not depend on any of it.

`shared/partner_credentials.json` and `shared/chain_state.json` are now
git-ignored - they hold live passwords for real UAT accounts.


---

## 2026-09-22 - Validate before creating; edit a submitted escrow from its row; reuse an onboarded partner

**Asked for.** Run the whole positive/negative set when an escrow is created,
including going back to edit in the wizard *and* the edit process in the list
after final submit. Then: a brand-new representative email means Gmail ->
temporary password -> change -> save, but a previously-used address means no
Gmail at all, just a partner login.

**Already covered, and left alone.** The wizard back/edit round-trip is
`test_review_submit.py`: `test_edit_a_reviewed_value_and_carry_it_forward`
(edit from Review, change a value, walk forward, assert the summary shows the
edit), `test_previous_returns_to_documents_with_them_intact` and
`test_every_review_section_offers_edit`. The stage +/- suites are the four
`test_<stage>.py` files. Saving the password after the change landed in the
previous entry (`core/credentials.py`).

### Editing a **submitted** escrow from the dashboard list - new coverage

The gap. `EscrowNavPage` could tell whether a record was listed but had no way
back into one, so nothing exercised the post-submit edit path at all.

Calibrated against the live list rather than guessed:

* The row's only action is `<i class="icon-edit" tabindex="0">` in an `Action`
  column - **not** an anchor and not a button, so it carries no `link`/`button`
  role. `get_by_role("button", name="Edit")` finds nothing, which means a test
  written that way passes for the wrong reason. Same trap as the Review Edit
  links. `test_the_row_action_carries_no_aria_role` pins it, and says to delete
  itself rather than be adjusted if the markup ever improves.
* There is **no separate edit screen**. The icon routes to
  `#/excrow/add-excrow/<ESCROW-ID>` - the same four-stage wizard reopened on
  **Review**, with all three sections (Project Information, Documents, Service &
  Maintenance Charges) and their own Edit links.
* Columns are `Created | Excrow Name | Excrow Id | Industry | Bank Name |
  Bank Acc Number | Trustee | Balance | Status | Action`, so cells are read by
  heading rather than by index.

**A race the first run caught.** `a:has(.icon-edit)` returned 0 immediately
after the navigation and 3 a moment later, and `sections()` returned `[]` - the
summary is filled from an API call *after* both the route and the
`.global-loading-overlay` have settled. Measured across runs: present at t+0.1s
on one, still absent when the assertion ran on another. That is a flapping test,
not a failing one, so the wait went into `open_escrow_for_edit()` rather than
into each test.

New `admin/tests/test_dashboard_list_edit.py` (8 tests) covers the Action
column, one edit affordance per row, the no-role trap, that the icon reopens
*that* escrow, that the reopened wizard shows the row's own name/bank/account,
that all three sections are still editable after submission, and that a
hand-edited Escrow ID in the URL does not surface another partner's record.

### Validate the wizard *before* filing records against it

`--with-admin-suite` ran **after** `phase_admin`. Checking whether stage 1
accepts a valid IFSC after fourteen real escrows have already been created
against it is the wrong way round. It now runs first, as PHASE 1a, and
`-m "admin and not create"` already means the whole set bar the submitting ones
- stage positives and negatives, the Review back/edit round-trip, and now the
dashboard-list edit. New `--suite-gates-creates` skips the creates entirely when
that set fails, so nothing is filed on top of a known-broken wizard; without it
the ordering changes but the creates still run.

### `--representative EMAIL` - reuse an onboarded partner, skip Gmail

The chain variant always minted a fresh identity, so there was no way to say
"use the partner I already onboarded". With the flag the escrow is filed under
the existing record - the portal auto-fills first/last name, company and mobile
readonly and issues **no** credentials - and the chain state is seeded from
`shared/partner_credentials.json`, so `phase_partner` sees an onboarded partner
and goes straight to the login. An address with no saved password yet prints a
note up front saying the mailbox will still be needed, rather than failing later
looking for a mail that carries no credentials.

**Verified against live UAT:**

| What | Result |
|---|---|
| `admin/tests/test_dashboard_list_edit.py` | **8 passed** (2:39) |
| Row action is an `<i>`, no button/link role | asserted against the live DOM |
| Edit opens `#/excrow/add-excrow/<id>` on Review, 3 sections | asserted |
| The async-summary race | reproduced, then fixed in `open_escrow_for_edit()` |
| Bogus Escrow ID in the URL does not surface a real record | asserted |
| `EscrowData(representative_email=...)` -> existing-partner path | `new_representative is None`, `rep_email` correct |
| Credential routing: unknown -> mailbox; after change -> no mailbox | asserted |
| A second escrow for the same partner does not send it back to Gmail | asserted |
| `--help` shows the new flags; script parses | clean |
| Full suite collects | 268 tests (was 260) |

**Not verified, and why** - the `--representative` flag end to end. Proving it
means creating a real escrow in shared UAT, which was not asked for. Its two
halves are verified separately: the payload branch selects the existing-partner
path, and the credential routing reports "no mailbox needed" for an onboarded
address. `--suite-gates-creates` is likewise only exercised on its pass branch -
forcing the fail branch would mean breaking the admin suite on purpose.

**Noticed, not chased:** the reopened wizard renders a saved document's signing
date as **"Invalid Date"**. Visible in the Review summary of `EXC-0926-359072`.
Not in scope here and not asserted either way, but it looks like a real defect
in how a stored date is read back.


---

## 2026-09-23 - The mailbox was never signed out; `div[gh='cm']` had stopped existing

**Finding.** `--phase partner` reported all ten cases BLOCKED, and
`python -m core.mailbox --check` said *"Gmail is signed out"*. It was not.
Opening the automation profile headless and reading the page gave:

    title: Inbox (121) - anand.kumar@paysprint.in - PAYSPRINT PVT LTD Mail
    tr.zA rows: 50
    div[gh='cm'] count: 0

The session was live the whole time. **Gmail dropped the `gh="cm"` attribute
from the Compose button**, which was the single marker every readiness check
waited for. The `gh` values it renders now are `sb, sda, mll, cl, chat, tm,
mtb, tl` - no `cm`. Compose is `<div class="T-I T-I-KE L3" role="button"
jscontroller="eIu7Db">Compose</div>`.

The same stale selector was in three places - `core.mailbox._wait_for_inbox`,
`save_gmail_session.inbox_reached`, `adopt_chrome_gmail_session.signed_in` - so
the suite, the sign-in script and the adopt script all failed the same way and
corroborated each other's wrong answer. New shared `INBOX_READY` matches any of
`form[role='search']`, `div[gh='mtb']`, `div[gh='tl']`, `input[name='q']` and
the old `div[gh='cm']`; all five were counted against the live inbox. None
depends on interface language - a text match on "Compose" would.

**The error message was the expensive part.** A timed-out readiness check
reported REAUTH_HINT - "Gmail is signed out, sign in again" - which is a
diagnosis, and it was wrong. It sent three sign-in attempts at a mailbox that
was already signed in. `_wait_for_inbox` now reports the last URL and the page
title, and says outright that a title naming a mailbox means the session is
fine and `INBOX_READY` needs updating.

### Three more bugs, each caught by the next step working

**`save_gmail_session.py` could not fill the sign-in form.** Google's address
box is `<input type="text" name="identifier" id="identifierId">`, not
`type="email"`, so the pre-fill matched nothing and timed out. Worse, the page
carries a decoy `<input type="password" name="hiddenPassword"
aria-hidden="true" tabindex="-1">` for password managers, present on the
*address* step - so `input[type=password]` first-match would have filled the
decoy and left the real field empty, failing exactly like a wrong password.
Verified: `EMAIL_BOX` matches 1 and fills; `PASSWORD_BOX` matches **0** on the
address step. The script also stopped emitting a twenty-line Playwright
traceback when the window is closed.

**`open_latest` clicked a hidden row.** Gmail keeps off-screen rows in the DOM,
so `tr.zA` first-match resolved to `<tr class="zA yO s00Hgd">` and the click
timed out against a list plainly on screen. All result lookups now go through
`tr.zA:visible`, wait for visibility, scroll into view, and click the subject
cell rather than the row (which has dead zones - checkbox, star, hover
actions).

**The security footer parsed as a temporary password.** Every SprintNXT mail
ends *"will never ask for your password, OTP or reset link over phone or
email"*, and the `your\s*password` label matched it, yielding a temp_password
of `", OTP or reset link over phone or email. (c) 2026 PaySprint."`. A label
now requires a separator and the value must look like a credential - one token,
under 64 chars - rather than a sentence.

### The OTP parser, calibrated against a real mail at last

The previous entry shipped `parse_login_otp` untested against anything real,
because the mailbox was unreadable. The live mail:

    Use the following One-Time Password to finish signing in to your
    SprintExcrow account:

    686671

Two things it taught. The code is **six digits**, and it sits **alone on its
own line with no adjacent label**, so every labelled pattern misses it - new
`OTP_STANDALONE_RE` handles that shape. And asked for *four* digits, the bare
fallback returned `2026`, out of `(c) 2026 PaySprint` in the footer: a
plausible-looking OTP that is a copyright year. A mail that presents a code on
its own line has shown its hand, so if none of those is the length asked for
the answer is now "" rather than a number found elsewhere.

**Note for later:** the live login code is **six digits**, but
`PARTNER_LOGIN_OTP` defaults to `1234` and `PartnerLoginPage.otp_boxes`
expects four `input[maxlength='1']`. Either UAT's fixed code no longer applies
to this account or the challenge has widened to six boxes. Not changed here -
it needs a partner login to observe, which burns the temporary password.

**Verified against the live mailbox:**

| What | Result |
|---|---|
| `python -m core.mailbox --check` | **"Gmail session is valid - inbox opened headless"** |
| `--find EXC-0926-328488` | parses `user_id`, `temp_password`, `escrow_id` (matches the portal) and `portal_link`; `has_credentials: true` |
| Readiness markers counted on the live inbox | 5 of 5 match; `div[gh='cm']` alone is 0 |
| Sign-in selectors | address fills; `PASSWORD_BOX` correctly 0 on the address step |
| `partner/tests/test_mail_parsing_live_bodies.py` (new, 5 tests) | 26 passed with the existing parser suite |
| Full suite collects | 273 tests (was 268) |

New `test_mail_parsing_live_bodies.py` pins the real captured bodies rather than
invented ones - both parser bugs above survived precisely because the fixtures
had been written to match the parser.

**Not verified, and why** - the partner phase end to end. The welcome mail for
`EXC-0926-328488` is now readable and carries a temporary password, but signing
in with it consumes it: the change is one-way and the mail is never re-sent.
That is the user's call to make, not a verification step to take unasked.
