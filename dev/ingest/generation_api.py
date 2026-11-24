"""Single-user background generation jobs and private profile storage."""

from pathlib import Path
from threading import Lock, Thread
from uuid import uuid4
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from datetime import datetime, timedelta, timezone
import fcntl
import copy
import json
import logging
import os
import re
import sys
import time
import traceback

from . import core, feed_health
from .paths import (STATE_DIR, checked_state_path, configured_state_file,
                    migrate_legacy_generated_files, profile_identifier)
from .state_io import atomic_write_json
from .usage import reset_usage_caller, usage_caller
from .daily_monitor import project_daily_monitor

PROFILE_PATH = STATE_DIR / 'profile.json'
CLIENT_PROFILES_DIR = STATE_DIR / 'client-profiles'
GENERATED_DIR = STATE_DIR / 'generated'
LEGACY_GENERATED_DIR = GENERATED_DIR
DEFAULT_EDITION_DIR = GENERATED_DIR / 'default'
STATUS_PATH = STATE_DIR / 'status.json'
_STATUS_PATH_AT_IMPORT = STATUS_PATH
FEED_HEALTH_PATH = STATE_DIR / 'feed-health.json'
GENERATION_LOCK_PATH = STATE_DIR / 'generation.lock'
MAX_PROFILE_BYTES = 64_000
MAX_GENERATION_SECONDS = 180
GENERATION_FINALIZE_RESERVE_SECONDS = 10
FEED_FETCH_BUDGET_SECONDS = 60
ARTICLE_EXTRACTION_BUDGET_SECONDS = 45
MAX_GEMINI_CALLS_PER_RUN = 40
MAX_GEMINI_CALLS_WITH_FILL = 40
MAX_ARTICLE_FETCHES_PER_RUN = 24
CACHED_FEED_MAX_AGE = timedelta(hours=2)
# A reader's repeat window is fourteen days, not fourteen rebuilds. Retain
# enough revisions for repeated manual rebuilds without letting this private
# history grow without bound; the repeat matcher still applies its date cutoff.
EDITION_HISTORY_LIMIT = 120
CLIENT_PROFILE_RETENTION = timedelta(days=30)
MAX_KNOWN_CLIENT_PROFILES = 3
_jobs = {}
_jobs_lock = Lock()
_profiles_lock = Lock()
_active_job = None
_active_daily_run = None


def validate_profile(profile):
    if not isinstance(profile, dict) or not isinstance(profile.get('topics'), list) or len(profile['topics']) > 12:
        raise ValueError('invalid_profile')
    if isinstance(profile.get('minutes', 12), bool) or profile.get('minutes', 12) not in {10, 12, 15}:
        raise ValueError('invalid_profile')
    for topic in profile['topics']:
        if not isinstance(topic, dict):
            raise ValueError('invalid_profile')
        for field, limit in (('id', 80), ('label', 120), ('scope', 400), ('location', 120)):
            value = topic.get(field, '')
            if not isinstance(value, str) or len(value) > limit:
                raise ValueError('invalid_profile')
    local_sources = profile.get('local_sources', [])
    if (not isinstance(local_sources, list) or len(local_sources) > 40 or
            any(not isinstance(source_id, str) or not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,63}', source_id)
                for source_id in local_sources)):
        raise ValueError('invalid_profile')
    weights = profile.get('behavior_weights')
    if weights is not None:
        topic_ids = {topic['id'] for topic in profile['topics'] if isinstance(topic.get('id'), str)}
        if (not isinstance(weights, dict) or len(weights) > 12 or
                any(topic_id not in topic_ids or isinstance(weight, bool) or
                    not isinstance(weight, (int, float)) or not 0.5 <= weight <= 1.5
                    for topic_id, weight in weights.items())):
            raise ValueError('invalid_profile')
    encoded = json.dumps(profile, ensure_ascii=False).encode('utf-8')
    if len(encoded) > MAX_PROFILE_BYTES:
        raise ValueError('invalid_profile')
    return profile


def _atomic_json(path, value):
    # Guard at the final persistence boundary as callers may hold stale module
    # constants or pass an explicit test path after environment setup.
    path = checked_state_path(Path(path).expanduser())
    atomic_write_json(path, value)


def _status_file_path(path=None):
    if path is not None:
        return checked_state_path(path)
    if (os.environ.get('BRIEFING_TESTING') == '1' and
            STATUS_PATH == _STATUS_PATH_AT_IMPORT):
        return configured_state_file('status.json')
    return checked_state_path(STATUS_PATH)


def client_profiles_directory(path=None):
    if path is None:
        if CLIENT_PROFILES_DIR.is_symlink():
            return CLIENT_PROFILES_DIR
        return checked_state_path(CLIENT_PROFILES_DIR)
    requested = Path(path or PROFILE_PATH).expanduser()
    raw_directory = requested.parent / 'client-profiles'
    if raw_directory.is_symlink():
        return raw_directory
    return checked_state_path(requested.parent) / 'client-profiles'


def client_profile_path(profile, path=None):
    return client_profiles_directory(path) / f'{profile_identifier(profile)}.json'


def _parse_seen_at(value, fallback=None):
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00')) if isinstance(value, str) else fallback
    except ValueError:
        parsed = fallback
    if not isinstance(parsed, datetime):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _read_client_profile(path):
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
        if isinstance(value, dict) and isinstance(value.get('profile'), dict):
            profile = validate_profile(value['profile'])
            seen_at = _parse_seen_at(value.get('last_seen_at'), datetime.fromtimestamp(
                path.stat().st_mtime, timezone.utc))
            return profile, seen_at
        profile = validate_profile(value)
        return profile, datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
    except (OSError, ValueError, TypeError, OverflowError):
        return None, None


def touch_client_profile(profile, path=None, *, now=None, minimum_interval=timedelta(hours=1)):
    """Persist a profile snapshot and bounded last-seen timestamp locally."""
    profile = validate_profile(profile)
    now = now or datetime.now(timezone.utc)
    now = now.astimezone(timezone.utc)
    target = client_profile_path(profile, path)
    if target.parent.is_symlink():
        return target
    with _profiles_lock:
        try:
            active = load_profile(path)
        except (OSError, ValueError, TypeError):
            return target
        if profile_identifier(active) != profile_identifier(profile):
            return target
        previous, seen_at = _read_client_profile(target)
        if (previous == profile and profile_identifier(previous) == profile_identifier(profile) and
                seen_at is not None and now - seen_at < minimum_interval):
            return target
        _atomic_json(target, {'version': 1, 'profile_id': profile_identifier(profile),
                              'profile': profile, 'last_seen_at': now.isoformat().replace('+00:00', 'Z')})
    return target


def load_known_profiles(path=None, *, now=None, limit=MAX_KNOWN_CLIENT_PROFILES):
    """Return recent locally observed profiles, newest first, within the daily cap."""
    requested = Path(path or PROFILE_PATH).expanduser()
    directory = client_profiles_directory(requested)
    now = now or datetime.now(timezone.utc)
    now = now.astimezone(timezone.utc)
    cutoff = now - CLIENT_PROFILE_RETENTION
    found = {}
    active = None
    try:
        active = load_profile(requested)
        active_path = checked_state_path(requested.parent) / requested.name
        found[profile_identifier(active)] = (active, datetime.fromtimestamp(active_path.stat().st_mtime, timezone.utc))
    except (OSError, ValueError, TypeError, OverflowError):
        pass
    if not directory.is_symlink() and directory.is_dir():
        for candidate in directory.glob('*.json'):
            if candidate.is_symlink() or not candidate.is_file():
                continue
            profile, seen_at = _read_client_profile(candidate)
            if (profile is None or seen_at is None or candidate.stem != profile_identifier(profile) or
                    seen_at < cutoff):
                continue
            profile_id = profile_identifier(profile)
            prior = found.get(profile_id)
            if prior is None or seen_at >= prior[1]:
                found[profile_id] = (profile, seen_at)
    if active is not None:
        active_id = profile_identifier(active)
        active_seen = found.get(active_id, (active, cutoff - timedelta(seconds=1)))[1]
        found[active_id] = (active, active_seen)
    recent = [(profile, seen_at) for profile, seen_at in found.values() if seen_at >= cutoff]
    recent.sort(key=lambda row: row[1], reverse=True)
    bounded_limit = min(MAX_KNOWN_CLIENT_PROFILES, max(1, int(limit)))
    if active is not None:
        active_id = profile_identifier(active)
        active_index = next((index for index, (profile, _seen_at) in enumerate(recent)
                             if profile_identifier(profile) == active_id), None)
        if active_index is not None:
            recent.insert(0, recent.pop(active_index))
    return [profile for profile, _seen_at in recent[:bounded_limit]]


def save_profile(profile, path=PROFILE_PATH):
    requested = Path(path).expanduser()
    path = checked_state_path(requested.parent) / requested.name
    try:
        profile = validate_profile(profile)
    except ValueError:
        return 400, {'error': 'invalid_profile'}
    _atomic_json(path, profile)
    touch_client_profile(profile, path, minimum_interval=timedelta(0))
    return 200, {'saved': True}


