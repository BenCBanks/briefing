"""Opt-in invite-code accounts for the preview API."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import shutil
import threading
import time
import unicodedata
from pathlib import Path

try:
    from .ingest.paths import configured_state_dir
except ImportError:  # Direct preview_server imports used by fixture modules.
    from ingest.paths import configured_state_dir

SESSION_COOKIE = 'briefing_session'
SCRYPT_N, SCRYPT_R, SCRYPT_P = 2**14, 8, 1
PASSWORD_MIN, PASSWORD_MAX = 10, 256
MAX_SESSIONS = 10_000
SESSION_ABSOLUTE_SECONDS = 60 * 60 * 24 * 30
SESSION_IDLE_SECONDS = 60 * 60 * 24 * 7
DEFAULT_DAILY_EPISODE_LIMIT = 3
MAX_DAILY_EPISODE_LIMIT = 100
MAX_LOGIN_FAILURES = 5
MAX_SIGNUP_FAILURES = 5
LOCKOUT_SECONDS = 15 * 60
COMMON_PASSWORDS = frozenset({
    '1234567890', 'password123', 'qwerty12345', 'letmein1234',
    'welcome1234', 'correcthorsebatterystaple', 'iloveyou1234',
    'admin123456', 'abc1234567', 'password1!', 'monkey12345',
})
_lock = threading.RLock()
_attempts: dict[tuple[str, str], list[float]] = {}


def daily_episode_limit() -> int:
    """Return the operator's per-reader limit without allowing bad config."""
    raw = os.environ.get('BRIEFING_ACCOUNT_DAILY_EPISODE_LIMIT', '').strip()
    if not raw:
        return DEFAULT_DAILY_EPISODE_LIMIT
    try:
        value = int(raw, 10)
    except ValueError:
        return DEFAULT_DAILY_EPISODE_LIMIT
    return value if 1 <= value <= MAX_DAILY_EPISODE_LIMIT else DEFAULT_DAILY_EPISODE_LIMIT


class AccountError(Exception):
    def __init__(self, status: int, code: str):
        super().__init__(code)
        self.status, self.code = status, code


def _root(create=True) -> Path:
    root = configured_state_dir() / 'accounts'
    if create:
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(root, 0o700)
    return root


def reader_state_dir(account_id, *, create=True):
    if not isinstance(account_id, str) or not re.fullmatch(r'[a-f0-9]{32}', account_id):
        raise AccountError(400, 'invalid_account')
    path = _root() / 'readers' / account_id
    if create:
        if path.parent.is_symlink() or path.is_symlink():
            raise AccountError(503, 'reader_state_unavailable')
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(path.parent, 0o700)
        os.chmod(path, 0o700)
    return path


def reader_profile_path(account_id):
    path = reader_state_dir(account_id) / 'profile.json'
    if path.is_symlink():
        raise AccountError(503, 'reader_state_unavailable')
    return path


def reader_generated_dir(account_id, profile_id):
    if not isinstance(profile_id, str) or not re.fullmatch(r'[a-f0-9]{24}', profile_id):
        raise AccountError(400, 'invalid_profile')
    base = reader_state_dir(account_id)
    for path in (base / 'generated', base / 'generated' / profile_id):
        if path.is_symlink():
            raise AccountError(503, 'reader_state_unavailable')
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(path, 0o700)
    return base / 'generated' / profile_id


def _read(name, default):
    try:
        value = json.loads((_root() / name).read_text(encoding='utf-8'))
        return value if isinstance(value, type(default)) else default
    except (OSError, ValueError, TypeError):
        return default


def _write(name, value):
    root = _root()
    path = root / name
    temporary = path.with_name(f'.{path.name}.{os.getpid()}.{secrets.token_hex(4)}.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True), encoding='utf-8')
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)


def _name(value):
    if not isinstance(value, str):
        raise AccountError(400, 'invalid_account')
    value = unicodedata.normalize('NFKC', value).strip().casefold()
    if (not 3 <= len(value) <= 32 or
            any(c not in 'abcdefghijklmnopqrstuvwxyz0123456789._-' for c in value)):
        raise AccountError(400, 'invalid_account')
    return value


