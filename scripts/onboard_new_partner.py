"""Create an Escrow for a brand-new Company Representative.

This is the onboarding path: because the representative address has no partner
record, the portal creates the partner and emails a **User ID + temporary
password + Escrow ID + partner link** to it. Sub-addressing
(`anand.kumar+new2@paysprint.in`) keeps each run a distinct new partner while
still landing in the one real inbox.

    python scripts/onboard_new_partner.py --tag new2 --headed

Writes the created Escrow ID and the representative address to the artifact
store, so the partner-side checks know what to look for in the mail.
"""
from __future__ import annotations

import argparse
import sys

from playwright.sync_api import sync_playwright

from admin.data.escrow_data import (EscrowData, NewRepresentative,
                                    unique_alias_tag)
from admin.pages.escrow_creation_flow import EscrowCreationFlow
from admin.pages.login_page import AdminLoginPage
from config.settings import settings
from core.artifact_store import ArtifactStore


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", default=None,
                        help="sub-address tag, e.g. new2 -> anand.kumar+new2@… "
                             "(default: a random unused one)")
    parser.add_argument("--headed", action="store_true", help="show the browser")
    parser.add_argument("--slowmo", type=int, default=0, help="ms between actions")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    tag = args.tag or unique_alias_tag()
    rep = NewRepresentative.with_tag(tag)
    data = EscrowData(new_representative=rep)

    print(f"Representative : {rep.email}  (new - details typed by hand)")
    print(f"  name         : {rep.first_name} {rep.last_name}")
    print(f"  company      : {rep.company_name}")
    print(f"  mobile       : {rep.mobile}")
    print(f"Escrow name    : {data.name}")
    print(f"Account / IFSC : {data.account_number} / {data.ifsc}")
    print(f"Account types  : {', '.join(data.account_types)}")
    print(f"Pricing        : {data.rate_type} / {data.charge_frequency}")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not args.headed,
                                    slow_mo=args.slowmo)
        context = browser.new_context(
            ignore_https_errors=True,
            viewport={"width": 1440, "height": 900},
            permissions=["geolocation"],
            geolocation={"latitude": 28.6139, "longitude": 77.2090},
            locale="en-IN",
        )
        context.set_default_timeout(settings.default_timeout_ms)
        page = context.new_page()

        login = AdminLoginPage(page)
        login.open()
        login.login(settings.admin_username, settings.admin_password,
                    settings.admin_pin)
        login.assert_dashboard_loaded()

        flow = EscrowCreationFlow(page)
        escrow_id = flow.create(data)
        print(f"\nCreated Escrow ID: {escrow_id}")

        store = ArtifactStore()
        store.set("escrow_id", escrow_id)
        store.set("escrow_name", data.name)
        store.set("new_partner_email", rep.email)
        store.set("new_partner_company", rep.company_name)

        listed = flow.nav.is_listed(escrow_id)
        print(f"Listed on dashboard: {listed}")
        context.close()
        browser.close()

    print("\nNow check the inbox. The welcome email should carry:")
    print(f"  User ID           : {rep.email}")
    print(f"  Your Escrow ID    : {escrow_id}")
    print("  Temporary Password: (generated - read it from the mail)")
    print("  A 'Go to Dashboard' / set-password link to the partner portal")
    return 0


if __name__ == "__main__":
    sys.exit(main())
