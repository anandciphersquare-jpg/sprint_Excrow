# Partner Portal - Operations, Payout & Wallet Reconciliation

Test design, execution record and defect log for the Partner-side workflow that
follows escrow creation: activation state, beneficiaries, payouts, Virtual IDs,
reports, service pricing and end-to-end wallet reconciliation.

* **Environment** SprintNXT Partner Portal - UAT
  (`https://excrow-uat.sprintnxt.in/EXcrowPartnerFrontV2/`)
* **Backend** `.../EXcrowPartnerBackendV2/`
* **Account** `anand.kumar@paysprint.in` (established partner, 31 escrows)
* **Executed** 14 August 2026
* **Automation** `partner_ops/` - run with `pytest -m partner_ops`
* **Companion doc** `post-creation-email-and-partner-login.md` covers the
  *first-time* partner: invitation email, temporary password and the forced
  password change. That ground is not repeated here.

---

## 1. Executive summary

The financial plumbing is sound where it was measurable: the wallet ledger's
running balance chains correctly to the paisa, GST is applied consistently at
18%, VID creation charges match the configured price exactly, and the payout
report agrees with the wallet statement.

Two defects are serious enough to block a release:

* **DEF-OPS-01 (Critical)** - a partner who signs in a second time from the same
  location is **locked out of the portal permanently**. The backend issues a
  valid token on a trusted location and the front end never consumes it.
* **DEF-OPS-02 (Critical)** - a **failed payout keeps its charge**. A ₹10
  transfer that the portal reported as failed still took ₹14.75 from the wallet
  with no reversal.

Twelve defects in total are recorded in §6. Every financial claim in this
document is backed by a captured before/after balance, a transaction id, or an
API response body - not by a success message.

| Area | Result |
| --- | --- |
| Login, OTP, session policy | Pass, except DEF-OPS-01 |
| Escrow list, filters, state gating | Pass, except DEF-OPS-10 / 11 |
| Service pricing (read, not assumed) | Pass |
| Beneficiary create / validate / verify | Pass |
| Payout: modes, limits, OTP, negatives | Pass, except DEF-OPS-03 / 04 |
| Failed-payout refund | **Fail** - DEF-OPS-02 |
| Payout report ↔ wallet statement | Pass, except DEF-OPS-08 |
| Virtual ID create + charge | Pass |
| VID payout structure / Pay Now | **Not reachable** - see §7 |
| Bulk payment execution | **Not executed** by choice - see §7 |
| Collection (inbound) | Accounting verified; cannot be initiated here |

---

## 2. How the workflow actually works

Worth reading before writing any test against this portal - several of these
cost real debugging time.

### 2.1 Navigation

| Route | Screen |
| --- | --- |
| `#/dashboard` | Stat cards, payout/collection charts |
| `#/escrow-list` | Escrow Management |
| `#/escrow-detail/{project,documents,virtual-account,beneficiary,payout,account-statement,charges}` | Escrow detail tabs |
| `#/reports-statement/{payout,wallet}` | Reports (5 tabs) |
| `#/wallet-management` | Wallet funding / accounts |
| `#/audit-trail-statement`, `#/profile-statement`, `#/active-bank-credentials` | Supporting screens |

**There is no free-text search on the escrow list.** It filters by Bank, Industry
and Status only. The brief's "search by Escrow Name" is a filter-plus-scan.

### 2.2 The escrow detail tab set is conditional

This is the single most useful discovery for anyone automating here. An escrow
being **Active is not sufficient** to operate on it:

| Escrow | Tabs |
| --- | --- |
| `EXC-0826-859771` | Project Information, Documents, Virtual ID, Charges |
| `EXC-0826-934758` | Project Information, Documents, Beneficiary, Payout, Account Statement, Charges |
| `EXC-0726-212489` | all seven |

Beneficiary/Payout/Account Statement appear only once the escrow is provisioned
(VID prefix + usable bank credential), and Virtual ID can be absent even when
Beneficiary and Payout are present. Tests must select an escrow by **tab
composition**, not by status - which is what `ops_escrow` does.

