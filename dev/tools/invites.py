#!/usr/bin/env python3
"""Create and manage hashed, expiring account invite codes."""
from __future__ import annotations

import argparse
import hashlib
import secrets
import time
from datetime import datetime, timezone

from dev import accounts


def _digest(code: str) -> str:
    return hashlib.sha256(code.encode('utf-8')).hexdigest()


def create(count: int, expires_hours: int) -> list[str]:
    if not 1 <= count <= 100 or not 1 <= expires_hours <= 24 * 30:
        raise ValueError('count must be 1..100 and expiry must be 1..720 hours')
    expires = int(time.time()) + expires_hours * 3600
    codes = [secrets.token_urlsafe(18) for _ in range(count)]
    with accounts._lock:
        invites = accounts._read('invites.json', {})
        for code in codes:
            invites[_digest(code)] = {'expires': expires, 'remaining': 1}
        accounts._write('invites.json', invites)
    return codes


def list_unused(now: int | None = None) -> list[dict]:
    now = int(time.time()) if now is None else now
    with accounts._lock:
        invites = accounts._read('invites.json', {})
    return [
        {'digest': digest, 'expires': datetime.fromtimestamp(int(row['expires']), timezone.utc).isoformat(),
         'remaining': int(row['remaining'])}
        for digest, row in sorted(invites.items())
        if isinstance(row, dict) and int(row.get('expires', 0)) > now and int(row.get('remaining', 0)) > 0
    ]


def revoke(code_or_digest: str) -> bool:
    if not isinstance(code_or_digest, str) or not code_or_digest:
        return False
    digest = code_or_digest if len(code_or_digest) == 64 else _digest(code_or_digest)
    with accounts._lock:
        invites = accounts._read('invites.json', {})
        row = invites.get(digest)
        if not isinstance(row, dict):
            return False
        row = dict(row)
        row['remaining'] = 0
        invites[digest] = row
        accounts._write('invites.json', invites)
    return True


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    make = sub.add_parser('create', help='create codes; each code is printed once')
    make.add_argument('-n', '--count', type=int, default=1)
    make.add_argument('--expires-hours', type=int, default=24)
    sub.add_parser('list', help='list unused invite digests and expiry times')
    revoke_parser = sub.add_parser('revoke', help='revoke by code or stored digest')
    revoke_parser.add_argument('code_or_digest')
    args = parser.parse_args(argv)
    if args.command == 'create':
        for code in create(args.count, args.expires_hours):
            print(code)
        return 0
    if args.command == 'list':
        for row in list_unused():
            print(f"{row['digest']}\t{row['expires']}\tremaining={row['remaining']}")
        return 0
    if not revoke(args.code_or_digest):
        parser.error('invite not found')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