def load_profile(path=None):
    path = path or PROFILE_PATH
    requested = Path(path).expanduser()
    path = checked_state_path(requested.parent) / requested.name
    profile = json.loads(Path(path).read_text(encoding='utf-8'))
    return validate_profile(profile)


def profile_response(path=None):
    try:
        profile = load_profile(path or PROFILE_PATH)
        touch_client_profile(profile, path or PROFILE_PATH)
    except FileNotFoundError:
        return 404, {'error': 'profile_not_found'}
    except (OSError, ValueError, TypeError):
        return 500, {'error': 'profile_unavailable'}
    return 200, {'profile': profile}


def _public_daily_run(value):
    """Return only the bounded run fields needed by the Today summary."""
    if not isinstance(value, dict):
        return None

    def count(field, maximum):
        raw = value.get(field)
        return raw if type(raw) is int and 0 <= raw <= maximum else None

    local_date = value.get('local_date')
    if not isinstance(local_date, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', local_date):
        local_date = None

    brief = value.get('daily_briefing') if isinstance(value.get('daily_briefing'), dict) else {}
    ladder = []
    allowed_rungs = {'feed', 'open_web', 'followups', 'background'}
    allowed_states = {'available', 'exhausted', 'not_configured', 'not_needed',
                      'limit_reached', 'target_met', 'unavailable', 'unknown'}
    rows = brief.get('supply_ladder')
    if isinstance(rows, list):
        for row in rows[:4]:
            if not isinstance(row, dict):
                continue
            rung = row.get('rung')
            if not isinstance(rung, str) or rung not in allowed_rungs:
                continue
            raw_state = row.get('status')
            state = raw_state if isinstance(raw_state, str) and raw_state in allowed_states else 'unknown'
            ladder.append({'rung': rung, 'status': state})

    audio = value.get('daily_audio') if isinstance(value.get('daily_audio'), dict) else {}
    audio_status = audio.get('audio_status')
    if not isinstance(audio_status, str) or audio_status not in {'ready', 'pending', 'failed', 'disabled', 'unavailable'}:
        audio_status = 'unavailable'
    ready_at = audio.get('ready_at')
    if not isinstance(ready_at, str) or not re.fullmatch(
            r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,6})?(?:Z|\+00:00)', ready_at):
        ready_at = None

    narration = value.get('daily_narration') if isinstance(value.get('daily_narration'), dict) else {}
    narration_status = narration.get('status')
    if narration_status not in {'written', 'fallback', 'off', 'unavailable'}:
        narration_status = 'unavailable'
    narration_reason = narration.get('reason')
    if not isinstance(narration_reason, str) or not re.fullmatch(
            r'(?:narration_[a-z0-9_]{1,80}|budget_estimate|flag_disabled|'
            r'[A-Z][A-Za-z0-9]{0,39})', narration_reason):
        narration_reason = None
    if narration_status == 'fallback' and narration_reason is None:
        narration_reason = 'narration_failed'

    profile_rows = []
    raw_profile_rows = value.get('profile_runs')
    if isinstance(raw_profile_rows, list):
        for row in raw_profile_rows[:MAX_KNOWN_CLIENT_PROFILES]:
            if not isinstance(row, dict):
                continue
            profile_id = row.get('profile_id')
            if not isinstance(profile_id, str) or not re.fullmatch(r'[a-f0-9]{24}', profile_id):
                continue
            reused = row.get('reused_from_profile_id')
            if not isinstance(reused, str) or not re.fullmatch(r'[a-f0-9]{24}', reused):
                reused = None
            raw_status = row.get('status')
            profile_rows.append({
                'profile_id': profile_id,
                'status': raw_status if isinstance(raw_status, str) and raw_status in {
                    'complete', 'failed', 'timeout'} else 'failed',
                'edition_id': row.get('edition_id')[:120] if isinstance(row.get('edition_id'), str) else None,
                'stories': count_brief(row.get('stories'), 1000),
                'gemini_calls': count_brief(row.get('gemini_calls'), 1000),
                'topic_count': count_brief(row.get('topic_count'), 12),
                'reused_from_profile_id': reused,
            })
    topic_supply = []
    raw_topic_supply = value.get('topic_supply')
    if isinstance(raw_topic_supply, list):
        for row in raw_topic_supply[:12]:
            if not isinstance(row, dict):
                continue
            topic_id = row.get('topic_id')
            if not isinstance(topic_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', topic_id):
                continue
            gap = row.get('gap')
            topic_supply.append({
                'topic_id': topic_id,
                'stories': count_brief(row.get('stories'), 100),
                'candidates': count_brief(row.get('candidates'), 1000),
                'state': row.get('state') if row.get('state') in {'covered', 'limited', 'quiet'} else None,
                'gap': gap[:240] if isinstance(gap, str) else None,
            })
    return {
        'local_date': local_date,
        'status': value.get('status') if isinstance(value.get('status'), str) and value.get('status') in {'complete', 'failed', 'timeout'} else None,
        'stories': count('stories', 1000),
        'thin_day': value.get('thin_day') if type(value.get('thin_day')) is bool else None,
        'topic_supply': topic_supply,
        'daily_briefing': {
            'target_minutes': count_brief(brief.get('target_minutes'), 60),
            'spoken_words': count_brief(brief.get('spoken_words'), 10000),
            'duration_seconds': count_brief(brief.get('duration_seconds'), 86400),
            'supply_ladder': ladder,
        },
        'daily_narration': {'status': narration_status, 'reason': narration_reason},
        'daily_audio': {'audio_status': audio_status, 'ready_at': ready_at},
        'profiles_attempted': count_brief(value.get('profiles_attempted'), MAX_KNOWN_CLIENT_PROFILES),
        'profiles_completed': count_brief(value.get('profiles_completed'), MAX_KNOWN_CLIENT_PROFILES),
        'profile_runs': profile_rows,
    }


def count_brief(raw, maximum):
    return raw if type(raw) is int and 0 <= raw <= maximum else None


def _last_good_edition_date(status_path):
    current_path = Path(status_path).parent / 'generated' / 'default' / 'current.json'
    try:
        bundle = json.loads(current_path.read_text(encoding='utf-8'))
    except (OSError, ValueError, TypeError):
        return None
    editions = bundle.get('editions') if isinstance(bundle, dict) else None
    rows = editions.get('editions') if isinstance(editions, dict) else None
    edition = rows[0] if isinstance(rows, list) and rows and isinstance(rows[0], dict) else None
    local_date = edition.get('local_date') if edition else None
    return local_date if isinstance(local_date, str) and re.fullmatch(r'\d{4}-\d{2}-\d{2}', local_date) else None


def _daily_monitor_active():
    with _jobs_lock:
        return ({'started_at': _active_daily_run.get('started_at')}
                if isinstance(_active_daily_run, dict) else None)


def status_response(path=None, *, now=None):
    path = path or STATUS_PATH
    try:
        value = json.loads(Path(path).read_text(encoding='utf-8'))
        if isinstance(value, dict):
            disabled = value.get('feeds_disabled', [])
            safe_disabled = []
            if isinstance(disabled, list):
                for row in disabled:
                    if not isinstance(row, dict):
                        continue
                    safe_disabled.append({key: str(row[key])[:240] for key in ('source_id', 'reason', 'retry_at', 'last_success_at', 'last_error')
                                          if isinstance(row.get(key), str)})
            last_run_at = value.get('last_run_at')
            reason = value.get('reason')
            payload = {
                'last_run_at': last_run_at if isinstance(last_run_at, str) else None,
                'ok': value.get('ok') if isinstance(value.get('ok'), bool) else None,
                'items_fetched': value.get('items_fetched', 0) if type(value.get('items_fetched', 0)) is int else 0,
                'stories_written': value.get('stories_written', 0) if type(value.get('stories_written', 0)) is int else 0,
                'feeds_disabled': safe_disabled,
                **_health_status_fields(value),
                'reason': reason[:240] if isinstance(reason, str) else None,
                'daily_run': _public_daily_run(value.get('daily_run')),
            }
            payload['daily_monitor'] = project_daily_monitor(
                value, last_good_edition_date=_last_good_edition_date(path),
                active_run=_daily_monitor_active(), now=now)
            return 200, payload
    except FileNotFoundError:
        pass
    except (OSError, ValueError, TypeError):
        return 500, {'error': 'status_unavailable'}
    payload = {'last_run_at': None, 'ok': None, 'items_fetched': 0, 'stories_written': 0,
               'feeds_disabled': [], 'reason': None, 'daily_run': None, **_health_status_fields({})}
    payload['daily_monitor'] = project_daily_monitor(
        {}, last_good_edition_date=_last_good_edition_date(path),
        active_run=_daily_monitor_active(), now=now)
    return 200, payload


def _health_status_fields(value):
    raw = value.get('feed_health_counts')
    raw = raw if isinstance(raw, dict) else {}
    return {'feed_health_counts': {key: max(0, raw[key]) if type(raw.get(key)) is int else 0
                                   for key in ('enabled', 'backing_off', 'disabled_permanent')},
            'feed_health_event': 'outage' if value.get('feed_health_event') == 'outage' else None,
            **{key: max(0, value[key]) if type(value.get(key)) is int else 0
               for key in ('feed_health_attempted', 'feed_health_failed')}}


def _run_status(ok, counts=None, stories=0, disabled=None, reason=None, path=None,
                repeat_suppressed=0, stale_suppressed=0, retrieval_fill=None,
                article_fetch_outcomes=None):
    from .retrieval_fill import safe_report
    path = _status_file_path(path)
    counts = counts or {}
    value = {'last_run_at': core.iso_date(), 'ok': bool(ok),
             'items_fetched': int(counts.get('parsed_items', 0)), 'stories_written': int(stories),
             'feeds_disabled': disabled or [], 'reason': reason,
             'repeat_suppressed': max(0, int(repeat_suppressed)),
             'retrieval_fill': safe_report(retrieval_fill),
             'article_fetch_by_host': _article_fetch_by_host(article_fetch_outcomes),
             'stale_suppressed': max(0, int(stale_suppressed)), **_health_status_fields(counts)}
    _atomic_json(path, value)
    return value


def _article_fetch_by_host(outcomes):
    """Compact extraction yield counters; never copy article text or URLs to status."""
    if not isinstance(outcomes, list):
        return {}
    methods = {'readability', 'semantic', 'paragraphs', 'publisher_selector',
               'jsonld_article_body', 'pdf', 'plain', 'rss_content_encoded',
               'atom_content', 'json_content_text', 'feed_full'}
    summary = {}
    for row in outcomes[:500]:
        if not isinstance(row, dict):
            continue
        host = row.get('host')
        if not isinstance(host, str):
            continue
        host = host.lower().rstrip('.')
        if not re.fullmatch(r'[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?', host) or '..' in host:
            continue
        counts = summary.setdefault(host, {'attempted': 0, 'succeeded': 0, 'methods': {}})
        counts['attempted'] += 1
        if row.get('reason') == 'ok':
            counts['succeeded'] += 1
            method = row.get('method')
            if isinstance(method, str) and method in methods:
                counts['methods'][method] = counts['methods'].get(method, 0) + 1
    return {host: summary[host] for host in sorted(summary)[:32]}


def _failure_reason(error, stage='initialization'):
    """Return a useful, bounded reason without serializing upstream exception text."""
    if isinstance(error, TimeoutError) or getattr(error, 'status', None) == 504:
        return 'generation_timeout'
    message = str(error)
    if message in {"can't start new thread", 'cannot start new thread', 'feed_worker_capacity'}:
        return 'generation_failed:thread_capacity:RuntimeError'
    if message == 'ingestion_disabled_by_kill_switch' or 'disabled by kill switch' in message.lower():
        return 'ingestion_disabled_by_kill_switch'
    if message.startswith('scorecard_critical_failed:'):
        failed = re.sub(r'[^a-zA-Z0-9_,.-]', '', message.partition(':')[2])[:120]
        return 'scorecard_critical_failed' + (':' + failed if failed else '')
    if message == 'scorecard_critical_failed':
        return message
    if message in {'gemini_call_limit', 'generation_in_progress', 'writer_disabled'}:
        return message
    # Provider and filesystem exceptions can include URLs or request details.
    # Keep the stage and exception class so the owner can diagnose failures without
    # copying provider messages, source text, paths, or key material into status.
    safe_stage = stage if stage in {'initialization', 'feed_fetch', 'edition_writer', 'scorecard', 'edition_publish'} else 'initialization'
    safe_type = type(error).__name__ if re.fullmatch(r'[A-Za-z][A-Za-z0-9]{0,39}', type(error).__name__) else 'UnknownError'
    return f'generation_failed:{safe_stage}:{safe_type}'


def _failure_message(reason):
    messages = {
        'ingestion_disabled_by_kill_switch': 'The daily briefing is temporarily paused. Please try again later.',
        'generation_in_progress': 'Your daily briefing is already being prepared.',
        'generation_timeout': 'The daily briefing took too long to prepare. Please try again later.',
        'gemini_call_limit': 'We could not finish the daily briefing just now. Please try again later.',
    }
    if reason.startswith('scorecard_critical_failed'):
        return 'Your daily briefing is not ready yet. Please try again later.'
    if reason == 'generation_failed:thread_capacity:RuntimeError':
        return 'The daily briefing could not start just now. Please try again later.'
    parts = reason.split(':')
    if len(parts) == 3 and parts[0] == 'generation_failed':
        return 'The daily briefing could not be prepared just now. Please try again later.'
    return messages.get(reason, 'The daily briefing could not be completed. Please try again later.')


def _safe_exception_detail(error):
    """Return only fixed diagnostics; exception text can contain profile/source data."""
    message = str(error)
    if message in {"can't start new thread", 'cannot start new thread',
                   'cannot schedule new futures after shutdown', 'feed_worker_capacity', 'ingestion_disabled_by_kill_switch',
                   'generation_in_progress', 'gemini_call_limit', 'generation_budget_expired'}:
        return message
    if message.startswith('scorecard_critical_failed:'):
        return _failure_reason(error)
    if isinstance(error, OSError) and getattr(error, 'errno', None) is not None:
        return f'OS error {error.errno}'
    return 'No safe diagnostic message available.'


def _log_generation_traceback(error, stage, job_id):
    """Write stack frames and a sanitized exception summary to stderr/journal."""
    safe_type = type(error).__name__ if re.fullmatch(r'[A-Za-z][A-Za-z0-9]{0,39}', type(error).__name__) else 'UnknownError'
    chain = []
    current = error
    seen = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        chain.append(current)
        current = current.__cause__ or (current.__context__ if not current.__suppress_context__ else None)
    print(f'briefing_generation_failure job={job_id} stage={stage} error_type={safe_type}', file=sys.stderr)
    for index, current in enumerate(chain):
        if index:
            print('Caused by:', file=sys.stderr)
        print('Traceback (most recent call last):', file=sys.stderr)
        if current.__traceback__ is not None:
            traceback.print_tb(current.__traceback__, file=sys.stderr)
        safe_detail = _safe_exception_detail(current)
        exception_type = type(current).__name__ if re.fullmatch(r'[A-Za-z][A-Za-z0-9]{0,39}', type(current).__name__) else 'UnknownError'
        print(f'{exception_type}: {safe_detail}', file=sys.stderr)
    sys.stderr.flush()


def _read_feed_health(path=None):
    path = path or FEED_HEALTH_PATH
    try:
        result = json.loads(Path(path).read_text(encoding='utf-8'))
        return result if isinstance(result, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def _ready_sources(registry, health, now=None):
    # Selecting a due probe must not mutate health: an outage can cancel the
    # entire batch, and only an actual success may reset a failure streak.
    now = now or core.utc_now()
    return [source for source in registry
            if source.get('enabled') is True and source.get('robots_ok') is True
            and not source.get('ai_bots_disallowed')
            and feed_health.status(health.get(source['id'], {}), now) == 'enabled']


def _update_feed_health(registry, health, source_results, source_stats=None, now=None, health_path=None,
                        diagnostics=None):
    health_path = health_path or FEED_HEALTH_PATH
    now = now or core.utc_now()
    source_stats = source_stats or {}
    attempted = {source['id']: source_results[source['id']] for source in registry
                 if feed_health.attempted(source_results.get(source['id']))}
    failures = sum(outcome not in feed_health.SUCCESS for outcome in attempted.values())
    outage = failures > len(attempted) / 2
    if diagnostics is not None:
        diagnostics.update(feed_health_event='outage' if outage else None,
                           feed_health_attempted=len(attempted), feed_health_failed=failures)
    if outage:
        # Keep *all* rows unchanged, including successes and weekly probes.
        # Actual fetching still checks robots; freezing health grants no access.
        print(f'feed_health outage: {failures}/{len(attempted)} feeds failed; health unchanged', file=sys.stderr)
    else:
        for source in registry:
            source_id = source['id']
            outcome = attempted.get(source_id)
            if outcome is None:
                continue
            stats = source_stats.get(source_id, {})
            feed_health.record(
                source_id, health, ok=outcome in feed_health.SUCCESS, outcome=outcome,
                new_items=bool(stats.get('new_item_count', 0)),
                duplicate_rate=float(stats.get('duplicate_rate', 0)),
                robots_ok=outcome != 'robots_disallowed',
                normal_interval_seconds=source.get('normal_interval_seconds', 86400), now=now)
        _atomic_json(health_path, health)
    return [{'source_id': source_id, 'reason': row.get('reason'), 'retry_at': row.get('disabled_until'),
             'last_success_at': row.get('last_success_at'), 'last_error': row.get('last_error')}
            for source_id, row in health.items() if isinstance(row, dict)
            and feed_health.status(row, now) != 'enabled']


def _set_job(job_id, **updates):
    with _jobs_lock:
        job = _jobs.get(job_id)
        if job is not None:
            job.update(updates)


def _daily_narration_job_status(report):
    narration = report.get('daily_narration') if isinstance(report, dict) else None
    if not isinstance(narration, dict):
        return {'status': 'off', 'reason': 'flag_disabled'}
    status = narration.get('status')
    if status not in {'written', 'fallback', 'off'}:
        return {'status': 'unavailable', 'reason': 'narration_status_unavailable'}
    reason = narration.get('reason')
    if not isinstance(reason, str) or not re.fullmatch(
            r'(?:narration_[a-z0-9_]{1,80}|budget_estimate|flag_disabled|'
            r'[A-Z][A-Za-z0-9]{0,39})', reason):
        reason = ('narration_failed' if status == 'fallback' else
                  'flag_disabled' if status == 'off' else None)
    return {'status': status, 'reason': reason}


def _safe_narration_exception_reason(error):
    message = str(error)
    if isinstance(error, NameError) and re.fullmatch(r"name '[A-Za-z_][A-Za-z0-9_]*' is not defined", message):
        return 'NameError:' + message.split("'")[1]
    if re.fullmatch(r'(?:narration_[a-z0-9_]{1,80}|budget_estimate)', message):
        return message
    error_type = type(error).__name__
    return error_type if re.fullmatch(r'[A-Z][A-Za-z0-9]{0,39}', error_type) else 'narration_failed'


def _publish_text_only_after_narration_failure(edition, report, narration_result):
    """Keep the checked written edition publishable when optional narration fails."""
    if isinstance(narration_result, dict) and narration_result.get('status') == 'written':
        return False
    reason = narration_result.get('reason') if isinstance(narration_result, dict) else None
    if not isinstance(reason, str) or not reason:
        reason = 'narration_unavailable'
    safe_status = _daily_narration_job_status(
        {'daily_narration': {'status': 'fallback', 'reason': reason}})
    reason = safe_status.get('reason') or 'narration_unavailable'
    report['daily_narration'] = {'status': 'fallback', 'reason': reason}
    daily = edition.setdefault('daily_briefing', {})
    if not isinstance(daily, dict):
        daily = edition['daily_briefing'] = {}
    daily['briefing_script'] = []
    daily['background_segments'] = []
    daily['spoken_words'] = 0
    daily['duration_seconds'] = 0
    daily['text_only'] = True
    audio = edition.get('audio') if isinstance(edition.get('audio'), dict) else {}
    audio['state'] = 'pending'
    audio['reason'] = 'no_spoken_stories'
    edition['audio'] = audio
    logging.getLogger(__name__).warning(
        'Daily narration unavailable; publishing the text edition only (%s).', reason)
    return True


def _persist_job_scorecard(directory, job_id, report):
    # Keep only the inspectable scorecard; never persist profile or evidence text.
    _atomic_json(Path(directory) / 'jobs' / f'{job_id}.scorecard.json', report)


_CONTENT_FAILURE_CODES = {
    'repeats': 'content_repeats',
    'freshness': 'content_freshness',
    'relevance': 'content_relevance',
    'citations': 'content_citations',
    'boilerplate': 'content_boilerplate',
    'quality': 'content_quality',
    'story count': 'content_story_count',
    'daily length': 'content_daily_length',
}


def _content_failure_codes(checks):
    """Return bounded internal codes for failed deterministic publication checks."""
    if not isinstance(checks, list) or not checks:
        return ['content_unavailable']
    failures = []
    for row in checks:
        if not isinstance(row, dict):
            failures.append('content_unavailable')
            continue
        if row.get('passed') is True:
            continue
        name = str(row.get('name') or '').casefold().strip()
        failures.append(_CONTENT_FAILURE_CODES.get(name, 'content_unknown'))
    return list(dict.fromkeys(failures))


def _record_content_gate(report, checks):
    """Attach one deterministic content review and merge any bounded failures."""
    report['content_checks'] = checks
    failures = _content_failure_codes(checks)
    if failures:
        existing = report.get('failed_critical')
        existing = existing if isinstance(existing, list) else []
        report['critical_failed'] = True
        report['failed_critical'] = list(dict.fromkeys([*existing, *failures]))
    return failures


def _hard_content_failures(failures):
    """Keep evidence/citation safety hard; editorial shape is publishable."""
    return [code for code in failures if code in {'content_citations', 'content_quality'}]


def _score_and_filter_stories(edition, sources, registry, evidence, score_edition_fn):
    story_rows = [row for row in edition.get('sections', []) if row.get('cluster_id')]
    passed = []
    outcomes = []
    for index, story in enumerate(story_rows, 1):
        candidate = dict(edition)
        candidate['sections'] = [story]
        report = score_edition_fn(candidate, sources, registry, evidence)
        failed = report.get('failed_critical', []) if report.get('critical_failed') else []
        if failed:
            outcomes.append({'story': index, 'status': 'dropped', 'failed_critical': failed,
                             'topic_ids': list(story.get('topic_ids', [])),
                             'checks': [{'number': check.get('number'), 'status': check.get('status'),
                                         'detail': check.get('detail')}
                                        for check in report.get('checks', []) if check.get('critical') and check.get('status') != 'PASS']})
        else:
            passed.append(story)
            outcomes.append({'story': index, 'status': 'passed', 'failed_critical': [], 'checks': [],
                             'topic_ids': list(story.get('topic_ids', []))})

    keep_clusters = {row.get('cluster_id') for row in passed}
    kept_sections = [row for row in edition.get('sections', [])
                     if not row.get('cluster_id') or row.get('cluster_id') in keep_clusters]
    referenced = {source_id for row in passed for source_id in row.get('source_ids', [])}
    kept_sources = [source for source in sources if source.get('id') in referenced]
    edition['sections'] = kept_sections
    retained_topic_counts = {}
    for row in passed:
        for topic_id in row.get('topic_ids', []):
            retained_topic_counts[topic_id] = retained_topic_counts.get(topic_id, 0) + 1
    for coverage in edition.get('coverage', []):
        if coverage.get('state') != 'covered':
            continue
        count = retained_topic_counts.get(coverage.get('topic_id'), 0)
        if count:
            candidate_count = coverage.get('candidate_count')
            if type(candidate_count) is int and candidate_count < 3:
                coverage.update(few_results=True,
                                reason=f'Few results today: {candidate_count} fresh candidate(s) produced {count} checked story or stories.')
            else:
                coverage.update(few_results=False, reason=f'{count} clustered story or stories included.')
        else:
            candidate_count = coverage.get('candidate_count')
            if type(candidate_count) is int and candidate_count:
                reason = f'Fresh items were found, but none of {candidate_count} candidate(s) passed the source and quality checks today.'
                coverage.update(state='limited', few_results=True, reason=reason)
            else:
                reason = 'No fresh source-linked story passed this topic’s quality checks.'
                coverage.update(state='limited', few_results=True, reason=reason)
    try:
        from .write_edition import _edition_dek
        edition['summary'] = _edition_dek(kept_sections)
    except (ImportError, AttributeError):
        pass
    if not passed:
        return kept_sources, outcomes

    # Removing a story changes narration and revision identity; invalidate any old audio.
    import hashlib
    script = '\n\n'.join(f"{row.get('heading', '')}\n{row.get('body', '')}" for row in kept_sections)
    edition['script_hash'] = hashlib.sha256(script.encode('utf-8')).hexdigest()
    seed = hashlib.sha256((edition['script_hash'] + str(edition.get('generated_at', ''))).encode()).hexdigest()[:8]
    revision = 'generated-' + str(edition.get('generated_at', '')).replace(':', '').replace('-', '') + '-' + seed
    edition['revision_id'] = revision
    edition['audio'] = {'state': 'pending', 'revision_id': revision, 'script_hash': edition['script_hash']}
    return kept_sources, outcomes


def _budgeted_article_fetcher(deadline, max_fetches=MAX_ARTICLE_FETCHES_PER_RUN, shared_context=None,
                              blocked_hosts=(), blocked_source_ids=()):
    """Use the registry and robots-checked transient fetcher within the writer budget."""
    calls = 0
    calls_lock = Lock()
    diagnostics = []
    blocked_hosts = {host.lower().rstrip('.') for host in blocked_hosts if isinstance(host, str)}
    blocked_source_ids = set(blocked_source_ids)
    article_cache = None
    cache_lock = None
    if isinstance(shared_context, dict):
        article_cache = shared_context.setdefault('article_cache', {})
        cache_lock = shared_context.setdefault('article_cache_lock', Lock())
        shared_context.setdefault('article_fetch_budget', {'calls': 0, 'limit': max_fetches})

    def cache_key_for(url):
        canonical = core.canonical_link(url)
        if not canonical:
            return str(url)
        parts = urlsplit(canonical)
        query = [(key, value) for key, value in parse_qsl(parts.query, keep_blank_values=True)
                 if not key.lower().startswith('utm_') and key.lower() not in {'fbclid', 'gclid', 'mc_cid', 'mc_eid'}]
        return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), ''))

    def fetch(url, source):
        nonlocal calls
        cache_key = cache_key_for(url)
        if article_cache is not None:
            with cache_lock:
                if cache_key in article_cache:
                    return article_cache[cache_key]
        try:
            host = (urlsplit(cache_key).hostname or '').lower().rstrip('.')
        except ValueError:
            host = ''
        if source.get('id') in blocked_source_ids or host in blocked_hosts:
            diagnostics.append({'source_id': source.get('id'), 'reason': 'feed_host_timeout'})
            return None
        if time.monotonic() >= deadline:
            diagnostics.append({'source_id': source.get('id'), 'reason': 'generation_deadline'})
            return None
        if article_cache is not None:
            with cache_lock:
                fetch_budget = shared_context['article_fetch_budget']
                if fetch_budget['calls'] >= fetch_budget['limit']:
                    diagnostics.append({'source_id': source.get('id'), 'reason': 'run_fetch_cap'})
                    return None
                fetch_budget['calls'] += 1
        else:
            with calls_lock:
                if calls >= max_fetches:
                    diagnostics.append({'source_id': source.get('id'), 'reason': 'run_fetch_cap'})
                    return None
                calls += 1
        try:
            value = core.fetch_article_text(url, source, diagnostics=diagnostics, deadline=deadline)
        except TypeError as error:
            # Keep small test fakes and older integrations that accept the original signature.
            if 'unexpected keyword' not in str(error):
                raise
            value = core.fetch_article_text(url, source)
        if article_cache is not None:
            with cache_lock:
                article_cache[cache_key] = value
        return value
    fetch.diagnostics = diagnostics
    return fetch