Two hard gates seen live:

* Opening some Active escrows raises **"Please add bank credentials first"**.
* `+ Create Account` on an escrow with no prefix raises **"No Prefix found!
  Please contact to administration"**.

### 2.3 Money: what the wallet pays for

Measured, not assumed:

| Activity | Wallet | Escrow account |
| --- | --- | --- |
| Payout (success **or** failure) | −(charge + 18% GST) | transferred amount |
| VID creation | −(charge + 18% GST) | - |
| Bank / TSP maintenance | −(charge + 18% GST) | *claimed*, not observed |
| Manual funding | +amount | - |

So the brief's `Expected Wallet Deduction = Transaction Amount + Charges`
describes the **combined** cost. The **wallet leg is charges + GST only** - the
transferred amount leaves the escrow account. Asserting the brief's formula
against the wallet fails every payout by the transaction value.

**GST at 18% is added on top of every charge and is never mentioned on the
Charges tab.** The tab quotes net; the wallet loses gross. Both the statement and
the payout report spell the split out, which is how the rate was confirmed:
`₹12.00 + ₹2.16 = ₹14.16`, `₹25.50 + ₹4.59 = ₹30.09`, `₹100.00 + ₹18.00 = ₹118.00`.

### 2.4 Configured Service Pricing (read from the portal)

`EXC-0826-859771` → Escrow Detail → Charges:

| Service | Basis | Configured |
| --- | --- | --- |
| IMPS | FIXED, slabs | ₹0–1,000 → ₹0.00; ₹1,000–25,000 → ₹0.00; ₹25,000–5,00,000 → ₹0.00 |
| NEFT | FIXED | ₹0.00 / txn |
| RTGS | FIXED | ₹0.00 / txn |
| UPI Collection | FIXED | ₹0.00 / txn |
| Collection via VID | PERCENTAGE | 1.5% / collection, min ₹5.00, max ₹50.00 |
| VID Creation | FIXED | ₹1.00 / VID |
| Bank Charges | MONTHLY | ₹100.00, auto-debit, 8th of month |
| TSP Charges | MONTHLY | ₹100.00, auto-debit, 8th of month |

Pricing is **per escrow**: `EXC-0726-212489` charges ₹12.00 for VID creation and
₹12.50–₹25.50 for IMPS depending on amount. Never hard-code a charge - read the
escrow's own tab, which is what `partner_ops/data/pricing.py` does.

### 2.5 Authentication and session

* Login is two-step: credentials → **4-digit** OTP (`1234` in UAT, auto-submits
  on the 4th digit). The **payout** OTP is **6-digit** (`123456`) - a different
  length, so the two must not share a helper.
* The login button is disabled reading *"Location Loading..."* until
  `navigator.geolocation` answers. The context must grant geolocation.
* `POST /auth/login` branches on the coordinates sent - see DEF-OPS-01.
* **Idle timeout is ~2 minutes**, with a 60-second "Are you still there?"
  countdown, then `#/auth/session-expired`. Any step that idles longer dies
  mid-test. `auth/extend-session` exists for keep-alive.

### 2.6 Overlays swallow clicks

Alerts (`.alert-modal-panel`) and the idle prompt
(`.session-timeout-modal-panel`) each add a `.cdk-overlay-backdrop` that
intercepts pointer events **page-wide**, and they stack. An undismissed backdrop
turns the next click into a 30-second timeout whose error names the *button*, not
the overlay - which reads as "the Submit button is broken". Clear overlays
explicitly (`partner_ops/pages/overlays.py`).

---

## 3. Wallet ledger - measured evidence

Every row below is a real statement entry captured on 14 Aug 2026, oldest first.
The running balance chains exactly.

