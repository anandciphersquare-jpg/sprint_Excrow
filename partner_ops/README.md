# `partner_ops` - Partner operations, payout & wallet reconciliation

Covers everything a Partner does *after* the escrow exists: beneficiaries,
payouts, Virtual IDs, reports, service pricing and end-to-end wallet
reconciliation.

Separate from `partner/` on purpose. That suite owns **getting in** - first-time
login, the emailed temporary password, the forced password change. This one owns
**operating** once you are in, and it needs a different shape: a shared
authenticated session, money-aware fencing, and a reconciliation engine.

Full test design, execution record and defect log:
[`docs/partner-operations-test-cases.md`](../docs/partner-operations-test-cases.md).

## Run it

```bash
pytest -m partner_ops                             # read-only; moves no money
pytest partner_ops/tests/test_reconciliation.py   # the wallet ledger
PARTNER_OPS_ESCROW_ID=EXC-0726-212489 pytest -m partner_ops
```

Tests that move money are **off by default**:

| Variable | Effect |
| --- | --- |
| `PARTNER_OPS_SPEND=1` | Allow tests that submit a valid payout OTP or create a Virtual ID |
| `PARTNER_OPS_MAX_SPEND` | Per-test rupee ceiling (default `50`) |
| `PARTNER_OPS_CREATE_DATA=1` | Allow creating a beneficiary (not removable from the partner UI) |
| `PARTNER_OPS_ESCROW_ID` | Pin the escrow under test instead of auto-selecting |

`PARTNER_LOGIN_OTP` (default `1234`) and `PARTNER_PAYOUT_OTP` (default `123456`)
are UAT's fixed challenge codes. They are different lengths - 4 boxes for login,
6 for payout.

## Five things that will bite you

1. **A partner is locked out after their first login.** DEF-OPS-01: from a
   location the backend already trusts, `POST /auth/login` returns a valid
   `authtoken` that the UI never consumes. `PartnerOpsLoginPage.login()` works
   around it by sending unused coordinates so the OTP branch is taken. Without
   that workaround, nothing authenticated is reachable at all.
2. **Every payout attempt costs the wallet its charge - even when it fails**, and
   nothing is refunded (DEF-OPS-02). This is why the money-moving tests are
   fenced and why a "test every mode at min/normal/max" sweep is not run.
3. **The wallet pays charges + 18% GST; the payout amount leaves the escrow.**
   The brief's `amount + charges` formula is the *combined* cost - applying it to
   the wallet fails every payout by the transaction value.
4. **Active is not enough.** Beneficiary/Payout/Account Statement tabs appear
   only on a fully-provisioned escrow, and Virtual ID can be missing even when
   they are present. Select escrows by tab composition (`ops_escrow`).
5. **The session dies after ~2 minutes idle**, and dismissed-but-lingering CDK
   backdrops swallow every click page-wide - producing timeouts that name the
   button rather than the overlay. Clear them (`pages/overlays.py`).

## Layout

```
data/pricing.py    Service pricing read from the portal: slabs, percentage
                   with min/max, 18% GST. Never a hard-coded charge.
ledger.py          Reconciliation: running-balance chain, expected-move,
                   GST breakdown, dashboard-vs-statement.
pages/             login, dashboard, escrow, beneficiary, payout,
                   virtual_id, reports, overlays
tests/             8 modules, 111 tests, mapped to PARTS 1-27
```

## Conventions

* Known defects are `xfail(strict=False)` with the defect id and evidence in the
  reason, so the suite stays green **and** tells you the day a defect is fixed.
* A blocked area skips with the precondition and the endpoints to look for -
  never with invented locators. PARTS 17-20 are the live example.
* Financial assertions carry the arithmetic in the failure message, so output can
  be pasted straight into a defect report.
