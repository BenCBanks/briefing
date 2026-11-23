#!/usr/bin/env python3
"""Format and print the count-only operational status from ``/api/health``."""

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import json
import os
from pathlib import Path
import sys
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

JOB_STATES = ('queued', 'running', 'done', 'failed', 'cancelled')
JOB_STAGES = ('queued', 'researching', 'reading', 'checking', 'writing', 'recording',
              'done', 'failed', 'cancelled')
AUDIO_STATES = ('disabled', 'pending', 'recording', 'recorded', 'failed',
                'regenerating', 'superseded')
DAILY_AUDIO_STATES = frozenset({'ready', 'recorded', 'pending', 'failed', 'disabled',
                                'regenerating', 'superseded', 'unavailable'})


def _count_map(value, keys):
    source = value if isinstance(value, dict) else {}
    return {key: max(0, source.get(key, 0)) if type(source.get(key, 0)) is int else 0
            for key in keys}


def _money(value):
    try:
        amount = Decimal(str(value))
        if not amount.is_finite() or amount < 0:
            return None
        return f'{amount:.6f}'
    except (InvalidOperation, ValueError, TypeError):
        return None


def _age_seconds(value, now):
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return max(0, int((now - parsed.astimezone(timezone.utc)).total_seconds()))


def _safe_timestamp(value):
    if not isinstance(value, str) or len(value) > 64:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z')


def build_health_payload(*, status, feeds, jobs, usage, daily_audio_status,
                         service_ok=True, now=None) -> dict:
    """Project component data onto a strict count-only health schema."""
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    now = now.astimezone(timezone.utc)
    status = status if isinstance(status, dict) else {}
    feeds = feeds if isinstance(feeds, dict) else None
    jobs = jobs if isinstance(jobs, dict) else None
    usage = usage if isinstance(usage, dict) else None

    run_ok = status.get('ok') if type(status.get('ok')) is bool else None
    last_run_at = _safe_timestamp(status.get('last_run_at'))
    daily_usage = usage.get('daily') if usage and isinstance(usage.get('daily'), dict) else {}
    monthly_usage = usage.get('monthly') if usage and isinstance(usage.get('monthly'), dict) else {}
    daily_audio = daily_audio_status if daily_audio_status in DAILY_AUDIO_STATES else 'unavailable'
    if daily_audio == 'recorded':
        daily_audio = 'ready'
    audio_counts = _count_map(jobs.get('by_audio_status') if jobs else None, AUDIO_STATES)
    return {
        'ok': service_ok is True,
        'daily_run_ok': run_ok,
        'last_run_at': last_run_at,
        'daily_run': {'ok': run_ok, 'last_run_at': last_run_at,
                      'age_seconds': _age_seconds(last_run_at, now)},
        'feeds': {'available': feeds is not None,
                  'enabled': _count_map(feeds, ('enabled',)).get('enabled', 0),
                  'backing_off': _count_map(feeds, ('backing_off',)).get('backing_off', 0),
                  'disabled_permanent': _count_map(feeds, ('disabled_permanent',)).get('disabled_permanent', 0)},
        'jobs': {'available': jobs is not None,
                 'by_state': _count_map(jobs.get('by_state') if jobs else None, JOB_STATES),
                 'by_stage': _count_map(jobs.get('by_stage') if jobs else None, JOB_STAGES)},
        'usage': {'available': usage is not None,
                  'today': {key: _money(daily_usage.get(source)) for key, source in (
                      ('estimated_cost_usd', 'estimated_cost_usd'), ('cap_usd', 'cap_usd'),
                      ('remaining_usd', 'remaining_usd'))},
                  'month': {key: _money(monthly_usage.get(source)) for key, source in (
                      ('estimated_cost_usd', 'estimated_cost_usd'), ('cap_usd', 'cap_usd'),
                      ('remaining_usd', 'remaining_usd'))}},
        'audio': {'daily_status': daily_audio, 'episodes_by_status': audio_counts},
    }


