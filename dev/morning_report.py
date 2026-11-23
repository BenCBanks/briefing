#!/usr/bin/env python3
"""Print a six-line local report for the scheduled daily edition."""

import argparse
from datetime import date, datetime
import json
import os
from pathlib import Path
import re
import sys
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dev.ingest.paths import STATE_DIR

DEFAULT_TIMEZONE = os.environ.get('BRIEFING_TIMEZONE', 'America/New_York')


def _read_json(path):
    try:
        value = json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, TypeError):
        return None
    return value if isinstance(value, dict) else None


def _scheduled_edition(state_dir):
    generated = Path(state_dir) / 'generated'
    for path in (generated / 'default' / 'current.json',
                 generated / 'default' / 'editions.json', generated / 'current.json'):
        bundle = _read_json(path)
        if not bundle:
            continue
        editions = bundle.get('editions')
        if isinstance(editions, dict):
            rows = editions.get('editions')
        else:
            rows = editions
        if isinstance(rows, list):
            edition = next((row for row in rows if isinstance(row, dict) and row.get('kind') == 'daily'), None)
            if edition:
                sources = bundle.get('sources')
                if isinstance(sources, dict):
                    sources = sources.get('sources')
                return edition, sources if isinstance(sources, list) else []
    return None, []


def _story_count(edition):
    sections = edition.get('sections') if isinstance(edition, dict) else None
    return sum(isinstance(row, dict) and bool(row.get('cluster_id')) for row in sections or [])


def _depths(edition, sources):
    linked = set()
    for story in edition.get('sections', []) if isinstance(edition, dict) else []:
        source_ids = story.get('source_ids') if isinstance(story, dict) else None
        if isinstance(source_ids, list):
            linked.update(source_id for source_id in source_ids if isinstance(source_id, str))
    result = {}
    for source in sources:
        if not isinstance(source, dict) or source.get('id') not in linked:
            continue
        depth = source.get('evidence_depth')
        if not isinstance(depth, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,40}', depth):
            depth = 'unknown'
        result[depth] = result.get(depth, 0) + 1
    return result


def _safe_reason(value):
    if not isinstance(value, str):
        return None
    allowed = (
        r'generation_failed:(?:initialization|feed_fetch|edition_writer|scorecard|edition_publish):[A-Za-z][A-Za-z0-9]{0,39}',
        r'scorecard_critical_failed(?::[A-Za-z0-9_,.-]{1,120})?',
        r'(?:generation_timeout|generation_in_progress|gemini_call_limit|'
        r'ingestion_disabled_by_kill_switch|daily_generation_timeout|profile_missing_or_invalid|'
        r'feed_fetch_failures|generation_failed|invalid_profile)',
    )
    return value if any(re.fullmatch(pattern, value) for pattern in allowed) else 'generation_failed'



def summarize(state_dir=STATE_DIR, today=None, timezone_name=DEFAULT_TIMEZONE):
    timezone = ZoneInfo(timezone_name)
    expected = today or datetime.now(timezone).date()
    if isinstance(expected, str):
        expected = date.fromisoformat(expected)
    state_dir = Path(state_dir)
    status = _read_json(state_dir / 'status.json') or {}
    run = status.get('daily_run') if isinstance(status.get('daily_run'), dict) else {}
    edition, sources = _scheduled_edition(state_dir)
    story_count = _story_count(edition)
    edition_date = edition.get('local_date') if isinstance(edition, dict) else None
    current = edition_date == expected.isoformat()
    thin = bool(edition and (edition.get('thin_day') is True or 0 < story_count < 3))

    run_status = run.get('status') if isinstance(run.get('status'), str) else None
    last_run = status.get('last_run_at')
    run_line = f'Run: {run_status or ("successful" if status.get("ok") is True else "failed" if status.get("ok") is False else "unavailable")}'
    if isinstance(last_run, str):
        try:
            parsed = datetime.fromisoformat(last_run.replace('Z', '+00:00'))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone)
            run_line += f' at {parsed.astimezone(timezone).strftime("%H:%M %Z")}'
        except ValueError:
            pass

    if current:
        edition_line = f'Edition: {edition_date} current'
    else:
        edition_line = f'Edition: {edition_date or "missing"} (expected {expected.isoformat()})'
    count_state = 'thin day' if thin else 'ready' if current and story_count >= 3 else 'missing'
    stories_line = f'Stories: {story_count} / 3 ({count_state})'

    depths = _depths(edition, sources) if edition else {}
    depth_line = 'Evidence depth: ' + (', '.join(f'{name} {count}' for name, count in sorted(depths.items())) or 'unavailable')

    feeds = run.get('feeds') if isinstance(run.get('feeds'), dict) else {}
    attempted = feeds.get('attempted', status.get('feed_health_attempted', 0))
    failed = feeds.get('failed', status.get('feed_health_failed', 0))
    disabled = feeds.get('disabled')
    if disabled is None:
        disabled_rows = status.get('feeds_disabled')
        disabled = len(disabled_rows) if isinstance(disabled_rows, list) else 0
    feed_line = f'Feeds: {_count(attempted)} attempted, {_count(failed)} failed, {_count(disabled)} disabled'

    usage = run.get('usage') if isinstance(run.get('usage'), dict) else {}
    calls = _count(usage.get('gemini_calls', 0))
    elapsed = usage.get('elapsed_seconds')
    elapsed_text = f'{elapsed:.1f}s' if isinstance(elapsed, (int, float)) and not isinstance(elapsed, bool) and elapsed >= 0 else 'unknown duration'
    failures = _safe_reason(run.get('failure_reason') or status.get('reason'))
    if failures is None and _count(failed):
        failures = 'feed_fetch_failures'
    usage_line = (f'Usage: {calls} Gemini calls, {elapsed_text}; failures: {failures or "none"}')

    report_lines = [run_line, edition_line, stories_line, depth_line, feed_line, usage_line]
    run_failed = run_status in {'failed', 'timeout'}
    return report_lines, (0 if current and not thin and status.get('ok') is not False and not run_failed else 1)


def _count(value):
    return value if type(value) is int and value >= 0 else 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state-dir', type=Path, default=STATE_DIR,
                        help='State directory to inspect (defaults to the configured local state path).')
    parser.add_argument('--timezone', default=DEFAULT_TIMEZONE,
                        help='Reader timezone used to determine today (default: America/New_York).')
    parser.add_argument('--today', help='Override today as YYYY-MM-DD for fixture checks.')
    args = parser.parse_args(argv)
    try:
        lines, code = summarize(args.state_dir, args.today, args.timezone)
    except (ValueError, ZoneInfoNotFoundError) as error:
        print('\n'.join(['Run: unavailable', 'Edition: unavailable', 'Stories: 0 / 3 (missing)',
                         'Evidence depth: unavailable', 'Feeds: unavailable',
                         f'Usage: 0 Gemini calls, unknown duration; failures: invalid_report_config']))
        return 2
    print('\n'.join(lines))
    return code


if __name__ == '__main__':
    raise SystemExit(main())