def _cached_article_plan(profile, items, registry, now=None):
    """Use a recent feed snapshot for one strong, fresh article per host.

    Only headlines fetched within two hours qualify; older feeds still refresh
    normally. A body is always fetched anew and never reused from disk.
    """
    from .write_edition import _canonical_profile, _rank_profile_topic_items, _matches_topic_scope, _tokens
    now = now or core.utc_now()
    by_id = {source['id']: source for source in registry}
    profile, topics, _ = _canonical_profile(profile)
    candidates = {}
    for topic in topics:
        terms = _tokens(' '.join([topic.get('label', ''), topic.get('scope', ''), *topic.get('include', [])]))
        for rank, item in enumerate(_rank_profile_topic_items(items, profile, topic)):
            source = by_id.get(item.get('source_id'))
            published = core.parse_date(item.get('published')) or core.parse_date(item.get('updated'))
            fetched = core.parse_date(item.get('fetched_at'))
            if (not source or source.get('enabled') is not True or source.get('robots_ok') is not True or
                    source.get('ai_bots_disallowed') or
                    source.get('article_access') not in {'full-text', 'transient-read'} or
                    source.get('paywalled') is True or not published or not fetched or
                    not now - timedelta(hours=72) <= published <= now or
                    not now - CACHED_FEED_MAX_AGE <= fetched <= now):
                continue
            text = item.get('title', '') + ' ' + item.get('snippet', '')
            if not _matches_topic_scope(topic, text, source):
                continue
            priority = (-len(terms & _tokens(text)), rank, -published.timestamp())
            host = (urlsplit(item.get('url', '')).hostname or '').lower()
            if host not in candidates or priority < candidates[host][0]:
                candidates[host] = (priority, item, source)
    return [(item, source) for _, item, source in sorted(candidates.values(), key=lambda row: row[0])[:8]]


