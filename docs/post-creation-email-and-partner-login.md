# Post-Escrow-Creation: Email Confirmation & Partner Login

Validation of everything that happens *after* Super Admin submits an Escrow:
the confirmation email, the Partner invitation link, first-time login and
password change, and existing-Partner login.

---

## 0. Status

The new-partner workflow has now been executed end to end. Summary:

* **Mailbox access: solved.** A Playwright session is stored in
  `.auth/gmail_state.json` after a one-time assisted 2FA, so the inbox is
  readable on demand. Mail is searchable by company name or Escrow ID.
* **Partner OTP: not a blocker.** UAT accepts the fixed code `1234`
  (`PARTNER_OTP=1234`). Entering the 4th digit auto-submits.
* **New-partner onboarding: works**, provided the representative's identity is
  *entirely* new - see DEF-MAIL-01 for the trap.
* **Two open defects** block normal use: DEF-PARTNER-04 (cannot reach the
  dashboard after the first login) and DEF-PARTNER-01/02 (wrong password
  returns 500 with no user feedback).

### How partner login actually works

`POST /auth/login` with valid credentials returns either

* `allowTwoStep: true` → the portal shows *"Verify it's you - Enter the 4-digit
  code we just sent you"* against a masked address, or
* `allowTwoStep: false` + `firstLogin` + `authtoken` → no OTP round (this is the
  branch the UI fails to handle, DEF-PARTNER-04).