def _age_label(seconds):
    if type(seconds) is not int or seconds < 0:
        return 'age unavailable'
    if seconds < 60:
        return 'under 1m ago'
    minutes = seconds // 60
    if minutes < 60:
        return f'{minutes}m ago'
    hours, minutes = divmod(minutes, 60)
    if hours < 48:
        return f'{hours}h {minutes}m ago' if minutes else f'{hours}h ago'
    days, hours = divmod(hours, 24)
    return f'{days}d {hours}h ago' if hours else f'{days}d ago'


def _usage_text(period):
    if not isinstance(period, dict):
        return 'unavailable'
    fields = [_money(period.get(key)) for key in (
        'estimated_cost_usd', 'cap_usd', 'remaining_usd')]
    if any(value is None for value in fields):
        return 'unavailable'
    return f"${fields[0]} / ${fields[1]} cap (${fields[2]} left)"


def format_status_report(health) -> list[str]:
    """Return five plain, content-free lines suitable for a terminal summary."""
    if not isinstance(health, dict):
        health = {}
    daily = health.get('daily_run') if isinstance(health.get('daily_run'), dict) else {}
    run_ok = daily.get('ok')
    run_label = 'OK' if run_ok is True else 'FAIL' if run_ok is False else 'unavailable'
    run_age = _age_label(daily.get('age_seconds'))

    feeds = health.get('feeds') if isinstance(health.get('feeds'), dict) else {}
    if feeds.get('available') is True:
        feed_counts = _count_map(feeds, ('enabled', 'backing_off'))
        feed_line = f"Feeds: {feed_counts['enabled']} enabled · {feed_counts['backing_off']} backing off"
    else:
        feed_line = 'Feeds: unavailable'

    jobs = health.get('jobs') if isinstance(health.get('jobs'), dict) else {}
    states = jobs.get('by_state') if isinstance(jobs.get('by_state'), dict) else {}
    if jobs.get('available') is True:
        state_counts = _count_map(states, JOB_STATES)
        job_line = 'Jobs: ' + ' · '.join(f'{state_counts[key]} {key}' for key in JOB_STATES)
    else:
        job_line = 'Jobs: unavailable'

    usage = health.get('usage') if isinstance(health.get('usage'), dict) else {}
    if usage.get('available') is True:
        usage_line = f"Usage: today {_usage_text(usage.get('today'))} · month {_usage_text(usage.get('month'))}"
    else:
        usage_line = 'Usage: unavailable'

    audio = health.get('audio') if isinstance(health.get('audio'), dict) else {}
    if audio.get('daily_status') in DAILY_AUDIO_STATES:
        audio_counts = _count_map(audio.get('episodes_by_status'), AUDIO_STATES)
        episodes = ', '.join(f'{key} {value}' for key, value in audio_counts.items() if value)
        audio_line = f"Audio: daily {audio['daily_status']}"
        if episodes:
            audio_line += f" · episodes {episodes}"
    else:
        audio_line = 'Audio: unavailable'
    return [f'Daily run: {run_label} · {run_age}', feed_line, job_line, usage_line, audio_line]


def fetch_health(base_url, *, opener=urlopen):
    request = Request(base_url.rstrip('/') + '/api/health',
                      headers={'Accept': 'application/json', 'User-Agent': 'BriefingStatusReport/1.0'})
    try:
        with opener(request, timeout=8) as response:
            if response.status != 200:
                raise RuntimeError('health endpoint unavailable')
            value = json.loads(response.read().decode('utf-8'))
    except (HTTPError, URLError, OSError, TimeoutError, UnicodeDecodeError, json.JSONDecodeError):
        raise RuntimeError('health endpoint unavailable') from None
    if not isinstance(value, dict):
        raise RuntimeError('health response is invalid')
    return value


def main(argv=None, *, fetcher=fetch_health, output=print):
    args = list(sys.argv[1:] if argv is None else argv)
    base_url = args[0] if len(args) == 1 else os.environ.get('BRIEFING_URL')
    if not base_url or len(args) > 1:
        output('\n'.join(format_status_report(None)))
        return 2
    try:
        health = fetcher(base_url)
    except (RuntimeError, ValueError):
        health = None
    output('\n'.join(format_status_report(health)))
    return 0 if isinstance(health, dict) and health.get('ok') is True else 1


if __name__ == '__main__':
    raise SystemExit(main())