| Activity | Opening | Charge | GST | Debit | Closing | Evidence |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| Manual funding | 182.24 | - | - | +500.00 | 682.24 | `EXC-0826-426668` |
| Bank charges | 682.24 | 100.00 | 18.00 | 118.00 | 564.24 | `AMC35820260814` |
| Tcp charges | 564.24 | 100.00 | 18.00 | 118.00 | 446.24 | `AMC35920260814` |
| Bank charges | 446.24 | 100.00 | 18.00 | 118.00 | 328.24 | `AMC36020260814` |
| Tcp charges | 328.24 | 100.00 | 18.00 | 118.00 | 210.24 | `AMC36120260814` |
| IMPS payout ₹4,343 (**Success**) | 210.24 | 25.50 | 4.59 | 30.09 | 180.15 | `1786686013906`, UTR `TEST558691` |
| IMPS payout ₹10 (**reported failed**) | 180.15 | 12.50 | 2.25 | 14.75 | 165.40 | `1786693437161`, UTR `NA` → **DEF-OPS-02, DEF-OPS-08** |
| VID creation | 165.40 | 12.00 | 2.16 | 14.16 | 151.24 | `VA123620260814011726` |

**Checks performed**

* Running balance chains across all 40 visible entries - **pass**.
* GST is 18% of the net charge on every row that publishes a breakdown - **pass**.
* VID creation debited exactly the configured price + GST - **pass** (PART 15).
* Dashboard = Wallet Statement **after refreshing the card** - **pass**
  (before refresh they disagreed; DEF-OPS-05).
* Failed payout refunded - **FAIL** (DEF-OPS-02).

---

## 4. Coverage against the brief (PARTS 1-27)

Status key: **Pass** · **Fail** (defect raised) · **N/A** (not this portal) ·
**Blocked** (precondition unavailable) · **Deferred** (deliberately not run).

| PART | Scope | Status | Notes |
| --- | --- | --- | --- |
| 1 | First login, password change | Pass / covered elsewhere | New-partner path is owned by `post-creation-email-and-partner-login.md`. Login, OTP, masking, route guards, session policy verified here. **DEF-OPS-01**. |
| 2 | Activate escrow (wizard) | N/A | Partner-side detail is **read-only** - no Next/Submit/Confirm chain. Activation is a Super Admin flow (`admin/tests/test_review_submit.py`). Asserted read-only. |
| 3 | Verify activated escrow | Pass | ID/name/bank/status agree between list and detail; filters honoured; Inactive escrows expose no Payout/Beneficiary. **DEF-OPS-10/11**. |
| 4 | Beneficiary creation + negatives | Pass | 14 fields mapped; 11 invalid-input cases assert the portal's exact messages; duplicate account refused; guarded submit fires no API call. |
| 5 | Beneficiary verification | Pass | List/detail verified; Deactive beneficiaries expose **no** payout action; `Verify Beneficiary` offered for unverified records. |
| 6 | Payout - every transfer mode | Pass | **IMPS, NEFT, RTGS, A2A** enumerated and asserted; 3-step wizard; 6-digit OTP; `send-payout-otp` → `excrow-payout-request`. |
| 7 | Payout positive cases | Partial | One real payout executed (₹10 IMPS). Broader min/normal/max sweeps deliberately fenced - see §7.1. |
| 8 | Payout negatives | Pass | Invalid OTP, partial OTP, empty/zero/negative/non-numeric amount, below-minimum, above-maximum, disabled beneficiary. **DEF-OPS-04**. |
| 9 | Failed payment & refund | **Fail** | **DEF-OPS-02** - charge retained, no reversal. |
| 10 | Reports → Payout | Pass | Columns, charge breakdown, status filter, export present; report charges reconcile with wallet debits. **DEF-OPS-08**. |
| 11 | Wallet statement | Pass | Running balance chains; dashboard matches after refresh. **DEF-OPS-05**. |
| 12 | Service pricing & charges | Pass | Pricing parsed from the portal; slab/percentage/min/max modelled; GST verified. **DEF-OPS-06/07**. |
| 13 | Virtual ID creation | Pass | Form mapped; VID created live; `va/check-duplicate` → `va/create-va`. |
| 14 | VID positive/negative | Pass | Empty form, invalid phone/PAN/pincode, negative limits, duplicate VID number. |
| 15 | VID wallet charge | Pass | ₹14.16 debited for a ₹12.00 configured charge (+18% GST); statement and dashboard agree. |
| 16 | VID list | Pass | 13 columns; prefix/limits/charge/status validated. |
| 17 | VID payout structure matrix | **Blocked** | No entry point - see §7.2. |
| 18 | Payout structure validation | **Blocked** | Same. |
| 19 | VID Pay Now | **Blocked** | No Pay Now control; payouts run from the escrow Payout tab. |
| 20 | Pay Now negatives | **Blocked** | Same; equivalent negatives covered for the escrow payout flow. |
| 21 | Bulk payment | Deferred | Entry points asserted; execution withheld - see §7.1. |
| 22 | Bulk reconciliation | Deferred | Report surfaces asserted; per-transaction reconciliation blocked on §7.1. |
| 23 | End-to-end wallet ledger | Pass | §3; automated in `test_end_to_end_wallet_ledger`. |
| 24 | Collection | Partial | Collection is **inbound** - not initiable from this portal. Pricing bounds, VID Collection report and wallet charges verified. |
| 25 | Negative & boundary sweep | Pass | Distributed across modules; see the per-PART rows. |
| 26 | Security | Pass | Masking, route guards, tampered identifiers, OTP gate, storage hygiene. Cross-tenant deferred - §7.3. **DEF-OPS-09/11**. |
| 27 | Final acceptance | **Not met** | Blocked by DEF-OPS-01 and DEF-OPS-02. |

