"""Shared private state locations for the briefing preview and timer jobs."""

import hashlib
import json
import os
import pwd
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4


_ACCOUNT_LIVE_STATE_DIR = (Path(pwd.getpwuid(os.getuid()).pw_dir) /
                           '.local/state/briefing').resolve()
LIVE_STATE_DIR = Path(os.environ.get('BRIEFING_LIVE_STATE_DIR') or
                      _ACCOUNT_LIVE_STATE_DIR).expanduser().resolve()


def checked_state_path(path):
    """Resolve a state path and refuse any access to live state in test mode."""
    candidate = Path(path).expanduser().resolve()
    if os.environ.get('BRIEFING_TESTING') == '1':
        configured = os.environ.get('BRIEFING_STATE_DIR')
        if not configured:
            raise RuntimeError('BRIEFING_STATE_DIR is required when BRIEFING_TESTING=1')
        roots = {_ACCOUNT_LIVE_STATE_DIR, LIVE_STATE_DIR}
        for live_root in roots:
            if (candidate == live_root or candidate in live_root.parents or
                    live_root in candidate.parents):
                raise RuntimeError(f'test state path resolves into live state: {candidate}')
        configured_root = Path(configured).expanduser().resolve()
        if any(configured_root == root or configured_root in root.parents or
               root in configured_root.parents for root in roots):
            raise RuntimeError('BRIEFING_STATE_DIR resolves into live state')
    return candidate


def configured_state_dir():
    configured = os.environ.get('BRIEFING_STATE_DIR')
    if os.environ.get('BRIEFING_TESTING') == '1' and not configured:
        raise RuntimeError('BRIEFING_STATE_DIR is required when BRIEFING_TESTING=1')
    return checked_state_path(configured or LIVE_STATE_DIR)


def configured_state_file(name):
    """Resolve a top-level state file dynamically, including test overrides."""
    if not isinstance(name, str) or Path(name).name != name or name in {'.', '..'}:
        raise ValueError('invalid state filename')
    return checked_state_path(configured_state_dir() / name)


STATE_DIR = configured_state_dir()


def profile_identifier(profile):
    identity = dict(profile) if isinstance(profile, dict) else profile
    if isinstance(identity, dict):
        # Behavior weights affect editorial allocation, not the reader's topic
        # identity or the location of their edition history and cached audio.
        identity.pop('behavior_weights', None)
    canonical = json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(canonical.encode('utf-8')).hexdigest()[:24]


def _legacy_owner_path(legacy_dir=None):
    legacy_dir = Path(legacy_dir) if legacy_dir is not None else STATE_DIR / 'generated'
    return legacy_dir / 'legacy-profile-owner.json'


def legacy_profile_owner(legacy_dir=None):
    try:
        value = json.loads(_legacy_owner_path(legacy_dir).read_text(encoding='utf-8'))
        return value.get('profile_id') if isinstance(value, dict) else None
    except (OSError, ValueError, TypeError):
        return None


def set_legacy_profile_owner(profile_id, legacy_dir=None):
    """Pin old single-profile files to their original profile before profile edits."""
    marker = _legacy_owner_path(legacy_dir)
    marker.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps({'profile_id': profile_id, 'marked_at': datetime.now(timezone.utc).isoformat()})
    temporary = marker.with_name(f'.{marker.name}.{os.getpid()}.tmp')
    try:
        temporary.write_text(payload + '\n', encoding='utf-8')
        os.chmod(temporary, 0o600)
        try:
            os.link(temporary, marker)
        except FileExistsError:
            pass
    finally:
        temporary.unlink(missing_ok=True)
    return legacy_profile_owner(legacy_dir)


def legacy_migration_allowed(profile, legacy_dir=None):
    """Legacy files may seed only the original saved profile, never a new one."""
    marker = _legacy_owner_path(legacy_dir)
    if not marker.is_file():
        return True
    return legacy_profile_owner(legacy_dir) == profile_identifier(profile)


def migrate_legacy_generated_files(legacy_dir=None, default_dir=None):
    """Copy the pre-profile generated edition into the default profile once.

    Files remain in the legacy directory as a recovery copy. Existing files in
    the new default directory always win, and hard-link creation avoids
    replacing a concurrently published default edition.
    """
    legacy_dir = Path(legacy_dir) if legacy_dir is not None else STATE_DIR / 'generated'
    default_dir = Path(default_dir) if default_dir is not None else legacy_dir / 'default'
    if legacy_dir.resolve() == default_dir.resolve():
        return []

    migrated = []
    for name in ('current.json', 'editions.json', 'editions-history.json', 'sources.json'):
        source = legacy_dir / name
        target = default_dir / name
        if target.exists() or not source.is_file():
            continue
        try:
            payload = source.read_bytes()
            json.loads(payload)
            default_dir.mkdir(parents=True, exist_ok=True)
            temporary = default_dir / f'.{name}.{os.getpid()}.{uuid4().hex}.tmp'
            temporary.write_bytes(payload)
            os.chmod(temporary, 0o600)
            try:
                os.link(temporary, target)
                migrated.append(name)
            except FileExistsError:
                pass
            finally:
                temporary.unlink(missing_ok=True)
        except (OSError, ValueError, TypeError):
            continue
    return migrated
