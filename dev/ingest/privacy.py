"""Profile export and deletion helpers for local privacy controls."""

from contextlib import contextmanager
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from threading import Lock
from uuid import uuid4
import hmac
import json
import math
import os
import secrets
import shutil
import time

from . import generation_api
from .paths import STATE_DIR, checked_state_path, profile_identifier


LOCAL_STORAGE_PREFIXES = ('briefing:',)
SESSION_STORAGE_KEYS = ('briefing:build-error', 'briefing:topics-draft', 'briefing:library-filter')
SIGNAL_TYPES = {'finish', 'skip', 'save', 'sourceOpen', 'explainOpen',
                'replay', 'more', 'less', 'mute'}
MAX_LOCAL_SIGNALS_BYTES = 16_384
MAX_LOCAL_SIGNALS = 1_000
CONFIRMATION_TTL_SECONDS = 600
_TOKEN_LOCK = Lock()
_DELETE_LOCK = Lock()
_DELETE_TOKENS = {}


class PrivacyError(RuntimeError):
    def __init__(self, status, code, message):
        self.status = status
        self.code = code
        super().__init__(message)


@contextmanager
def profile_write_guard():
    """Serialize profile-scoped writes with deletion."""
    with _DELETE_LOCK:
        yield


def _read_json(path):
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, TypeError):
        return None


def _assert_safe_path(root, path):
    root = Path(root)
    path = Path(path)
    if root.is_symlink() or not path.is_relative_to(root):
        raise PrivacyError(500, 'profile_data_unavailable', 'Profile data is unavailable.')
    current = root
    for component in path.relative_to(root).parts:
        current = current / component
        if current.is_symlink():
            raise PrivacyError(500, 'profile_data_unavailable', 'Profile data is unavailable.')
        if current.exists() and not current.resolve().is_relative_to(root):
            raise PrivacyError(500, 'profile_data_unavailable', 'Profile data is unavailable.')


def _clean_profile(profile):
    try:
        generation_api.validate_profile(profile)
    except (ValueError, TypeError):
        raise PrivacyError(500, 'profile_unavailable', 'The saved profile is invalid.') from None
    top_fields = ('version', 'words', 'minutes', 'background')
    result = {key: profile[key] for key in top_fields if key in profile}
    result['topics'] = []
    for topic in profile.get('topics', []):
        if not isinstance(topic, dict):
            continue
        result['topics'].append({key: topic[key] for key in (
            'id', 'label', 'scope', 'include', 'exclude', 'depth', 'cadence',
            'mustFollow', 'sourcePhrase') if key in topic})
    result['muted'] = [dict(item) for item in profile.get('muted', [])
                       if isinstance(item, dict) and isinstance(item.get('label'), str)]
    return result


def parse_local_signals_header(raw):
    """Validate the browser's optional local-signal export header; never trust extra fields."""
    if raw is None:
        return None
    if not isinstance(raw, str) or len(raw.encode('utf-8')) > MAX_LOCAL_SIGNALS_BYTES:
        raise PrivacyError(413, 'local_signals_too_large', 'Local signals exceed the export limit.')
    try:
        rows = json.loads(raw)
    except (ValueError, TypeError):
        raise PrivacyError(400, 'invalid_local_signals', 'Local signals must be a JSON array.') from None
    return _clean_local_signals(rows)


def _clean_local_signals(rows):
    if not isinstance(rows, list) or len(rows) > MAX_LOCAL_SIGNALS:
        raise PrivacyError(400, 'invalid_local_signals', 'Local signals must be an array of at most 1,000 events.')
    clean = []
    for event in rows:
        timestamp = event.get('ts') if isinstance(event, dict) else None
        valid_timestamp = (type(timestamp) is int and abs(timestamp) <= 1_000_000_000_000_000) or (
            type(timestamp) is float and math.isfinite(timestamp) and abs(timestamp) <= 1_000_000_000_000_000)
        if (not isinstance(event, dict) or not isinstance(event.get('ts'), (int, float)) or
                not valid_timestamp or not isinstance(event.get('type'), str) or
                event['type'] not in SIGNAL_TYPES or
                not isinstance(event.get('storyId'), str) or not 1 <= len(event['storyId']) <= 100 or
                not isinstance(event.get('topicId'), str) or not 1 <= len(event['topicId']) <= 100):
            raise PrivacyError(400, 'invalid_local_signals', 'A local signal has an invalid event shape.')
        clean.append({'ts': event['ts'], 'type': event['type'],
                      'storyId': event['storyId'], 'topicId': event['topicId']})
    return clean


def _edition_metadata(history_path):
    value = _read_json(history_path)
    if value is None:
        return []
    if not isinstance(value, list):
        raise PrivacyError(500, 'history_unavailable', 'Edition history is invalid.')
    rows = []
    for bundle in value:
        if not isinstance(bundle, dict):
            continue
        editions = bundle.get('editions')
        edition_rows = editions.get('editions') if isinstance(editions, dict) else None
        if not isinstance(edition_rows, list):
            continue
        for edition in edition_rows:
            if not isinstance(edition, dict):
                continue
            metadata = {key: edition[key] for key in (
                'id', 'revision_id', 'script_hash', 'kind', 'local_date', 'generated_at',
                'title', 'duration_minutes') if key in edition}
            rows.append(metadata)
    return rows