def _store_edition(editions, sources, scorecard, directory=GENERATED_DIR):
    directory = checked_state_path(directory)
    current_path = directory / 'editions.json'
    source_path = directory / 'sources.json'
    current = None
    current_sources = None
    try:
        current = json.loads(current_path.read_text(encoding='utf-8'))
        current_sources = json.loads(source_path.read_text(encoding='utf-8'))
        if current.get('editions'):
            _atomic_json(directory / 'previous.json', current)
            _atomic_json(directory / 'previous-sources.json', current_sources)
    except (OSError, ValueError, TypeError, AttributeError):
        pass
    bundle = {'editions': editions, 'sources': sources, 'scorecard': scorecard}
    history_path = directory / 'editions-history.json'
    try:
        history = json.loads(history_path.read_text(encoding='utf-8'))
        history = history if isinstance(history, list) else []
    except (OSError, ValueError, TypeError):
        history = []
    entries = [bundle]
    if current and current_sources and current.get('editions'):
        entries.append({'editions': current, 'sources': current_sources,
                        'scorecard': json.loads((directory / 'scorecard.json').read_text(encoding='utf-8'))
                        if (directory / 'scorecard.json').is_file() else {}})
    entries.extend(history)
    unique = _merge_edition_history(entries, [])
    _atomic_json(history_path, unique)
    # A single atomic bundle is the authoritative API snapshot. Legacy files are
    # kept as exports for existing CLI tooling.
    _atomic_json(directory / 'current.json', bundle)
    _atomic_json(current_path, editions)
    _atomic_json(source_path, sources)
    _atomic_json(directory / 'scorecard.json', scorecard)