def _password_record(password, salt=None):
    if not isinstance(password, str) or not PASSWORD_MIN <= len(password) <= PASSWORD_MAX:
        raise AccountError(400, 'invalid_password')
    if password.casefold() in COMMON_PASSWORDS:
        raise AccountError(400, 'password_too_common')
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode('utf-8'), salt=salt,
                            n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P,
                            maxmem=128 * 1024 * 1024)
    return {'v': 1, 'salt': salt.hex(), 'hash': digest.hex(),
            'n': SCRYPT_N, 'r': SCRYPT_R, 'p': SCRYPT_P}


def _verify(password, record):
    try:
        if not isinstance(password, str) or len(password) > PASSWORD_MAX:
            return False, False
        salt = bytes.fromhex(record['salt'])
        expected = bytes.fromhex(record['hash'])
        params = (int(record['n']), int(record['r']), int(record['p']))
        if (params[0] < 2**12 or params[0] > 2**16 or params[0] & (params[0] - 1) or
                not 1 <= params[1] <= 16 or not 1 <= params[2] <= 4):
            return False, False
        actual = hashlib.scrypt(password.encode('utf-8'), salt=salt,
                                n=params[0], r=params[1], p=params[2],
                                maxmem=128 * 1024 * 1024)
        matches = hmac.compare_digest(actual, expected)
        return matches, (matches and (record.get('v') != 1 or params != (SCRYPT_N, SCRYPT_R, SCRYPT_P)))
    except (KeyError, TypeError, ValueError, AttributeError):
        return False, False


def _public(account):
    return {'id': account['id'], 'name': account['name']}


def _token_key(token):
    return hashlib.sha256(token.encode('utf-8')).hexdigest()


def _sweep_sessions(sessions, now):
    valid = {}
    for key, row in sessions.items():
        try:
            if (isinstance(key, str) and isinstance(row, dict) and
                    now < int(row.get('absolute_expires', 0)) and
                    now < int(row.get('idle_expires', 0))):
                valid[key] = row
        except (TypeError, ValueError, OverflowError):
            continue
    return valid


def _new_session(account_id):
    token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    now = int(time.time())
    sessions = _sweep_sessions(_read('sessions.json', {}), now)
    if len(sessions) >= MAX_SESSIONS:
        raise AccountError(503, 'sessions_temporarily_unavailable')
    sessions[_token_key(token)] = {
        'account_id': account_id, 'csrf': csrf, 'created': now,
        'last_seen': now, 'absolute_expires': now + SESSION_ABSOLUTE_SECONDS,
        'idle_expires': now + SESSION_IDLE_SECONDS,
    }
    _write('sessions.json', sessions)
    return token, csrf


def session(token):
    if not isinstance(token, str) or not token:
        return None
    digest = _token_key(token)
    with _lock:
        now = int(time.time())
        sessions = _sweep_sessions(_read('sessions.json', {}), now)
        matched_key = next((key for key in sessions if hmac.compare_digest(key, digest)), None)
        if matched_key is None:
            if len(sessions) != len(_read('sessions.json', {})):
                _write('sessions.json', sessions)
            return None
        row = sessions[matched_key]
        row['last_seen'] = now
        row['idle_expires'] = min(now + SESSION_IDLE_SECONDS, row['absolute_expires'])
        _write('sessions.json', sessions)
        accounts = _read('accounts.json', {})
        account = next((item for item in accounts.values()
                        if isinstance(item, dict) and hmac.compare_digest(str(item.get('id', '')), str(row['account_id']))), None)
        return (token, row, account) if isinstance(account, dict) else None


def _record_attempt(bucket, identity):
    now = time.monotonic()
    with _lock:
        for key in list(_attempts):
            _attempts[key] = [stamp for stamp in _attempts[key] if stamp > now - LOCKOUT_SECONDS]
            if not _attempts[key]:
                _attempts.pop(key)
        key = (bucket, identity)
        if len(_attempts.get(key, [])) >= (MAX_SIGNUP_FAILURES if bucket == 'signup' else MAX_LOGIN_FAILURES):
            return True
        _attempts.setdefault(key, []).append(now)
        return False


