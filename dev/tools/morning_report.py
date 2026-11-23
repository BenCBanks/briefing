#!/usr/bin/env python3
"""Print Ben's five-line, count-only morning edition and spend report."""

from __future__ import annotations

import argparse
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
import json
import os
from pathlib import Path
import re
import sys
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dev.ingest.usage import GLOBAL_LEDGER_DIR, PRICE_CONFIG
from dev.ingest.paths import STATE_DIR, checked_state_path

EASTERN = ZoneInfo('America/New_York')
DEFAULT_STATE = STATE_DIR


def _read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, TypeError):
        return None


def _dict(value):
    return value if isinstance(value, dict) else {}


def _decimal(value):
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None
    return number if number.is_finite() and number >= 0 else None


def _money(value):
    number = _decimal(value)
    return f'${number:.2f}' if number is not None else 'unavailable'


def _edition_rows(bundle):
    if not isinstance(bundle, dict):
        return []
    rows = bundle.get('editions')
    rows = rows.get('editions') if isinstance(rows, dict) else rows
    return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


def _load_edition(state_dir, expected_date):
    generated = Path(state_dir) / 'generated'
    candidates = [generated / 'default' / 'current.json', generated / 'default' / 'editions.json',
                  generated / 'current.json']
    candidates.extend(sorted(generated.glob('profiles/*/current.json')))
    found = []
    for path in candidates:
        bundle = _read_json(path)
        for edition in _edition_rows(bundle):
            if edition.get('kind') == 'daily':
                found.append((edition.get('local_date') == expected_date.isoformat(),
                              str(edition.get('generated_at') or ''), edition, path))
    if not found:
        return None, None
    found.sort(key=lambda row: (row[0], row[1]), reverse=True)
    _current, _created, edition, path = found[0]
    return edition, path


def _timestamp(value, now, tz=EASTERN):
    if not isinstance(value, str) or len(value) > 64:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    parsed = parsed.astimezone(tz)
    if parsed > now.astimezone(tz):
        return None
    return parsed


def _spoken_minutes(edition, status):
    brief = _dict(edition.get('daily_briefing'))
    run = _dict(_dict(status).get('daily_run'))
    run_brief = _dict(run.get('daily_briefing'))
    seconds = brief.get('duration_seconds', run_brief.get('duration_seconds'))
    if isinstance(seconds, (int, float)) and not isinstance(seconds, bool) and seconds >= 0:
        return float(seconds) / 60
    words = brief.get('spoken_words', run_brief.get('spoken_words'))
    if type(words) is int and words >= 0:
        return words / 150
    minutes = edition.get('duration_minutes')
    if isinstance(minutes, (int, float)) and not isinstance(minutes, bool) and minutes >= 0:
        return float(minutes)
    return None


def _spoken_words(edition, status):
    brief = _dict(edition.get('daily_briefing'))
    run_brief = _dict(_dict(_dict(status).get('daily_run')).get('daily_briefing'))
    value = brief.get('spoken_words', run_brief.get('spoken_words'))
    return value if type(value) is int and 0 <= value <= 10000 else None


def _narration_line(run):
    narration = _dict(run.get('daily_narration'))
    status = narration.get('status')
    if status not in {'written', 'fallback', 'off', 'unavailable'}:
        status = 'unavailable'
    reason = narration.get('reason')
    if not isinstance(reason, str) or not re.fullmatch(
            r'(?:narration_[a-z0-9_]{1,80}|budget_estimate|flag_disabled|'
            r'[A-Z][A-Za-z0-9]{0,39})', reason):
        reason = None
    if status == 'fallback' and reason is None:
        reason = 'narration_failed'
    state = status + (f' ({reason})' if reason and status in {'fallback', 'off'} else '')
    return state


def _story_count(edition):
    sections = edition.get('sections') if isinstance(edition, dict) else None
    if not isinstance(sections, list):
        return 0
    return sum(1 for row in sections if isinstance(row, dict) and
               (row.get('cluster_id') or row.get('kind', 'story') == 'story') and
               row.get('kind') not in {'intro', 'outro', 'quiet', 'coverage'})


def _audio_status(edition, status):
    audio = _dict(edition.get('audio'))
    daily = _dict(_dict(_dict(status).get('daily_run')).get('daily_audio'))
    raw = audio.get('audio_status', audio.get('status', daily.get('audio_status')))
    if raw in {'ready', 'recorded'}:
        return 'ready'
    if raw in {'disabled', 'pending', 'failed', 'unavailable', 'regenerating', 'superseded'}:
        return raw
    return 'unavailable'


def _ledger_spend(ledger_file, prices_file, now):
    ledger = _read_json(ledger_file)
    prices = _read_json(prices_file)
    if not isinstance(ledger, dict) or not isinstance(prices, dict):
        return None
    day_key = now.astimezone(EASTERN).date().isoformat()
    month_key = now.astimezone(EASTERN).strftime('%Y-%m')
    days, months = _dict(ledger.get('days')), _dict(ledger.get('months'))
    day = _dict(days.get(day_key))
    month = _dict(months.get(month_key))
    caps = _dict(prices.get('caps'))
    daily_spend, monthly_spend = _decimal(day.get('estimated_cost_usd')), _decimal(month.get('estimated_cost_usd'))
    daily_cap, monthly_cap = _decimal(caps.get('daily_usd')), _decimal(caps.get('monthly_usd'))
    if None in (daily_spend, monthly_spend, daily_cap, monthly_cap):
        return None
    return {'daily_spend': daily_spend, 'monthly_spend': monthly_spend,
            'daily_cap': daily_cap, 'monthly_cap': monthly_cap,
            'daily_remaining': max(Decimal(0), daily_cap - daily_spend),
            'monthly_remaining': max(Decimal(0), monthly_cap - monthly_spend)}