def _episode_metadata(directory, profile_id):
    if directory.is_symlink():
        raise PrivacyError(500, 'episode_data_unavailable', 'Episode metadata is unavailable.')
    if not directory.is_dir():
        return []
    result = []
    for path in sorted(directory.glob('*.json')):
        if path.is_symlink() or not path.is_file():
            raise PrivacyError(500, 'episode_data_unavailable', 'Episode metadata is invalid.')
        job = _read_json(path)
        if not isinstance(job, dict) or job.get('profile_id') != profile_id:
            continue
        episode = job.get('episode') if isinstance(job.get('episode'), dict) else {}
        counts = job.get('counts') if isinstance(job.get('counts'), dict) else {}
        row = {key: job[key] for key in ('job_id', 'status', 'stage', 'created_at',
                                         'updated_at', 'finished_at', 'attempt') if key in job}
        row['counts'] = {key: counts[key] for key in ('chapters', 'sentences', 'sources') if key in counts}
        for key in ('title', 'topic', 'topic_label', 'made_date', 'duration_minutes'):
            if key in episode:
                row[key] = episode[key]
        if not row.get('title'):
            row['title'] = episode.get('topic_label') or episode.get('topic') or job.get('topic')
        result.append(row)
    return result


def _new_confirmation(profile_id, now=None):
    now = time.time() if now is None else now
    token = secrets.token_urlsafe(32)
    digest = sha256(token.encode('ascii')).hexdigest()
    with _TOKEN_LOCK:
        for key in list(_DELETE_TOKENS):
            if _DELETE_TOKENS[key]['expires_at'] <= now:
                del _DELETE_TOKENS[key]
        _DELETE_TOKENS[profile_id] = {
            'digest': digest, 'expires_at': now + CONFIRMATION_TTL_SECONDS,
        }
    return token


def _consume_confirmation(profile_id, token, now=None):
    now = time.time() if now is None else now
    if not isinstance(token, str) or not token:
        return False
    digest = sha256(token.encode('utf-8')).hexdigest()
    with _TOKEN_LOCK:
        stored = _DELETE_TOKENS.get(profile_id)
        if not stored or stored['expires_at'] <= now:
            _DELETE_TOKENS.pop(profile_id, None)
            return False
        if not hmac.compare_digest(digest, stored['digest']):
            return False
        del _DELETE_TOKENS[profile_id]
        return True


