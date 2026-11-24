"""Opt-in invite-code accounts for the preview API.

The default single-reader mode never imports or calls this module from a route.
State is deliberately small JSON under the configured briefing state directory;
the provider ledger remains the source of truth for global spend.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from pathlib import Path

from .ingest.paths import configured_state_dir

SESSION_COOKIE = 'briefing_session'
SESSION_TTL = 60 * 60 * 24 * 30
MAX_LOGIN_FAILURES = 5
LOCKOUT_SECONDS = 15 * 60
_lock = threading.RLock()
_attempts: dict[tuple[str, str], list[float]] = {}


class AccountError(Exception):
    def __init__(self, status: int, code: str):
        super().__init__(code)
        self.status, self.code = status, code


def _root() -> Path:
    root = configured_state_dir() / 'accounts'
    root.mkdir(parents=True, exist_ok=True)
    return root


def _read(name, default):
    try:
        value = json.loads((_root() / name).read_text(encoding='utf-8'))
        return value if isinstance(value, type(default)) else default
    except (OSError, ValueError, TypeError):
        return default


def _write(name, value):
    path = _root() / name
    temporary = path.with_name(f'.{path.name}.{os.getpid()}.{secrets.token_hex(4)}.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True), encoding='utf-8')
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)


def _normal_account(value):
    if not isinstance(value, str):
        raise AccountError(400, 'invalid_account')
    value = value.strip().casefold()
    if not 3 <= len(value) <= 64 or any(c not in 'abcdefghijklmnopqrstuvwxyz0123456789._-' for c in value):
        raise AccountError(400, 'invalid_account')
    return value


def _hash_password(password, salt=None):
    if not isinstance(password, str) or not 8 <= len(password) <= 512:
        raise AccountError(400, 'invalid_password')
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode('utf-8'), salt=salt, n=2**14, r=8, p=1)
    return {'v': 1, 'salt': salt.hex(), 'hash': digest.hex(), 'n': 2**14, 'r': 8, 'p': 1}


def _verify(password, record):
    try:
        salt = bytes.fromhex(record['salt'])
        expected = bytes.fromhex(record['hash'])
        actual = hashlib.scrypt(password.encode('utf-8'), salt=salt, n=int(record['n']), r=int(record['r']), p=int(record['p']))
        return hmac.compare_digest(actual, expected)
    except (KeyError, TypeError, ValueError, AttributeError):
        return False


def _public(account):
    return {'id': account['id'], 'name': account['name']}


def _new_session(account_id):
    token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    sessions = _read('sessions.json', {})
    now = int(time.time())
    sessions[token] = {'account_id': account_id, 'csrf': csrf, 'created': now,
                       'last_seen': now, 'expires': now + SESSION_TTL}
    _write('sessions.json', sessions)
    return token, csrf


def session(token):
    if not token:
        return None
    with _lock:
        sessions = _read('sessions.json', {})
        row = sessions.get(token)
        if not isinstance(row, dict) or int(row.get('expires', 0)) <= int(time.time()):
            if token in sessions:
                sessions.pop(token, None); _write('sessions.json', sessions)
            return None
        row['last_seen'] = int(time.time()); sessions[token] = row; _write('sessions.json', sessions)
        accounts = _read('accounts.json', {})
        account = next((value for value in accounts.values()
                        if isinstance(value, dict) and value.get('id') == row.get('account_id')), None)
        return (token, row, account) if isinstance(account, dict) else None


def _check_attempts(ip, account):
    now = time.monotonic()
    with _lock:
        for key in list(_attempts):
            _attempts[key] = [t for t in _attempts[key] if t > now - LOCKOUT_SECONDS]
            if not _attempts[key]: _attempts.pop(key)
        return (len(_attempts.get(('ip', ip), [])) >= MAX_LOGIN_FAILURES or
                len(_attempts.get(('account', account), [])) >= MAX_LOGIN_FAILURES)


def _failed(ip, account):
    with _lock:
        now = time.monotonic()
        _attempts.setdefault(('ip', ip), []).append(now)
        _attempts.setdefault(('account', account), []).append(now)


def signup(invite_code, account_name, password):
    expected = os.environ.get('BRIEFING_INVITE_CODE', '')
    if not expected or not isinstance(invite_code, str) or not hmac.compare_digest(invite_code, expected):
        raise AccountError(403, 'invalid_invite_code')
    name = _normal_account(account_name)
    with _lock:
        accounts = _read('accounts.json', {})
        if name in accounts:
            raise AccountError(409, 'account_exists')
        account_id = secrets.token_hex(16)
        accounts[name] = {'id': account_id, 'name': name, 'password': _hash_password(password), 'created': int(time.time())}
        _write('accounts.json', accounts)
        token, csrf = _new_session(account_id)
    return accounts[name], token, csrf


def login(account_name, password, ip):
    name = _normal_account(account_name)
    if _check_attempts(ip, name):
        raise AccountError(429, 'login_temporarily_locked')
    accounts = _read('accounts.json', {})
    account = accounts.get(name)
    if not isinstance(account, dict) or not _verify(password, account.get('password', {})):
        _failed(ip, name)
        raise AccountError(401, 'invalid_credentials')
    with _lock:
        token, csrf = _new_session(account['id'],)
    return account, token, csrf


def logout(token):
    with _lock:
        sessions = _read('sessions.json', {})
        sessions.pop(token, None); _write('sessions.json', sessions)


def require(token, csrf=None, mutate=False):
    row = session(token)
    if not row:
        raise AccountError(401, 'authentication_required')
    if mutate and (not csrf or not hmac.compare_digest(str(csrf), str(row[1].get('csrf', '')))):
        raise AccountError(403, 'csrf_required')
    return row


def delete_account(account_id):
    with _lock:
        accounts = _read('accounts.json', {})
        accounts = {k: v for k, v in accounts.items() if v.get('id') != account_id}
        _write('accounts.json', accounts)
        sessions = _read('sessions.json', {})
        _write('sessions.json', {k: v for k, v in sessions.items() if v.get('account_id') != account_id})
        reader = _root() / 'readers' / account_id
        if reader.exists():
            import shutil
            shutil.rmtree(reader)


def cookie(token, max_age=SESSION_TTL):
    return f'{SESSION_COOKIE}={token}; Path=/; Max-Age={max_age}; HttpOnly; SameSite=Lax; Secure'