def _real_build_counts(status, state_dir, today, now):
    run = _dict(_dict(status).get('daily_run'))
    run_date = run.get('local_date')
    daily = int(run_date == today.isoformat() and
                run.get('status') in {'complete', 'ready', 'failed', 'timeout'})
    if run_date is None:
        last_run = _timestamp(_dict(status).get('last_run_at'), now)
        daily = int(last_run is not None and last_run.date() == today and
                    type(_dict(status).get('ok')) is bool)

    episodes = 0
    profiles = Path(state_dir) / 'episode-jobs' / 'profiles'
    try:
        paths = profiles.glob('*/*.json') if profiles.is_dir() and not profiles.is_symlink() else ()
        for path in paths:
            if path.is_symlink() or not path.is_file():
                continue
            job = _dict(_read_json(path))
            created = _timestamp(job.get('created_at'), now)
            estimate = _dict(job.get('cost_estimate'))
            started = _timestamp(job.get('started_at'), now)
            is_real = (estimate.get('kind') in {'estimate', 'real'} and started is not None) or \
                isinstance(job.get('cost_record'), dict)
            if created is not None and created.date() == today and is_real:
                episodes += 1
    except OSError:
        pass
    return daily, episodes


def summarize(status, edition, spend, *, state_dir, now=None):
    """Return exactly five plain-text lines; missing measures are explicitly unavailable."""
    now = now or datetime.now(EASTERN)
    now = now.replace(tzinfo=EASTERN) if now.tzinfo is None else now.astimezone(EASTERN)
    today = now.date()
    status = _dict(status)
    edition = _dict(edition)
    run = _dict(status.get('daily_run'))
    run_story_count = run.get('stories')
    count = (_story_count(edition) if edition else
             run_story_count if type(run_story_count) is int and 0 <= run_story_count <= 1000 else 0)
    created = _timestamp(edition.get('generated_at'), now)
    ready_stamp = (created.strftime('%H:%M ET') if created else 'time unavailable')
    ready = bool(edition and edition.get('local_date') == today.isoformat() and
                 (run.get('status') in {'complete', 'ready'} or status.get('ok') is True))
    edition_line = f'Edition: {"ready" if ready else "not ready"} at {ready_stamp}; {count} stories'

    minutes = _spoken_minutes(edition, status)
    audio_line = f'Audio: {_audio_status(edition, status)}'
    words = _spoken_words(edition, status)
    minutes_text = f'{minutes:.1f} min' if minutes is not None else 'minutes unavailable'
    words_text = f'{words} words' if words is not None else 'word count unavailable'
    narration_line = f'Narration: {_narration_line(run)}; {minutes_text} / {words_text}; audio {audio_line[7:]}'

    feeds = _dict(run.get('feeds'))
    failed_feeds = feeds.get('failed', status.get('feed_health_failed'))
    feed_line = (f'Feeds: {failed_feeds} failing' if type(failed_feeds) is int and failed_feeds >= 0
                 else 'Feeds: failure count unavailable')

    if spend is None:
        spend_line = 'Spend: today/month unavailable; remaining caps unavailable'
    else:
        spend_line = (f'Spend (estimate): today {_money(spend["daily_spend"])}/{_money(spend["daily_cap"])} '
                      f'({_money(spend["daily_remaining"])} left); month '
                      f'{_money(spend["monthly_spend"])}/{_money(spend["monthly_cap"])} '
                      f'({_money(spend["monthly_remaining"])} left)')

    daily_builds, episode_builds = _real_build_counts(status, state_dir, today, now)
    builds_line = f'Real builds run: daily {daily_builds}; episodes {episode_builds}'
    return [edition_line, narration_line, feed_line, spend_line, builds_line]


def build_report(*, state_dir=DEFAULT_STATE, ledger_file=None, prices_file=PRICE_CONFIG,
                 now=None, timezone_name='America/New_York'):
    timezone = ZoneInfo(timezone_name)
    now = now or datetime.now(timezone)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone)
    else:
        now = now.astimezone(timezone)
    state_dir = checked_state_path(state_dir)
    status = _read_json(state_dir / 'status.json')
    status = _dict(status)
    edition, _edition_path = _load_edition(state_dir, now.date())
    ledger_file = Path(ledger_file) if ledger_file else GLOBAL_LEDGER_DIR / 'ledger.json'
    spend = _ledger_spend(ledger_file, prices_file, now)
    return summarize(status, edition, spend, state_dir=state_dir, now=now)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state-dir', type=Path, default=DEFAULT_STATE)
    parser.add_argument('--ledger-file', type=Path, help='read-only ledger JSON override for fixtures')
    parser.add_argument('--prices-file', type=Path, default=PRICE_CONFIG)
    parser.add_argument('--now', help='timezone-aware ISO timestamp, for deterministic fixtures')
    args = parser.parse_args(argv)
    try:
        now = datetime.fromisoformat(args.now.replace('Z', '+00:00')) if args.now else None
        print('\n'.join(build_report(state_dir=args.state_dir, ledger_file=args.ledger_file,
                                    prices_file=args.prices_file, now=now)))
        return 0
    except (OSError, ValueError, ZoneInfoNotFoundError):
        # Runtime config errors should still yield five plain lines for cron logs.
        print('\n'.join(['Edition: not ready at time unavailable; 0 stories',
                         'Narration: unavailable; minutes unavailable / word count unavailable; audio unavailable',
                         'Feeds: failure count unavailable',
                         'Spend: today/month unavailable; remaining caps unavailable',
                         'Real builds run: daily 0; episodes 0']))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