---

## 5. Automated suite

```
partner_ops/
  data/pricing.py    Service-pricing model: slabs, percentage+min/max, 18% GST
  ledger.py          Reconciliation engine: running-balance chain, expected-move,
                     GST breakdown, dashboard-vs-statement
  pages/             login, dashboard, escrow, beneficiary, payout, virtual_id,
                     reports, overlays
  tests/             8 modules, 111 tests
```

Run:

```bash
pytest -m partner_ops                      # read-only; moves no money
pytest partner_ops/tests/test_reconciliation.py
PARTNER_OPS_ESCROW_ID=EXC-0726-212489 pytest -m partner_ops
```

Money-moving tests are **off by default** and must be opted into:

| Variable | Effect |
| --- | --- |
| `PARTNER_OPS_SPEND=1` | Allows tests that submit a valid payout OTP or create a VID |
| `PARTNER_OPS_MAX_SPEND` | Per-test ceiling in rupees (default 50) |
| `PARTNER_OPS_CREATE_DATA=1` | Allows creating a beneficiary (not removable from the partner UI) |
| `PARTNER_OPS_ESCROW_ID` | Pin the escrow under test |

Known defects are encoded as `xfail(strict=False)`, so the suite is green while
they stand **and tells you the day they are fixed** rather than quietly agreeing.

---

## 6. Defect log

All raised 14 Aug 2026 · Environment UAT · Reporter QA Automation ·
Account `anand.kumar@paysprint.in`.

---

### DEF-OPS-01 - Partner is permanently locked out after the first login
**Severity Critical · Priority P1 · Module Authentication**

**Scenario** The backend applies trusted-location 2FA. The Angular client
implements only the OTP branch, so on a trusted location it receives a valid
session token and discards it.

**Steps**
1. Sign in successfully from any location (OTP `1234`) and complete the session.
2. Sign in again with the same credentials from the same place.

**Expected** Signed in, dashboard shown.
**Actual** `POST /auth/login` returns **HTTP 200** with
`{"allowTwoStep": false, "firstLogin": 0, "authtoken": "<valid token>"}`. The UI
**stays on `#/auth/login`** - no navigation, no error, no message, empty
`localStorage`. `#/dashboard` bounces back. The partner cannot get in again.

