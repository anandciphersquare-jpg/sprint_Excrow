#!/usr/bin/env python3
"""Run the cross-portal chain: admin creates an escrow, partner verifies it.

Usage:
    python scripts/run_e2e.py            # headless
    python scripts/run_e2e.py --headed   # watch it run
Extra args are forwarded to pytest, e.g. --browser firefox --slowmo 200
"""
import subprocess
import sys

EXTRA = sys.argv[1:]


def run(args: list[str]) -> int:
    print(">>>", " ".join(args))
    return subprocess.call(args)


def main() -> int:
    admin = run(["pytest", "admin/tests/test_create_escrow.py", "-m", "e2e", *EXTRA])
    if admin != 0:
        print("Admin creation failed; skipping partner verification.")
        return admin
    return run(["pytest", "partner/tests/test_verify_escrow.py", "-m", "e2e", *EXTRA])


if __name__ == "__main__":
    raise SystemExit(main())
