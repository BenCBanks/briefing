#!/usr/bin/env python3
"""Flag-gated operator commands for account status and password resets."""
from __future__ import annotations

import argparse
import os

from dev import accounts


def _require_enabled() -> bool:
    if os.environ.get('BRIEFING_ACCOUNTS', '').strip().lower() == 'on':
        return True
    print('Account administration is unavailable while accounts are disabled.')
    return False


def _resolve(short_id: str) -> str | None:
    return accounts._account_id_by_prefix(short_id)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('list', help='list short account IDs and enabled status')
    for command in ('disable', 'enable', 'reset'):
        action = sub.add_parser(command)
        action.add_argument('short_id')
    args = parser.parse_args(argv)
    if not _require_enabled():
        return 2
    if args.command == 'list':
        rows = accounts.admin_accounts()
        for row in rows:
            state = 'disabled' if row['disabled'] else 'enabled'
            print(f"{row['short_id']} {state}")
        if not rows:
            print('No accounts.')
        return 0
    account_id = _resolve(args.short_id)
    if not account_id:
        print('Account not found or ID is ambiguous.')
        return 1
    if args.command in ('disable', 'enable'):
        if not accounts.set_disabled(account_id, args.command == 'disable'):
            print('Account not found.')
            return 1
        state = 'disabled' if args.command == 'disable' else 'enabled'
        print(f"{account_id[:8]} {state}.")
        return 0
    code = accounts.issue_password_reset(account_id)
    if code is None:
        print('Reset unavailable for this account.')
        return 1
    print(f'Reset code (expires in 30 minutes): {code}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