def profile_generated_dir(profile, root=None):
    root = checked_state_path(root if root is not None else GENERATED_DIR)
    return root / 'profiles' / profile_identifier(profile)


def mirror_scheduled_edition(source_profile, target_profile):
    """Copy one checked scheduled result to a profile with the same daily topics."""
    if profile_identifier(source_profile) == profile_identifier(target_profile):
        return False
    source = profile_generated_dir(source_profile)
    target = profile_generated_dir(target_profile)
    copied_current = False
    for name in ('editions.json', 'sources.json', 'scorecard.json', 'editions-history.json', 'current.json'):
        path = source / name
        try:
            payload = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError, TypeError):
            continue
        if name == 'editions-history.json':
            try:
                prior_history = json.loads((target / name).read_text(encoding='utf-8'))
            except (OSError, ValueError, TypeError):
                prior_history = []
            payload = _merge_edition_history(payload, prior_history)
        _set_bundle_profile_id(payload, profile_identifier(target_profile))
        _atomic_json(target / name, payload)
        copied_current = copied_current or name == 'current.json'
    if not copied_current:
        raise FileNotFoundError('scheduled edition bundle missing')
    return True


def _merge_edition_history(recent, existing):
    combined = []
    revisions = {}
    for bundle in (recent if isinstance(recent, list) else []) + (existing if isinstance(existing, list) else []):
        editions = bundle.get('editions') if isinstance(bundle, dict) else None
        rows = editions.get('editions') if isinstance(editions, dict) else []
        edition = next((row for row in rows if isinstance(row, dict)), None) if isinstance(rows, list) else None
        revision = edition.get('revision_id') if isinstance(edition, dict) else None
        if not isinstance(revision, str):
            continue
        if revision in revisions:
            prior = revisions[revision]
            if (edition.get('seen_by_reader') is True or edition.get('reader_seen_at')):
                for key in ('seen_by_reader', 'reader_seen_at'):
                    if edition.get(key) is not None and prior.get(key) is None:
                        prior[key] = edition[key]
            continue
        revisions[revision] = edition
        combined.append(bundle)
        if len(combined) >= EDITION_HISTORY_LIMIT:
            break
    return combined


def _set_bundle_profile_id(payload, profile_id):
    def update_editions(bundle):
        editions = bundle.get('editions') if isinstance(bundle, dict) else None
        rows = editions.get('editions') if isinstance(editions, dict) else editions
        if isinstance(rows, list):
            for row in rows:
                if isinstance(row, dict) and row.get('generated') is True:
                    row['profile_id'] = profile_id

    if isinstance(payload, dict):
        update_editions(payload)
    elif isinstance(payload, list):
        for bundle in payload:
            update_editions(bundle)