**Isolating evidence** - three consecutive `POST /auth/login` calls:

| Coordinates | `allowTwoStep` | Token issued |
| --- | --- | --- |
| 28.6139 / 77.2090 (already used) | `false` | yes |
| 19.0760 / 72.8777 (new) | `true` | no |
| 28.6139 / 77.2090 (already used) | `false` | yes |

So the branch is chosen by **whether the location has been verified before**, and
the failing branch is the one the UI cannot handle. Clearing cookies,
`localStorage` and `sessionStorage` does not help - the state is server-side.

**Impact** Every partner is locked out after their first successful sign-in from
their usual location. This is consistent with DEF-PARTNER-04, previously observed
on new partners; the trusted-location mechanism is the root cause.

**Workaround (test harness only)** Rewrite the login payload with unused
coordinates so the OTP branch is taken - `PartnerOpsLoginPage.force_fresh_location`.
Not available to a real user.

**Fix** Handle the `allowTwoStep: false` response: store `authtoken` and route to
the dashboard (or `#/auth/set-password` when `firstLogin` is set).

---

### DEF-OPS-02 - Failed payout keeps its charge; no refund
**Severity Critical · Priority P1 · Module Payout / Wallet**

**Steps**
1. Wallet balance before: **₹180.15**.
2. Escrow `EXC-0726-212489` → Payout → beneficiary *Mohammad Forbes*.
3. Bank Transfer, **IMPS**, amount **₹10**, remarks, *Make Payout*.
4. OTP `123456` → *Verify & Pay*.

**Expected** Either the payout succeeds, or it fails and any charge taken is
reversed. PART 9: no permanent deduction for a transaction that did not happen.

**Actual** The portal showed **"Your request has been failed."**
(`payout/excrow-payout-request` → `{"code":201,"responsecode":3,"status":false}`).
The wallet was nevertheless debited **₹14.75** (charge ₹12.50 + GST ₹2.25):

* Wallet before **₹180.15** → after **₹165.40**
* Statement entry `TXN 1786693437161 · Escrow EXC-0726-212489`, `−₹14.75`, `Bal ₹165.40`
* **No credit or reversal entry anywhere in the statement**

**Systematic, not a one-off** - historical `Failure` rows carry full charges:

| Date | Txn | Amount | Charge | Status | UTR |
| --- | --- | ---: | ---: | --- | --- |
| 04-08 16:53:51 | `SPL000000952` | 19.00 | ₹24.78 | Failure | NA |
| 04-08 16:15:24 | `1785840317248` | 1111.00 | ₹24.78 | Failure | NA |
| 04-08 13:44:39 | `SPL000000948` | 690.00 | ₹14.28 | Failure | NA |

**Impact** Partners are charged for transfers that never happen, and a
failing beneficiary or downstream outage drains the wallet through retries. The
₹10 attempt cost ₹14.75 - 147% of the transaction value.

**Fix** Reverse the charge when the payout does not settle, and post the reversal
to the wallet statement. If retaining the fee is intended, disclose it before
authorisation (see DEF-OPS-03) and label it in the statement.

---

### DEF-OPS-03 - Charge is never disclosed before the partner authorises
**Severity High · Priority P2 · Module Payout**

**Steps** Payout → any beneficiary → IMPS, ₹100 → *Make Payout* → OTP screen.

**Expected** The amount, the charge, the GST and the total wallet impact shown
before the OTP is entered.
**Actual** The Details step shows no fee. The Verify step restates only
`₹100.00 to <beneficiary> · IMPS`. No charge, GST or total appears at any point.
The wallet is then debited charge + GST (₹14.75 on a ₹10 transfer).

**Impact** The partner authorises a debit whose size the portal never states -
material for a fee that can exceed the transaction value, and compounding
DEF-OPS-02 because the undisclosed fee is also non-refundable.

---

### DEF-OPS-04 - Per-mode amount limits enforced only after the OTP is spent
**Severity Medium · Priority P3 · Module Payout**