def _invite_digest(code):
    if not isinstance(code, str) or not code or len(code) > 256:
        return ''
    return hashlib.sha256(code.encode('utf-8')).hexdigest()


def signup(invite_code, account_name, password, ip='unknown'):
    if _record_attempt('signup', ip):
        raise AccountError(429, 'signup_temporarily_locked')
    with _lock:
        # Only a digest is configured/stored. Each invite has an expiry and one use.
        supplied = _invite_digest(invite_code)
        invites = _read('invites.json', {})
        if not invites:
            configured = os.environ.get('BRIEFING_INVITE_CODE_SHA256', '')
            if configured:
                invites = {configured: {'expires': int(time.time()) + 86400, 'remaining': 1}}
        now = int(time.time())
        matched = next((key for key in invites if hmac.compare_digest(str(key), supplied)), None)
        invite = invites.get(matched) if matched else None
        if (not matched or not isinstance(invite, dict) or int(invite.get('expires', 0)) <= now
                or int(invite.get('remaining', 0)) <= 0):
            raise AccountError(403, 'invalid_invite_code')
        name = _name(account_name)
        record = _password_record(password)
        accounts = _read('accounts.json', {})
        if name in accounts:
            raise AccountError(409, 'account_exists')
        account_id = secrets.token_hex(16)
        account = {'id': account_id, 'name': name, 'password': record, 'created': now}
        accounts[name] = account
        invite['remaining'] = int(invite['remaining']) - 1
        _write('accounts.json', accounts)
        _write('invites.json', invites)
        token, csrf = _new_session(account_id)
    return account, token, csrf


def login(account_name, password, ip):
    # Invalid and nonexistent names use the same public login failure.
    try:
        name = _name(account_name)
    except AccountError:
        name = 'invalid-account'
    if _record_attempt('login-ip', ip):
        raise AccountError(429, 'login_temporarily_locked')
    if _record_attempt('login-account', name):
        raise AccountError(429, 'login_temporarily_locked')
    accounts = _read('accounts.json', {})
    account = accounts.get(name)
    verified, rehash = _verify(password, account.get('password', {})) if isinstance(account, dict) else (False, False)
    if not verified:
        raise AccountError(401, 'invalid_credentials')
    with _lock:
        if rehash:
            account['password'] = _password_record(password)
            accounts[name] = account
            _write('accounts.json', accounts)
        old_sessions = _read('sessions.json', {})
        account_id = account['id']
        sessions = {key: row for key, row in old_sessions.items()
                    if not (isinstance(row, dict) and hmac.compare_digest(str(row.get('account_id', '')), account_id))}
        _write('sessions.json', sessions)
        token, csrf = _new_session(account_id)
    return account, token, csrf


def logout(token):
    with _lock:
        sessions = _read('sessions.json', {})
        digest = _token_key(token) if isinstance(token, str) else ''
        sessions = {key: row for key, row in sessions.items() if not hmac.compare_digest(key, digest)}
        _write('sessions.json', sessions)


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
        accounts = {key: value for key, value in accounts.items()
                    if not hmac.compare_digest(str(value.get('id', '')), str(account_id))}
        _write('accounts.json', accounts)
        sessions = _read('sessions.json', {})
        _write('sessions.json', {key: row for key, row in sessions.items()
                                 if not hmac.compare_digest(str(row.get('account_id', '')), str(account_id))})
        reader = _root() / 'readers' / account_id
        if reader.exists():
            shutil.rmtree(reader)


def cookie(token, *, secure=True, max_age=SESSION_ABSOLUTE_SECONDS):
    secure_flag = '; Secure' if secure else ''
    return f'{SESSION_COOKIE}={token}; Path=/; Max-Age={max_age}; HttpOnly; SameSite=Lax{secure_flag}'