def mark_edition_seen(profile_id, edition_id, revision_id, *, now=None):
    """Persist that this reader's generated daily edition was displayed in Today."""
    if (not isinstance(profile_id, str) or not re.fullmatch(r'[a-f0-9]{24}', profile_id) or
            not isinstance(edition_id, str) or not edition_id.strip() or len(edition_id) > 120 or
            not isinstance(revision_id, str) or not revision_id.strip() or len(revision_id) > 160):
        return 400, {'error': 'invalid_seen_edition'}
    profile = next((row for row in load_known_profiles()
                    if profile_identifier(row) == profile_id), None)
    if profile is None:
        return 404, {'error': 'profile_not_found'}

    lock_path = checked_state_path(GENERATION_LOCK_PATH)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_stream = open(lock_path, 'a', encoding='utf-8')
    try:
        try:
            fcntl.flock(lock_stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 409, {'error': 'profile_busy'}
        directory = profile_generated_dir(profile)
        current_time = now or datetime.now(timezone.utc)
        if current_time.tzinfo is None:
            current_time = current_time.replace(tzinfo=timezone.utc)
        stamp = current_time.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z')
        matched = False
        changed_files = []
        for name in ('current.json', 'editions.json', 'editions-history.json'):
            path = directory / name
            try:
                value = json.loads(path.read_text(encoding='utf-8'))
            except (OSError, ValueError, TypeError):
                continue
            bundles = value if isinstance(value, list) else [value]
            file_changed = False
            for bundle in bundles:
                edition_data = bundle.get('editions') if isinstance(bundle, dict) else None
                rows = (edition_data.get('editions') if isinstance(edition_data, dict) else
                        edition_data if isinstance(edition_data, list) else [])
                for edition in rows if isinstance(rows, list) else []:
                    if (not isinstance(edition, dict) or edition.get('id') != edition_id or
                            edition.get('revision_id') != revision_id or edition.get('generated') is not True or
                            edition.get('kind') != 'daily' or
                            edition.get('profile_id') not in (None, profile_id)):
                        continue
                    matched = True
                    if edition.get('seen_by_reader') is not True or not edition.get('reader_seen_at'):
                        edition['seen_by_reader'] = True
                        edition['reader_seen_at'] = stamp
                        file_changed = True
            if file_changed:
                _atomic_json(path, value)
                changed_files.append(name)
        if not matched:
            return 404, {'error': 'edition_not_found'}
        return 200, {'seen': True, 'changed': bool(changed_files)}
    finally:
        try:
            fcntl.flock(lock_stream.fileno(), fcntl.LOCK_UN)
        finally:
            lock_stream.close()


def _run_job(job_id, profile, directory, generate=None, registry=None, is_default=False,
             publish_default=False, shared_context=None):
    # This coordinator only creates daily editions. Reader-triggered rebuilds
    # use the same protected daily budget as the scheduled run; on-demand
    # episodes have a separate job path and remain ordinary callers.
    usage_token = usage_caller('daily')
    started = time.monotonic()
    lock_stream = None
    counts = {}
    stories = 0
    disabled = []
    health = {}
    ready = []
    feed_health_recorded = False
    budget = {'calls': 0, 'limit': 0}
    stage = 'initialization'
    early_articles = None
    writer_deadline = started + MAX_GENERATION_SECONDS - GENERATION_FINALIZE_RESERVE_SECONDS
    article_deadline = min(writer_deadline,
                           started + FEED_FETCH_BUDGET_SECONDS + ARTICLE_EXTRACTION_BUDGET_SECONDS)
    try:
        from .flags import setting
        budget['limit'] = setting('gemini_calls_per_run')
        GENERATION_LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
        lock_stream = open(GENERATION_LOCK_PATH, 'a', encoding='utf-8')
        os.chmod(GENERATION_LOCK_PATH, 0o600)
        try:
            fcntl.flock(lock_stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RuntimeError('generation_in_progress') from error
        _set_job(job_id, status='running', progress={'stage': 'fetching', 'completed': 0, 'total': 0,
                                                      'message': 'Fetching 0 of 0 feeds'})
        def record_feed_health():
            nonlocal disabled, feed_health_recorded
            disabled = _update_feed_health(ready, health, counts.get('source_results', {}),
                                           counts.get('source_stats', {}), diagnostics=counts)
            counts['feed_health_counts'] = feed_health.counts(health, registry)
            feed_health_recorded = True

        shared_snapshot = (shared_context.get('feed_snapshot')
                           if isinstance(shared_context, dict) else None)
        if isinstance(shared_snapshot, dict):
            watch_results = shared_snapshot['watch_results']
            registry = shared_snapshot['registry']
            health = shared_snapshot['health']
            ready = shared_snapshot['ready']
            counts = copy.deepcopy(shared_snapshot['counts'])
            disabled = copy.deepcopy(shared_snapshot['disabled'])
            transient_feed_bodies = copy.deepcopy(shared_snapshot['transient_feed_bodies'])
            transient_feed_methods = copy.deepcopy(shared_snapshot['transient_feed_methods'])
            feed_health_recorded = True
        else:
            watch_results = []
            if registry is None:
                from .discover import run_weekly_watches
                watch_results = run_weekly_watches(local_source_ids=profile.get('local_sources'))
                registry = core.load_registry(local_source_ids=profile.get('local_sources'))
            health = _read_feed_health()
            ready = _ready_sources(registry, health)
            def feed_progress(done, total):
                _set_job(job_id, progress={'stage': 'fetching', 'completed': done, 'total': total,
                                           'message': f'Fetching {done} of {total} feeds'})

            stage = 'feed_fetch'
            counts = core.ingest_enabled(ready, core.STORE_PATH, budget_seconds=FEED_FETCH_BUDGET_SECONDS,
                                         progress=feed_progress, include_transient=True,
                                         max_workers=8)
            transient_feed_bodies = counts.pop('transient_feed_bodies', {})
            transient_feed_methods = counts.pop('transient_feed_methods', {})
            if counts.get('disabled_by_kill_switch'):
                raise RuntimeError('ingestion_disabled_by_kill_switch')
            if time.monotonic() - started >= MAX_GENERATION_SECONDS:
                raise TimeoutError('generation_budget_expired')
            if publish_default:
                migrate_legacy_generated_files(LEGACY_GENERATED_DIR, DEFAULT_EDITION_DIR)
            record_feed_health()
            if isinstance(shared_context, dict):
                shared_context['feed_snapshot'] = {
                    'watch_results': copy.deepcopy(watch_results), 'registry': copy.deepcopy(registry),
                    'health': copy.deepcopy(health), 'ready': copy.deepcopy(ready),
                    'counts': copy.deepcopy(counts), 'disabled': copy.deepcopy(disabled),
                    'transient_feed_bodies': copy.deepcopy(transient_feed_bodies),
                    'transient_feed_methods': copy.deepcopy(transient_feed_methods),
                }
        if time.monotonic() - started >= MAX_GENERATION_SECONDS:
            raise TimeoutError('generation_budget_expired')

        timed_out_feed_hosts = {
            (urlsplit(source.get('url', '')).hostname or '').lower().rstrip('.')
            for source in ready
            if counts.get('source_results', {}).get(source.get('id')) == 'timeout'
        }
        timed_out_feed_source_ids = {
            source.get('id') for source in ready
            if ((urlsplit(source.get('url', '')).hostname or '').lower().rstrip('.') in timed_out_feed_hosts or
                any(isinstance(host, str) and host.lower().rstrip('.') in timed_out_feed_hosts
                    for host in (source.get('allowed_domains') or [])))
        }
        if generate is None and not core.KILL_SWITCH_PATH.exists():
            prefetch_sources = [source for source in ready
                                if source.get('id') not in timed_out_feed_source_ids]
            early_articles = core.TransientArticlePrefetch(
                _cached_article_plan(profile, core.load_store(), prefetch_sources),
                _budgeted_article_fetcher(article_deadline, max_fetches=MAX_ARTICLE_FETCHES_PER_RUN,
                                          shared_context=shared_context,
                                          blocked_hosts=timed_out_feed_hosts,
                                          blocked_source_ids=timed_out_feed_source_ids))

        if generate is None:
            from .write_edition import generate_edition
            from .score_edition import score_edition
            stage = 'edition_writer'

            def story_progress(done, total):
                _set_job(job_id, progress={'stage': 'writing', 'completed': done, 'total': total,
                                           'message': f'Writing story candidate {done} of {total}'})

            call_budget_lock = Lock()

            def before_model_call():
                with call_budget_lock:
                    if budget['calls'] >= budget['limit']:
                        raise RuntimeError('gemini_call_limit')
                    budget['calls'] += 1

            def on_fill_start():
                with call_budget_lock:
                    budget['limit'] = min(MAX_GEMINI_CALLS_WITH_FILL, setting('gemini_calls_per_run'))

            from .flags import flag_enabled
            if not flag_enabled('writer'):
                raise RuntimeError('writer_disabled')
            transient_items = core.load_store()
            for item in transient_items:
                if early_articles is not None and early_articles.has_prefetch(item.get('url', '')):
                    item['prefetched_article'] = True
                transient_body = transient_feed_bodies.get((item.get('source_id'), item.get('url')))
                if transient_body:
                    item['feed_body'] = transient_body
                    item['feed_body_method'] = transient_feed_methods.get(
                        (item.get('source_id'), item.get('url')), 'feed_full')

            def score_candidate(section, candidate_sources, sentence_evidence):
                candidate_report = score_edition({'sections': [section]}, candidate_sources,
                                                 registry, sentence_evidence)
                linked_ids = set(section.get('source_ids', []))
                linked_sources = [row for row in candidate_sources if row.get('id') in linked_ids]
                failed_checks = [{'number': check.get('number'), 'detail': check.get('detail')}
                                 for check in candidate_report.get('checks', [])
                                 if check.get('critical') and check.get('status') != 'PASS']
                return {'passed': not candidate_report.get('critical_failed'),
                        'failed_critical': candidate_report.get('failed_critical', []),
                        'failed_checks': failed_checks,
                        'evidence_chars': sum(int(row.get('evidence_chars', 0)) for row in linked_sources),
                        'evidence_sentences': sum(int(row.get('source_sentence_count', 0))
                                                  for row in linked_sources)}

            previous = None
            from .repeats import load_history
            recent_history = load_history(Path(directory) / 'editions-history.json',
                                          limit=EDITION_HISTORY_LIMIT)
            previous_paths = [Path(directory) / 'current.json']
            if publish_default:
                previous_paths.insert(0, DEFAULT_EDITION_DIR / 'current.json')
            for previous_path in previous_paths:
                try:
                    old_bundle = json.loads(previous_path.read_text(encoding='utf-8'))
                    old_rows = old_bundle.get('editions', {}).get('editions', [])
                    previous = next((row for row in old_rows if row.get('kind') == 'daily'), None)
                    if previous is not None:
                        break
                except (OSError, ValueError, TypeError, AttributeError):
                    continue
            article_fetch = early_articles or _budgeted_article_fetcher(
                article_deadline, max_fetches=MAX_ARTICLE_FETCHES_PER_RUN,
                shared_context=shared_context, blocked_hosts=timed_out_feed_hosts,
                blocked_source_ids=timed_out_feed_source_ids)
            editions, sources, detail = generate_edition(profile, items=transient_items, registry=ready, max_stories=6,
                                                           progress=story_progress,
                                                           deadline=writer_deadline,
                                                           extraction_deadline=article_deadline,
                                                           article_fetcher=article_fetch,
                                                           before_model_call=before_model_call,
                                                           on_fill_start=on_fill_start,
                                                           story_scorer=score_candidate,
                                                           previous=previous,
                                                           recent_history=recent_history,
                                                           retrieval_fill_enabled=(is_default or
                                                                                   flag_enabled('retrieval_fill')),
                                                           search_first_enabled=flag_enabled('search_first'),
                                                           daily_word_target=750)
            from .evidence_packs import scorecard_evidence
            runtime_evidence = scorecard_evidence(detail)
            stage = 'scorecard'
            _set_job(job_id, progress={'stage': 'scoring', 'completed': 0, 'total': 1, 'message': 'Scoring edition'})
            edition = editions['editions'][0]
            sources['sources'], story_results = _score_and_filter_stories(
                edition, sources['sources'], registry, runtime_evidence, score_edition)
            stories = sum(bool(row.get('cluster_id')) for row in edition.get('sections', []))
            report = score_edition(edition, sources['sources'], registry,
                                   runtime_evidence)
            report['story_results'] = story_results
            report['stories_dropped'] = sum(row['status'] == 'dropped' for row in story_results)
            report['candidate_attempts'] = detail.get('candidate_attempts', 0)
            report['candidate_attempt_limit'] = detail.get('candidate_attempt_limit', 0)
            report['candidate_results'] = detail.get('candidate_results', [])
            report['candidate_pool_diagnostics'] = detail.get('candidate_pool_diagnostics', {})
            report['funnel_drop_reasons'] = detail.get('funnel_drop_reasons', {})
            selected_by_topic = {}
            for story in edition.get('sections', []):
                if not story.get('cluster_id'):
                    continue
                for topic_id in story.get('topic_ids', []):
                    selected_by_topic[topic_id] = selected_by_topic.get(topic_id, 0) + 1
            for topic_id, diagnostics in report['candidate_pool_diagnostics'].items():
                diagnostics['selected'] = selected_by_topic.get(topic_id, 0)
            for outcome in story_results:
                if outcome.get('status') != 'dropped':
                    continue
                for topic_id in outcome.get('topic_ids', []):
                    topic_reasons = report['funnel_drop_reasons'].setdefault(topic_id, {})
                    for check in outcome.get('failed_critical', []):
                        reason = f'scorecard:{check}'
                        topic_reasons[reason] = topic_reasons.get(reason, 0) + 1
            report['source_quality_rejections'] = detail.get('source_quality_rejections', [])
            report['source_selection'] = detail.get('source_selection', {})
            report['also_today_source_selection'] = detail.get('also_today_source_selection', {})
            report['repeat_suppressed'] = detail.get('repeat_suppressed', 0)
            report['stale_suppressed'] = detail.get('stale_suppressed', 0)
            report['retrieval_fill'] = detail.get('retrieval_fill', {})
            report['followup_count'] = detail.get('followup_count', 0)
            report['candidate_budget_exhausted'] = detail.get('budget_exhausted', False)
            report['article_fetch_outcomes'] = detail.get('article_fetch_outcomes',
                                                           getattr(article_fetch, 'diagnostics', []))
            report['evidence_depth_counts'] = detail.get('evidence_depth_counts', {})
            report['story_evidence_lengths'] = [
                {'section_id': section.get('id'),
                 'chars': sum(int(source.get('evidence_chars', 0)) for source in sources.get('sources', [])
                              if source.get('id') in section.get('source_ids', [])),
                 'sentences': sum(int(source.get('source_sentence_count', 0)) for source in sources.get('sources', [])
                                  if source.get('id') in section.get('source_ids', []))}
                for section in edition.get('sections', []) if section.get('cluster_id')]
            if 0 < stories < 3:
                edition['thin_day'] = True
            else:
                edition['thin_day'] = False
            edition.pop('availability_note', None)
            from . import daily_briefing
            daily_provider = daily_briefing.curated_background_provider(
                sources['sources'], registry=registry)
            source_dates = {row.get('id'): {'published_at': row.get('published_at') or row.get('published'),
                                            'source_class': row.get('source_class'),
                                            'kind': row.get('kind'),
                                            'citation_number': number}
                            for number, row in enumerate(sources['sources'], 1)
                            if isinstance(row, dict) and row.get('id')}
            source_tiers = daily_briefing.source_tier_map(sources['sources'])
            _persist_job_scorecard(directory, job_id, report)
            daily_briefing.assemble(
                edition, profile, background_fill=daily_provider,
                background_history=daily_briefing.background_history(recent_history),
                open_web_report=detail.get('retrieval_fill'),
                followup_count=detail.get('followup_count', 0), followups_checked=True,
                source_dates=source_dates, source_tiers=source_tiers)
            from .flags import flag_enabled
            # The scheduled run and narration-enabled reader rebuild both
            # publish the owner-facing daily. Apply the same deterministic
            # publication gate before spending on optional narration.
            if is_default or flag_enabled('daily_narration'):
                from dev.content_check import check_content
                content_checks = check_content(
                    {'editions': editions, 'sources': sources}, recent_history, profile,
                    now=core.utc_now())
                content_failures = _record_content_gate(report, content_checks)
                hard_failures = _hard_content_failures(content_failures)
                report['content_soft_warnings'] = [code for code in content_failures if code not in hard_failures]
                if hard_failures:
                    _persist_job_scorecard(directory, job_id, report)
                    raise RuntimeError('scorecard_critical_failed:' + ','.join(hard_failures))
            narration_result = None
            if flag_enabled('daily_narration'):
                try:
                    import stat
                    from dev.interpreter_proxy import KEY_FILE, call_gemini_json
                    key_path = KEY_FILE
                    if stat.S_IMODE(key_path.stat().st_mode) != 0o600:
                        raise ValueError('narration_key_permissions')
                    api_key = key_path.read_text(encoding='utf-8').strip()
                    if not api_key:
                        raise ValueError('narration_key_unavailable')

                    def narration_call_json(system_prompt, user_text, response_schema, _key, **kwargs):
                        before_model_call()
                        remaining = writer_deadline - time.monotonic()
                        if remaining <= 0:
                            raise TimeoutError('narration_deadline')
                        kwargs['timeout'] = min(float(kwargs.get('timeout', 20)), remaining)
                        return call_gemini_json(system_prompt, user_text, response_schema, api_key,
                                                model=kwargs['model'],
                                                max_output_tokens=kwargs['max_output_tokens'],
                                                timeout=kwargs['timeout'],
                                                thinking_level=kwargs.get('thinking_level'))

                    from .daily_narration import write_spoken_script
                    narration_result = write_spoken_script(
                        edition, profile, runtime_evidence, sources['sources'],
                        call_json=narration_call_json)
                except Exception as error:
                    # Keep the checked written edition if optional narration fails.
                    narration_result = {'status': 'fallback',
                                        'reason': _safe_narration_exception_reason(error)}
                if isinstance(narration_result, dict) and narration_result.get('status') == 'written':
                    report['daily_narration'] = narration_result
                    report['daily_briefing'] = {
                        key: edition['daily_briefing'][key]
                        for key in ('target_minutes', 'nominal_target_spoken_words', 'target_spoken_words',
                                    'maximum_spoken_words', 'spoken_words', 'duration_seconds',
                                    'supply_ladder', 'search_summary', 'topic_allocation')
                        if key in edition['daily_briefing']
                    }
                    final_content_checks = check_content(
                        {'editions': editions, 'sources': sources}, recent_history, profile,
                        now=core.utc_now())
                    final_content_failures = _record_content_gate(report, final_content_checks)
                    hard_failures = _hard_content_failures(final_content_failures)
                    report['content_soft_warnings'] = [code for code in final_content_failures
                                                       if code not in hard_failures]
                    if hard_failures:
                        _persist_job_scorecard(directory, job_id, report)
                        raise RuntimeError(
                            'scorecard_critical_failed:' + ','.join(hard_failures))
                else:
                    _publish_text_only_after_narration_failure(edition, report, narration_result)
            else:
                report['daily_narration'] = {'status': 'off', 'reason': 'flag_disabled'}
            report['daily_briefing'] = {
                key: edition['daily_briefing'][key]
                for key in ('target_minutes', 'nominal_target_spoken_words', 'target_spoken_words',
                            'maximum_spoken_words', 'spoken_words', 'duration_seconds',
                            'supply_ladder', 'search_summary', 'topic_allocation')
                if key in edition['daily_briefing']
            }
            report['thin_day'] = edition.get('thin_day') is True
            if stories < 1 and edition['daily_briefing']['spoken_words'] < edition['daily_briefing']['target_spoken_words']:
                failed = ','.join(str(value) for value in report.get('failed_critical', [])) or 'no_passing_stories'
                raise RuntimeError('scorecard_critical_failed:' + failed)
            if report.get('critical_failed'):
                failed = ','.join(str(value) for value in report.get('failed_critical', []))
                raise RuntimeError('scorecard_critical_failed' + (':' + failed if failed else ''))
            _persist_job_scorecard(directory, job_id, report)
        else:
            stage = 'edition_writer'
            editions, sources, report = generate(profile)
            stories = sum(bool(row.get('cluster_id')) for row in editions['editions'][0].get('sections', []))
            _persist_job_scorecard(directory, job_id, report)
            if isinstance(report, dict) and report.get('critical_failed'):
                failed = ','.join(str(value) for value in report.get('failed_critical', []))
                raise RuntimeError('scorecard_critical_failed' + (':' + failed if failed else ''))
        stage = 'edition_publish'
        if time.monotonic() - started >= MAX_GENERATION_SECONDS:
            raise TimeoutError('generation_budget_expired')
        edition = editions['editions'][0]
        edition['profile_id'] = profile_identifier(profile)
        _store_edition(editions, sources, report, directory)
        bundle = json.loads((Path(directory) / 'current.json').read_text(encoding='utf-8'))
        if publish_default:
            default_bundle = Path(directory)
            profile_directory = profile_generated_dir(profile)
            for name, value in (('current.json', bundle), ('editions.json', editions),
                                ('sources.json', sources), ('scorecard.json', report)):
                _atomic_json(profile_directory / name, value)
            history_path = default_bundle / 'editions-history.json'
            if history_path.is_file():
                _atomic_json(profile_directory / 'editions-history.json',
                             json.loads(history_path.read_text(encoding='utf-8')))
        status_value = _run_status(True, counts, stories, disabled,
                                   reason=f"{counts.get('errors', 0)} feed errors" if counts.get('errors') else None,
                                   repeat_suppressed=detail.get('repeat_suppressed', 0) if 'detail' in locals() else 0,
                                   stale_suppressed=detail.get('stale_suppressed', 0) if 'detail' in locals() else 0,
                                   retrieval_fill=detail.get('retrieval_fill') if 'detail' in locals() else None,
                                   article_fetch_outcomes=(detail.get('article_fetch_outcomes')
                                                           if isinstance(locals().get('detail'), dict) else
                                                           report.get('article_fetch_outcomes')
                                                           if isinstance(locals().get('report'), dict) else None))
        topic_story_counts = {}
        for section in editions['editions'][0].get('sections', []):
            if not section.get('cluster_id'):
                continue
            for topic_id in section.get('topic_ids', []):
                topic_story_counts[topic_id] = topic_story_counts.get(topic_id, 0) + 1
        _set_job(job_id, status='complete', progress={'stage': 'complete', 'completed': 1, 'total': 1,
                                                       'message': 'Edition ready'},
                 edition_id=editions['editions'][0].get('id'), feed_counts=counts,
                 stories_written=stories, elapsed_seconds=round(time.monotonic() - started, 2),
                 topic_story_counts=topic_story_counts,
                 profile_id=profile_identifier(profile),
                 edition_ready={'edition': edition, 'sources': sources.get('sources', [])},
                 scorecard_path=f'jobs/{job_id}.scorecard.json',
                 gemini_calls=budget['calls'], watched_pages=watch_results, run_status=status_value,
                 daily_narration=_daily_narration_job_status(report))
    except Exception as error:
        if counts and not feed_health_recorded:
            try:
                record_feed_health()
            except Exception:
                pass
        reason = _failure_reason(error, stage)
        code = reason.split(':', 1)[0]
        _log_generation_traceback(error, stage, job_id)
        status_value = _run_status(False, counts, stories, disabled, reason,
                                   repeat_suppressed=detail.get('repeat_suppressed', 0) if 'detail' in locals() else 0,
                                   stale_suppressed=detail.get('stale_suppressed', 0) if 'detail' in locals() else 0,
                                   retrieval_fill=detail.get('retrieval_fill') if 'detail' in locals() else None,
                                   article_fetch_outcomes=(detail.get('article_fetch_outcomes')
                                                           if isinstance(locals().get('detail'), dict) else
                                                           report.get('article_fetch_outcomes')
                                                           if isinstance(locals().get('report'), dict) else None))
        if 'report' in locals() and isinstance(report, dict):
            _persist_job_scorecard(directory, job_id, report)
        _set_job(job_id, status='failed', error=code, failure_reason=reason,
                 scorecard_path=f'jobs/{job_id}.scorecard.json' if (Path(directory) / 'jobs' / f'{job_id}.scorecard.json').is_file() else None,
                 progress={'stage': 'failed', 'completed': 0, 'total': 0, 'message': _failure_message(reason)},
                 elapsed_seconds=round(time.monotonic() - started, 2), gemini_calls=budget['calls'],
                 run_status=status_value, daily_narration=_daily_narration_job_status(locals().get('report')))
    finally:
        # Reset this worker's caller context.
        try:
            reset_usage_caller(usage_token)
        finally:
            try:
                if early_articles is not None:
                    early_articles.close()
            finally:
                if lock_stream is not None:
                    try:
                        fcntl.flock(lock_stream.fileno(), fcntl.LOCK_UN)
                    finally:
                        lock_stream.close()
                global _active_job, _active_daily_run
                with _jobs_lock:
                    if _active_job == job_id:
                        _active_job = None
                    if (isinstance(_active_daily_run, dict) and
                            _active_daily_run.get('job_id') == job_id):
                        _active_daily_run = None


def start_generation(profile, directory=None, generate=None, registry=None, is_default=False,
                    publish_default=None, shared_context=None):
    if directory is not None:
        directory = checked_state_path(directory)
    global _active_job, _active_daily_run
    try:
        profile = validate_profile(profile)
    except ValueError:
        return 400, {'error': 'invalid_profile'}
    with _jobs_lock:
        if _active_job:
            return 409, {'error': 'generation_in_progress', 'job_id': _active_job}
        job_id = uuid4().hex
        profile_id = profile_identifier(profile)
        publish_default = is_default if publish_default is None else bool(publish_default and is_default)
        output_directory = Path(directory) if directory is not None else (
            DEFAULT_EDITION_DIR if publish_default else profile_generated_dir(profile))
        _active_job = job_id
        _jobs[job_id] = {'job_id': job_id, 'status': 'queued', 'progress': {
            'stage': 'queued', 'completed': 0, 'total': 0, 'message': 'Generation queued'},
                        'profile_id': profile_id}
        if is_default:
            _active_daily_run = {'job_id': job_id,
                                 'started_at': datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')}
    thread = Thread(target=_run_job, args=(job_id, profile, output_directory, generate, registry, is_default,
                                           publish_default, shared_context),
                    name='briefing-generation', daemon=True)
    try:
        thread.start()
    except Exception:
        with _jobs_lock:
            if _active_job == job_id:
                _active_job = None
            if isinstance(_active_daily_run, dict) and _active_daily_run.get('job_id') == job_id:
                _active_daily_run = None
            _jobs.pop(job_id, None)
        raise
    return 202, {'job_id': job_id, 'status': 'queued'}


def start_scheduled_generation(profile, *, publish_default=True, shared_context=None):
    if publish_default:
        migrate_legacy_generated_files(LEGACY_GENERATED_DIR, DEFAULT_EDITION_DIR)
    return start_generation(profile, is_default=True, publish_default=publish_default,
                            shared_context=shared_context)


def job_response(job_id):
    with _jobs_lock:
        job = _jobs.get(job_id)
        if job is None:
            return 404, {'error': 'job_not_found'}
        return 200, dict(job)