**Steps** Payout → IMPS → amount **₹1** → *Make Payout* → OTP `123456` → *Verify & Pay*.

**Expected** The Details step rejects an amount outside the mode's range before
sending an OTP.
**Actual** Details accepts ₹1, an OTP is sent and consumed, and only the final
request fails: **"For imps, enter an amount between ₹10 and ₹500000"**. The limit
was knowable up front. The partner must restart and burn another OTP.

---

### DEF-OPS-05 - Dashboard wallet balance is stale until manually refreshed
**Severity Low · Priority P4 · Module Dashboard**

After a payout the dashboard card still read **₹180.15** while the wallet
statement already read **₹165.40**. Using the card's own refresh control, or
reloading, corrected it to ₹165.40.

**Impact** The two screens contradict each other on the balance, which is exactly
the invariant partners are told to reconcile against. The underlying ledger is
correct - this is presentation staleness, not a money fault.

---

### DEF-OPS-06 - Maintenance charges debit the wallet, not the escrow account
**Severity Medium · Priority P3 · Module Charges / Wallet**

The Charges tab states maintenance charges are *"deducted directly from the
escrow account"*. The wallet statement shows them as **wallet** debits:
`Bank Charges −₹118.00`, `Tcp Charges −₹118.00` (`AMC35820260814`,
`AMC35920260814`, …). One of the two is wrong; the money leaves the wallet.

---

### DEF-OPS-07 - Same charge named "TSP Charges" and "Tcp Charges"
**Severity Cosmetic · Priority P5 · Module Charges / Reports**

Escrow → Charges calls it **TSP Charges**; the wallet statement calls the same
debit **Tcp Charges**. Reconciliation by label fails between the two screens.

---

### DEF-OPS-08 - UI reports failure, report records the payout as "Processed"
**Severity High · Priority P2 · Module Payout / Reports**

The ₹10 payout of DEF-OPS-02 was reported to the user as **"Your request has been
failed."**, but Reports → Payout records the same transaction as:

| Date | Txn | Bene | Amount | Charges | UTR | Status |
| --- | --- | --- | ---: | ---: | --- | --- |
| 14-08-2026 13:14:05 | `1786693437161` | Mohammad Forbes | 10.00 | ₹14.75 | NA | **Processed** |

**Impact** The partner is told the transfer failed while the system considers it
in flight. They cannot tell whether ₹10 left the escrow, and a retry risks
double payment.

---

### DEF-OPS-09 - Inconsistent masking of account numbers
**Severity Low · Priority P4 · Module Beneficiary / Escrow list**

The escrow list masks its own bank account (`XXXXXXXX7944`), while the
beneficiary list shows full account numbers (`8253245678`, `112345676543`) and
the Virtual ID list shows full PANs. Balances are masked behind an eye toggle,
so the product clearly intends masking - the policy is applied inconsistently.

---

### DEF-OPS-10 - Rows Per Page "All" fails and breaks the next request
**Severity High · Priority P2 · Module Escrow list**

**Steps** Escrow Management → Rows Per Page → **All**.

**Expected** All 31 escrows listed.
**Actual** A **FAILED** dialog reading **"Unsupported operand types: string *
int"**. The list keeps its previous result set, and the **next** request fails
the same way - so a status filter applied afterwards silently returns unfiltered
rows and appears broken itself.

Numeric page sizes (10/20/50/100/200/500) all work: `500` returns 31 rows, and
filtering to Active then correctly returns 6.

**Probable cause** The page-length string `"All"` is used in arithmetic
server-side (offset = length × page).

---

### DEF-OPS-11 - Raw server error text shown to the user
**Severity Medium (security) · Priority P3 · Module Error handling**

The DEF-OPS-10 failure surfaces the verbatim PHP message **"Unsupported operand
types: string * int"** in the user-facing dialog. Internal type errors disclose
implementation detail and give the partner nothing actionable.

---