After the OTP, a first-time user is routed to `#/auth/set-password`
("This is your first sign-in, so the password we emailed you has to be
replaced before you can continue") with **Password From Email / New Password /
Confirm New Password** and **Set Password & Continue**.

---

## 2. Onboarding a new representative (corrected)

An earlier version of this document claimed a brand-new representative could not
be onboarded through the Escrow wizard. **That was wrong.** The correct
behaviour:

* Entering an unregistered address raises an *informational* **"User not
  found!"** alert. Dismiss it and the First/Last name, Company and Mobile
  fields stay **editable** - type them in, and the wizard creates the partner
  as part of the escrow.
* An existing partner's address instead auto-fills those four fields
  **readonly**, and no new credentials are issued.

Both paths are implemented in `CompanyRepPage`
(`enter_new_representative` / `enter_email_and_wait_autofill`) and selected
automatically by `EscrowCreationFlow` based on `EscrowData.new_representative`.

`scripts/onboard_new_partner.py --tag new23` runs the new-partner path with a
fully randomised identity.

---

## 3. What IS automated and passing today

No mailbox needed; these run in CI now.

| Test | Asserts |
|---|---|
| `test_login_page_renders` | email, password, Login, Forgot password present |
| `test_valid_credentials_reach_the_otp_challenge` | credentials accepted → 4-box OTP challenge + Verify button |
| `test_otp_target_address_is_masked` | challenge masks the address; full local part not exposed |
| `test_otp_resend_is_rate_limited` | resend sits behind a countdown |
| `test_login_disabled_until_the_form_is_valid` | empty/malformed input keeps Login disabled, `ng-invalid` set |
| `test_guarded_routes_redirect_to_login` | `#/dashboard`, `#/excrow/dashboard`, `#/escrow-management`, `#/profile` all redirect when unauthenticated |
| `test_wrong_password_is_rejected_with_a_message` | **xfail** - documents DEF-PARTNER-01/02 |

Run: `pytest -m partner` (add `PARTNER_OTP=…` to reach past the challenge).

---

## 4. Test cases - Scenario 1: new Company Representative

Precondition per section 2: the partner is registered first.
Status: **NOT RUN** (blocked on mailbox).

| ID | Title | Steps | Expected |
|---|---|---|---|
| TC-N-01 | Confirmation email received | Register/create with a new address → open mailbox | Exactly one email, < 5 min, correct recipient |
| TC-N-02 | Email contains required fields | Open email | Escrow ID, Partner/User ID, login email, temporary password, partner link - all present and non-placeholder |
| TC-N-03 | Escrow ID matches portal | Compare email ID vs the ID on Review/confirmation | Identical; no stale/previous ID |
| TC-N-04 | Partner link target | Click link | Opens `excrow-uat.sprintnxt.in/EXcrowPartnerFrontV2` login; correct environment, HTTPS, no mixed content |
| TC-N-05 | First-time login | Log in with emailed email + temporary password | Authenticates (expect an OTP step too - see DEF/Q-01) |
| TC-N-06 | Change Password prompt | After first login | Change Password popup/page is forced, and cannot be dismissed to reach the dashboard |
| TC-N-07 | Password policy | Try each violation | Rejected with a specific message: min/max length, upper, lower, number, special, confirm mismatch, reuse of temporary password |
| TC-N-08 | Password change succeeds | Submit a valid new password | Success message; change persisted |
| TC-N-09 | Login with new password | Re-login | Succeeds; **no** Change Password prompt second time |
| TC-N-10 | Temporary password invalidated | Re-login with the old temporary password | Rejected |

### Scenario 1 negatives (NOT RUN)

| ID | Title | Expected |
|---|---|---|
| TC-N-N01 | Wrong temporary password | Rejected with a clear message |
| TC-N-N02 | Wrong email | Rejected; no account enumeration ("user not found" vs "wrong password" must not differ) |
| TC-N-N03 | Expired temporary password | Rejected with an expiry-specific message |
| TC-N-N04 | Mismatched confirm password | Blocked at the form |
| TC-N-N05 | Empty / whitespace password | Blocked at the form |
| TC-N-N06 | Refresh mid password-change | Prompt still enforced on return; no half-changed state |
| TC-N-N07 | Close browser mid password-change | Old password still valid; prompt reappears |
| TC-N-N08 | Partner link reused after password change | Does not re-issue credentials or grant access |
| TC-N-N09 | Expired partner link | Rejected, with a route to request a new one |
| TC-N-N10 | Duplicate emails | Only one credential email per event |

---

## 5. Test cases - Scenario 2: existing Company Representative

Status: **NOT RUN** (blocked on mailbox) except where noted.

| ID | Title | Expected |
|---|---|---|
| TC-E-01 | Escrow created with existing partner email | Wizard completes; rep details auto-fill readonly | **automated** |
| TC-E-02 | Notification email received | Email arrives for the new Escrow |
| TC-E-03 | **No new password issued** | Email contains **no** temporary password and no credential reset - the critical difference from Scenario 1 |
| TC-E-04 | Escrow ID correct | Matches the new Escrow; not an earlier one |
| TC-E-05 | Partner link opens portal | Correct environment and login page |
| TC-E-06 | Existing password still valid | Existing password authenticates | partially automated: credentials are accepted (HTTP 200 + OTP) |
| TC-E-07 | **No Change Password popup** | Goes straight to the dashboard after the OTP step |
| TC-E-08 | New Escrow associated with the partner | New Escrow ID visible in that partner's list, with correct details |

---

## 6. Data consistency (Super Admin → Email → Partner Portal)

Verify the same Escrow ID in all three places, and:

* Company Representative email identical in all three.
* Escrow attached to the correct Partner.
* Credentials issued **only** for genuinely new users.
* Existing users keep their password.
* No other partner's data visible anywhere in the flow.

The admin→portal half is automated: `test_create_escrow_end_to_end` asserts the
ID from stage 1 equals the one echoed in the confirmation dialog and that the
record then appears on the dashboard. The **email** leg is the missing third.

---

## 7. Security checks

| Check | Status |
|---|---|
| Unauthenticated deep links redirect to login | **Passing** (automated) |
| OTP target address masked in the UI | **Passing** (automated) |
| OTP resend rate-limited | **Passing** (automated) |
| Failed login gives no account enumeration | **Blocked** - all failures currently 500 (DEF-PARTNER-01) |
| Temporary password invalidated after change | NOT RUN |
| Expired/invalid link grants nothing | NOT RUN |
| Partner cannot open another partner's Escrow (ID/URL tampering) | NOT RUN - needs a session |
| Logout invalidates session; back button does not restore it | NOT RUN - needs a session |
| Passwords never exposed unnecessarily | NOT RUN - needs email contents |

---

## 8. Defects raised

### DEF-PARTNER-01 - Invalid credentials return HTTP 500 instead of 401/400

* **Severity:** High **Priority:** High
* **Environment:** Partner UAT, `excrow-uat.sprintnxt.in/EXcrowPartnerFrontV2`, Chromium 1208
* **Preconditions:** Registered partner account
* **Steps:** Open partner login → enter a valid email with a wrong password → Login
* **Expected:** `401`/`400` with a message such as "Invalid email or password"
* **Actual:** `POST /auth/login` → **HTTP 500**
  `{"code":500,"responsecode":5,"status":false,"message":"Internal server error. Please try again, or recheck the details you submitted."}`
* **Evidence:** captured request/response; `reports/screenshots/PP5_wrongpw.png`
* **Impact:** A 500 conflates "you typed it wrong" with "the server broke",
  hides genuine outages, and is likely to be logged/alerted as an incident.
* **Affected:** all partners entering a wrong password

### DEF-PARTNER-02 - Failed login shows the user nothing at all

* **Severity:** High **Priority:** High
* **Steps:** As above, then observe the page
* **Expected:** A visible error near the form or as a toast/alert
* **Actual:** No alert, no toast, no inline text, no field highlight - the form
  simply sits there. Verified `.swal2-popup`, `.toast`, `.alert`,
  `.text-danger`, `.invalid-feedback` all absent.
* **Impact:** The user cannot tell whether they mistyped, the account is
  locked, or the site is down. Likely driver of avoidable support contacts.

### DEF-PARTNER-03 - Valid credentials intermittently 500 under repeated attempts

* **Severity:** Medium **Priority:** Medium **Status:** Needs confirmation
* **Observed:** Three logins in quick succession (including one deliberately
  wrong password) returned 500 for *all* three, including the known-good
  password. A later isolated attempt with the same password returned 200.
* **Hypothesis:** rate limiting or a short lockout, surfaced as a 500 with no
  message (compounding DEF-PARTNER-02).
* **Next step:** confirm the threshold and whether a lockout is intended; if it
  is, it needs its own status code and message.

### DEF-MAIL-01 - Reusing any representative identifier silently attaches the escrow to the wrong partner

* **Severity:** High **Priority:** High
* **Corrected diagnosis.** An earlier version of this document blamed `+tag`
  address normalisation. That was **wrong** - sub-addressing works fine.
* **Actual cause:** the representative is matched on its identifying fields, and
  **the mobile number is enough on its own**. Runs that reused the existing
  partner's mobile (`9643063234`) were attached to *that* partner even though
  the email, and later the company name, were new.
* **Steps:** create an escrow with a brand-new representative email but reuse an
  existing partner's mobile number (or name)
* **Expected:** a new partner, and a welcome mail with User ID + temporary
  password
* **Actual:** the escrow is filed under the existing partner and the
  existing-partner mail is sent - link only, no credentials, greeting the
  *other* partner's company name (`EXCROW432226`)
* **Evidence:** 3 runs reusing the mobile (`+new2`, `+new3`, `+new4` - the last
  with a unique company name) all produced no credentials; 2 runs with a fully
  distinct identity (`+new21` QAEEVEQ Ltd, `+new22` QAQQOCS Ltd) both produced
  correct credential mails.
* **Why it matters:** the form gives no hint that this happened. The operator
  types a new person's details, the wizard accepts them, Review displays them,
  and the escrow is then attached to a different company. That is a
  data-integrity fault, and the missing credential mail is only its symptom.
* **Test-data rule (now enforced in code):** `NewRepresentative` randomises
  email, company, first/last name **and** mobile. Reusing any one of them
  invalidates a new-partner test.

### DEF-PARTNER-04 - After the first login, the UI ignores a successful sign-in (`allowTwoStep: false`)

* **Severity:** Critical **Priority:** Highest
* **Preconditions:** a partner who has already completed one OTP verification
* **Steps:** sign in with the correct password
* **Expected:** land on the partner dashboard
* **Actual:** `POST /auth/login` returns **200** with `status: true`,
  `allowTwoStep: false`, `firstLogin: 0` and a valid `authtoken` - and the UI
  **stays on `#/auth/login`**. No navigation, no modal, no error, no console
  output; `localStorage` stays empty and only `sprintExcrow_enc` is in
  `sessionStorage`. Navigating directly to `#/dashboard` bounces back to login.
* **Reproduced:** `+new21` (`firstLogin: 1`) and `+new22` (`firstLogin: 0`) -
  waited 35s, no change.
* **Reading:** the front end only handles the two-step branch. When the backend
  skips the OTP for an already-verified account and returns the token directly,
  nothing consumes it.
* **Impact:** a partner can complete onboarding once and is then locked out of
  the portal permanently - authentication succeeds server-side every time.

### Verified working: the full new-partner onboarding chain

With a fully distinct representative identity, every step below passed on
`+new22` / escrow `EXC-0826-838459`:

| Step | Result |
|---|---|
| Escrow created, listed on dashboard | pass |
| Welcome mail found by company name and by Escrow ID | pass |
| Mail carries User ID, Temporary Password, Escrow ID, portal link | pass |
| Escrow ID in mail == Escrow ID in portal | pass |
| Sign in with emailed credentials | pass (`allowTwoStep: true`) |
| OTP challenge (UAT fixed code `1234`, auto-submits) | pass |
| `#/auth/set-password` forced: "This is your first sign-in…" | pass |
| Fields: Password From Email / New Password / Confirm New Password | pass |
| `changePassword` -> "Password updated successfully" | pass |
| Re-login with the new password: no set-password, `firstLogin: 0` | pass |
| Re-login reaches the dashboard | **FAIL - DEF-PARTNER-04** |

Existing-representative escrows behave as specified throughout: mail with the
portal link and User ID, **no** new password, and the previously set password
remains valid.

### Open question Q-01 - Does first-time login also require an OTP?

Unknown: whether a brand-new partner using a temporary password gets the OTP
challenge *and* the Change Password prompt, and in which order. Determines
whether TC-N-05/06 are one step or three. Resolve with mailbox access.

---

## 9. Recommended next actions

1. **Provide mailbox access** (section 1, option 1 or 2) - unblocks ~30 cases.
2. **Fix DEF-PARTNER-01/02** - a login that fails silently with a 500 is worse
   for users than the email gap.
3. **Confirm the new-partner onboarding entry point** (section 2) so Scenario 1
   is written against the flow that actually issues credentials.
4. Once mail exists, automate in this order: email received → Escrow ID match →
   link target → first login → forced password change → re-login → temporary
   password invalidated.