def export_profile(profile, *, state_dir=STATE_DIR, local_signals=None):
    clean_profile = _clean_profile(profile)
    clean_signals = _clean_local_signals(local_signals) if local_signals is not None else []
    profile_id = profile_identifier(profile)
    requested_state = checked_state_path(state_dir)
    if requested_state.is_symlink():
        raise PrivacyError(500, 'profile_data_unavailable', 'Profile data is unavailable.')
    state_dir = requested_state.resolve()
    generated = state_dir / 'generated' / 'profiles' / profile_id
    episode_dir = state_dir / 'episode-jobs' / 'profiles' / profile_id
    _assert_safe_path(state_dir, generated / 'editions-history.json')
    _assert_safe_path(state_dir, episode_dir)
    payload = {
        'format_version': 1,
        'exported_at': datetime.now(timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z'),
        'profile': clean_profile,
        'interests': {'words': clean_profile.get('words', ''),
                      'topics': clean_profile.get('topics', []),
                      'muted': clean_profile.get('muted', [])},
        'editions_history': _edition_metadata(generated / 'editions-history.json'),
        'episodes': _episode_metadata(episode_dir, profile_id),
        'local_signals': clean_signals,
        'local_signals_included': local_signals is not None,
    }
    return payload, _new_confirmation(profile_id)


def _owned_legacy_paths(state_dir, profile_id):
    generated = Path(state_dir) / 'generated'
    _assert_safe_path(state_dir, generated / 'legacy-profile-owner.json')
    marker = _read_json(generated / 'legacy-profile-owner.json')
    if not isinstance(marker, dict) or marker.get('profile_id') != profile_id:
        return []
    names = ('legacy-profile-owner.json', 'current.json', 'editions.json',
             'editions-history.json', 'sources.json', 'scorecard.json')
    return [generated / name for name in names if (generated / name).exists() or
            (generated / name).is_symlink()]


def _safe_targets(state_dir, profile, profile_id):
    requested_root = Path(state_dir).expanduser()
    if requested_root.is_symlink():
        raise PrivacyError(500, 'profile_data_unavailable', 'Profile data is unavailable.')
    root = requested_root.resolve()
    for directory in ('generated', 'episode-jobs', 'episodes', 'corrections', 'client-profiles'):
        _assert_safe_path(root, root / directory)
    targets = [root / 'profile.json', root / 'client-profiles',
               root / 'generated' / 'profiles' / profile_id,
               root / 'episode-jobs' / 'profiles' / profile_id,
               root / 'episodes' / 'profiles' / profile_id,
               root / 'corrections' / 'profiles' / profile_id]
    default_dir = root / 'generated' / 'default'
    _assert_safe_path(root, default_dir / 'current.json')
    current = _read_json(default_dir / 'current.json')
    editions = current.get('editions') if isinstance(current, dict) else None
    edition_rows = editions.get('editions') if isinstance(editions, dict) else None
    edition = next((row for row in edition_rows if isinstance(row, dict)), None) if isinstance(edition_rows, list) else None
    if isinstance(edition, dict) and edition.get('profile_id') == profile_id:
        targets.append(default_dir)
    targets.extend(_owned_legacy_paths(root, profile_id))
    safe = []
    for path in targets:
        current = path
        while current != root:
            if current.is_symlink():
                raise PrivacyError(500, 'profile_data_unavailable', 'Profile data is unavailable.')
            current = current.parent
        if not path.exists():
            continue
        resolved = path.resolve()
        if not resolved.is_relative_to(root) or path == root:
            raise PrivacyError(500, 'profile_data_unavailable', 'Profile data path is unsafe.')
        safe.append(path)
    return root, safe


def delete_profile(profile, confirmation_token, *, state_dir=STATE_DIR, now=None,
                   episode_jobs=None):
    _clean_profile(profile)
    profile_id = profile_identifier(profile)
    state_dir = checked_state_path(state_dir)

    # Serialize deletion with generation start and other delete requests.
    with _DELETE_LOCK, generation_api._profiles_lock, generation_api._jobs_lock:
        try:
            saved_profile = _read_json(state_dir / 'profile.json')
            if not isinstance(saved_profile, dict) or profile_identifier(saved_profile) != profile_id:
                raise PrivacyError(409, 'profile_changed', 'The active profile changed; export it again before deleting.')
        except (TypeError, ValueError):
            raise PrivacyError(409, 'profile_changed', 'The active profile changed; export it again before deleting.') from None
        if any(row.get('profile_id') == profile_id and row.get('status') in {'queued', 'running'}
               for row in generation_api._jobs.values() if isinstance(row, dict)):
            raise PrivacyError(409, 'profile_busy', 'A generation job is still running for this profile.')
        if _profile_busy_episodes(profile_id, state_dir):
            raise PrivacyError(409, 'profile_busy', 'An episode job is still running for this profile.')
        if not _consume_confirmation(profile_id, confirmation_token, now=now):
            raise PrivacyError(403, 'confirmation_required', 'A fresh profile export confirmation token is required.')
        root, targets = _safe_targets(state_dir, profile, profile_id)
        episode_dir = root / 'episode-jobs' / 'profiles' / profile_id
        episode_ids = []
        if episode_dir.is_dir():
            episode_ids = [path.stem for path in episode_dir.glob('*.json')
                           if path.is_file() and not path.is_symlink()]
        quarantine = root / f'.profile-delete-{uuid4().hex}'
        moved = []
        try:
            quarantine.mkdir(mode=0o700)
            for index, source in enumerate(targets):
                destination = quarantine / f'{index:04d}'
                os.replace(source, destination)
                moved.append((source, destination))
            shutil.rmtree(quarantine)
        except OSError:
            for source, destination in reversed(moved):
                try:
                    os.replace(destination, source)
                except OSError:
                    pass
            shutil.rmtree(quarantine, ignore_errors=True)
            raise PrivacyError(500, 'profile_delete_failed', 'Profile data could not be deleted safely.') from None

        for job_id, row in list(generation_api._jobs.items()):
            if isinstance(row, dict) and row.get('profile_id') == profile_id:
                del generation_api._jobs[job_id]
        if episode_jobs is not None:
            lock = getattr(episode_jobs, '_events_lock', None)
            if lock:
                with lock:
                    for job_id in episode_ids:
                        episode_jobs._events.pop(job_id, None)
            else:
                for job_id in episode_ids:
                    episode_jobs._events.pop(job_id, None)
        return {'deleted': True, 'local_browser_storage_cleared': False,
                'local_storage_prefixes_to_clear': list(LOCAL_STORAGE_PREFIXES),
                'session_storage_keys_to_clear': list(SESSION_STORAGE_KEYS),
                'local_signals_included_in_server_delete': False,
                'removed_server_paths': len(targets)}


def _profile_busy_episodes(profile_id, state_dir):
    root = Path(state_dir).expanduser().resolve()
    episode_dir = root / 'episode-jobs' / 'profiles' / profile_id
    _assert_safe_path(root, episode_dir)
    if not episode_dir.is_dir():
        return False
    for path in episode_dir.glob('*.json'):
        if path.is_symlink():
            raise PrivacyError(500, 'profile_data_unavailable', 'Profile data is unavailable.')
        job = _read_json(path)
        if isinstance(job, dict) and job.get('status') in {
                'queued', 'researching', 'reading', 'checking', 'writing', 'recording'}:
            return True
    return False