### DEF-OPS-12 - Rejections return HTTP 201
**Severity Low · Priority P4 · Module API hygiene**

`payout/excrow-payout-request` answers **HTTP 201** with `status:false`,
`responsecode:3` for *Invalid OTP!*, *Your request has been failed.* and the
amount-limit rejection. A client checking status codes would read every rejection
as success. (Consistent with DEF-PARTNER-01/02, where a wrong password returns
500.) Callers must parse the body - `PayoutResult` does.

---

## 7. Gaps, and how to close them

Stated plainly rather than papered over.

### 7.1 Bulk payment and broad payout sweeps - not executed, by choice

The brief asks for every mode at minimum/normal/maximum and a full bulk run.
Both were withheld, for one reason: **DEF-OPS-02 means every attempt costs the
wallet its charge whether or not it succeeds, with no refund.** The wallet holds
₹151.24. A four-mode × three-amount sweep is twelve unrefundable charges; a bulk
batch is many more, and its failures would be unreconcilable against a defect
that already loses money.

The tests are written and fenced behind `PARTNER_OPS_SPEND=1` with a spend
ceiling. **Once DEF-OPS-02 is fixed and the wallet is funded, remove the fence
and run them** - the assertions for PART 22 (per-transaction amounts, charges,
totals, refunds, `total successful + charges == wallet deduction`) are already
expressed in `partner_ops/ledger.py`.

### 7.2 VID payout structure and Pay Now (PARTS 17-20) - no entry point

The six Payment Mode × Split Type combinations could not be tested: on every
escrow reachable by this account the VID list's Action cell is empty and no split
editor or *Pay Now* control renders. The capability clearly exists - the bundle
carries `merchant/split` and `merchant/virtual-account/<id>/split` - so it is
gated on provisioning or a role this account lacks.

`test_payout_structure_entry_point_is_absent` **fails the moment the control
appears**, which is the signal to implement the matrix. No invented locators were
written for a screen nobody has seen.

**To close:** provision a merchant-enabled VID (or grant the role), then cover
Automatic/Instant/Manual × Percentage/Fixed plus the PART 18 boundaries (0%,
100%, >100%, negative, decimal, zero/negative fixed, duplicates, edits).

### 7.3 Cross-tenant isolation (PART 26) - needs a second account

Verifying that partner A cannot reach partner B's escrow, beneficiary or VID
cannot be honestly tested from one login. Escrow identifiers are not in the URL
(context is held in app state), so there is nothing to tamper with from here, and
probing another tenant's data without an authorisation scope would not be
appropriate.

**To close:** provision partner B, capture one of its escrow ids, then assert
partner A's session cannot load it via the list, the `#/escrow-detail` routes or
the report filters.

### 7.4 Collection (PART 24) - inbound only

A collection is money arriving at a Virtual ID or UPI VPA from outside; nothing
in the partner portal initiates one. What is verified: the configured 1.5%
price with its ₹5/₹50 bounds behaves correctly at both ends, the VID Collection
report renders, and any collection charge in the wallet respects the band.
Driving a real collection needs a bank-side push or a simulator.

---

## 8. Regression checklist for the fixes

When DEF-OPS-01 and DEF-OPS-02 are addressed, re-run in this order:

1. `pytest partner_ops/tests/test_login_and_session.py` -
   `test_known_location_login_is_not_consumed_by_the_ui` should flip to **XPASS**;
   remove its `xfail` and drop the `force_fresh_location` workaround from
   `ops_session`.
2. Fund the wallet, then
   `PARTNER_OPS_SPEND=1 pytest partner_ops/tests/test_payout.py`.
3. `pytest partner_ops/tests/test_reconciliation.py` -
   `test_failed_payout_charge_is_refunded` should flip to **XPASS**.
4. Remove the fence in `test_execute_a_bulk_payment` and complete PARTS 21-22.
5. Re-check DEF-OPS-03/04 - a disclosed charge and an up-front limit check will
   each fail their current assertion, which is the intended signal.
