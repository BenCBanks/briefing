#!/usr/bin/env python3
"""Print a morning smoke summary and local feed-health summary."""

from collections import Counter
from datetime import date, datetime, time, timezone
import json
import os
import re
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from dev.acceptance_real_build import substantive_stories
from dev.smoke_live import run_checks
from dev.content_check import check_content, format_checks
from dev.ingest import feed_health
from dev.ingest.flags import effective_flags
from dev.tools.status_report import format_status_report

FEED_HEALTH_PATH = Path.home() / '.local/state/briefing/feed-health.json'
BACKUP_DIR = Path(os.environ.get('BRIEFING_DATA_DIR') or Path.home() / '.local/share/briefing') / 'backups'


def read_feed_health(path=FEED_HEALTH_PATH):
    try:
        value = json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, TypeError):
        return None
    return value if isinstance(value, dict) else None


def feed_health_lines(health):
    if not isinstance(health, dict):
        return ['Feed health: unavailable', 'Top failing sources: unavailable']
    rows = [(str(source_id), row) for source_id, row in health.items() if isinstance(row, dict)]
    summary = feed_health.counts(health)
    counts = []
    for source_id, row in rows:
        try:
            failures = max(0, int(row.get('consecutive_failures') or 0))
        except (TypeError, ValueError, OverflowError):
            failures = 0
        if failures:
            counts.append((failures, source_id))
    counts.sort(key=lambda item: (-item[0], item[1]))
    top = ', '.join(f'{source_id} ({failures})' for failures, source_id in counts[:5]) or 'none'
    return [f"Feed health: {summary['enabled']} enabled / {summary['backing_off']} backing_off / "
            f"{summary['disabled_permanent']} disabled_permanent ({len(rows)} tracked)",
            f'Top failing sources: {top}']


def last_backup_age_line(directory=BACKUP_DIR, now=None):
    """Report newest backup filename's mtime without opening archive contents."""
    root = Path(directory)
    try:
        candidates = [path for path in root.glob('briefing-state-*.tar.gz')
                      if not path.is_symlink() and path.is_file()
                      and re.fullmatch(r'briefing-state-\d{8}T\d{6}Z\.tar\.gz', path.name)]
        if not candidates:
            return 'Last backup age: unavailable'
        newest = max(candidates, key=lambda path: path.stat().st_mtime)
        modified = datetime.fromtimestamp(newest.stat().st_mtime, timezone.utc)
    except (OSError, ValueError, TypeError):
        return 'Last backup age: unavailable'
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    seconds = max(0, int((current.astimezone(timezone.utc) - modified).total_seconds()))
    days, remainder = divmod(seconds, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes = remainder // 60
    if days:
        age = f'{days}d {hours}h'
    elif hours:
        age = f'{hours}h {minutes}m'
    else:
        age = f'{minutes}m'
    return f'Last backup age: {age}'


def flag_summary_line(flags=None):
    flags = effective_flags() if flags is None else flags
    changed = {name: row for name, row in flags.items()
               if row.get('value') != row.get('default')}
    if not changed:
        return 'Non-default flags: none'
    summary = ', '.join(f'{name}={row["value"]} ({row["source"]})'
                        for name, row in sorted(changed.items()))
    return 'Non-default flags: ' + summary


def daily_audio_line(edition, expected_date, health=None, status=None):
    """Describe today's recorded daily audio, or why it is still pending."""
    audio = edition.get('audio') if isinstance(edition, dict) else None
    is_today = isinstance(edition, dict) and edition.get('local_date') == expected_date.isoformat()
    if not is_today:
        return "Audio: unavailable (today's edition unavailable)"
    if not isinstance(audio, dict):
        audio = {}
    daily_run = status.get('daily_run') if isinstance(status, dict) else {}
    status_audio = daily_run.get('daily_audio') if isinstance(daily_run, dict) else {}
    health_audio = health.get('audio') if isinstance(health, dict) else {}
    state = audio.get('audio_status') or (status_audio.get('audio_status')
        if isinstance(status_audio, dict) else None)
    if not state and isinstance(health_audio, dict):
        state = health_audio.get('daily_status')
    duration = audio.get('duration_seconds')
    if duration is None and isinstance(health_audio, dict):
        duration = health_audio.get('daily_duration_seconds')
    if state in {'ready', 'recorded'}:
        if isinstance(duration, (int, float)) and not isinstance(duration, bool) and duration >= 0:
            minutes = max(1, round(duration / 60))
            return f'Audio: recorded {minutes}m'
        return 'Audio: recorded (duration unavailable)'
    reason = audio.get('reason') or (status_audio.get('reason')
        if isinstance(status_audio, dict) else None)
    reason_labels = {
        'daily_budget': 'daily audio budget was used',
        'tts_disabled': 'audio is turned off',
        'no_spoken_stories': 'no spoken stories were available',
        'synthesis_failed': 'audio synthesis failed',
        'pending_human_listening': 'awaiting a listener check',
        'superseded': 'audio was replaced',
    }
    state_label = state if state in {'pending', 'failed', 'disabled', 'unavailable'} else 'unavailable'
    if state_label == 'unavailable' and not reason:
        return 'Audio: unavailable'
    plain_reason = reason_labels.get(reason)
    if not plain_reason and isinstance(reason, str) and re.fullmatch(r'[A-Za-z][A-Za-z0-9_ -]{0,80}', reason):
        plain_reason = re.sub(r'(?<=[a-z])(?=[A-Z])', ' ', reason).replace('_', ' ').lower()
    return f'Audio: {state_label} ({plain_reason or "reason unavailable"})'


def get_json(url):
    request = Request(url, headers={'Accept': 'application/json', 'User-Agent': 'BriefingMorningCheck/1.0'})
    try:
        with urlopen(request, timeout=10) as response:
            status, raw = response.status, response.read()
    except HTTPError as error:
        status, raw = error.code, error.read()
    except (URLError, TimeoutError, OSError) as error:
        raise RuntimeError(str(error)) from error
    if status != 200:
        raise RuntimeError(f'HTTP {status}')
    try:
        value = json.loads(raw.decode('utf-8'))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RuntimeError('invalid JSON response') from error
    if not isinstance(value, dict):
        raise RuntimeError('response is not a JSON object')
    return value


def summarize(base_url, smoke_runner=run_checks, json_fetch=get_json, today=None,
              health_reader=read_feed_health, backup_age_reader=last_backup_age_line,
              flags_reader=effective_flags, now=None):
    base = base_url.rstrip('/')
    eastern = ZoneInfo('America/New_York')
    current_time = now or datetime.now(eastern)
    if current_time.tzinfo is None:
        current_time = current_time.replace(tzinfo=eastern)
    else:
        current_time = current_time.astimezone(eastern)
    expected = today or current_time.date()
    if isinstance(expected, str):
        expected = date.fromisoformat(expected)
    date_deadline_passed = current_time >= datetime.combine(expected, time(6, 30), tzinfo=eastern)
    try:
        checks = smoke_runner(base)
        smoke_failures = [name for name, passed, _detail in checks
                          if not passed and (name != 'freshness' or date_deadline_passed)]
    except (ValueError, RuntimeError) as error:
        checks = []
        smoke_failures = [str(error)]
    payload = {}
    try:
        payload = json_fetch(base + '/api/edition?profile=active')
        edition = payload.get('edition') if payload.get('generated') is True else None
        sources = payload.get('sources') if isinstance(payload.get('sources'), list) else []
    except (RuntimeError, ValueError) as error:
        edition, sources = None, []
        smoke_failures.append(f'active edition unavailable: {error}')
    try:
        status = json_fetch(base + '/api/status')
    except (RuntimeError, ValueError) as error:
        status = {}
        smoke_failures.append(f'status unavailable: {error}')
    daily_run = status.get('daily_run') if isinstance(status.get('daily_run'), dict) else {}
    topic_supply = daily_run.get('topic_supply') if isinstance(daily_run.get('topic_supply'), list) else []
    topic_gaps = [row for row in topic_supply if isinstance(row, dict) and row.get('gap')]
    supply_line = ('Topic supply gaps: ' + '; '.join(
        str(row['gap'])[:240] for row in topic_gaps[:12])) if topic_gaps else 'Topic supply gaps: none'
    narration = daily_run.get('daily_narration') if isinstance(daily_run.get('daily_narration'), dict) else {}
    if narration.get('status') == 'fallback':
        smoke_failures.append('daily narration fallback')
    try:
        health = json_fetch(base + '/api/health')
    except (RuntimeError, ValueError):
        health = None
        # The detailed smoke/status checks remain authoritative; health is an
        # additive operational summary and must not mask their useful output.

    if isinstance(edition, dict):
        stories, _assessments = substantive_stories(edition, sources)
        story_line = f'Stories: {len(stories)} substantive / 3 required'
        source_by_id = {source.get('id'): source for source in sources if isinstance(source, dict)}
        linked_ids = {source_id for story in stories for source_id in story.get('source_ids', [])}
        depths = Counter((source_by_id.get(source_id, {}).get('evidence_depth') or 'unknown')
                         for source_id in linked_ids)
        depth_line = 'Depth: ' + (', '.join(f'{name} {count}' for name, count in sorted(depths.items())) or 'none')
        date_value = edition.get('local_date')
        try:
            edition_date = date.fromisoformat(date_value)
            date_line = f'Date: {edition_date.isoformat()} ' + (
                'current' if edition_date == expected else 'stale' if date_deadline_passed else 'not due by 06:30 ET')
            if edition_date != expected and date_deadline_passed:
                smoke_failures.append('freshness')
        except (TypeError, ValueError):
            date_line = f'Date: unavailable ({date_value or "missing"})'
            if date_deadline_passed:
                smoke_failures.append('invalid edition date')
    else:
        story_line = 'Stories: unavailable'
        depth_line = 'Depth: unavailable'
        date_line = 'Date: unavailable'
        if date_deadline_passed:
            smoke_failures.append('freshness')

    try:
        history = payload.get('history')
        if history is None:
            history = json_fetch(base + '/api/edition?profile=active&history=1').get('history')
        profile = json_fetch(base + '/api/profile')
        content = check_content(payload, history, profile)
        content_lines = ['Content: ' + line for line in format_checks(content)]
        if any(not row['passed'] for row in content):
            smoke_failures.append('content checks')
    except (RuntimeError, ValueError, TypeError, KeyError) as error:
        content_lines = [f'Content: FAIL unavailable ({error})']
        smoke_failures.append('content checks unavailable')

    disabled = status.get('feeds_disabled') if isinstance(status.get('feeds_disabled'), list) else None
    disabled_line = f'Feeds disabled: {len(disabled)}' if disabled is not None else 'Feeds disabled: unavailable'
    # Keep the health endpoint as the shared, allowlisted operational report.
    # The local feed-health detail below is intentionally limited to aggregate
    # counts and failure totals; source labels stay in this existing private
    # morning report only.
    health_lines = format_status_report(health)
    health_lines[-1] = daily_audio_line(edition, expected, health, status)
    local_feed_lines = feed_health_lines(health_reader())
    if status.get('feed_health_event') == 'outage':
        local_feed_lines[0] += '; outage (health unchanged)'
    api_ok = status.get('ok') is True
    passed = api_ok and not smoke_failures
    failure_detail = ', '.join(dict.fromkeys(smoke_failures))
    status_line = f'Status: {"OK" if passed else "FAIL"}'
    if not api_ok:
        status_line += f' (API ok={status.get("ok", "unavailable")})'
    elif failure_detail:
        status_line += f' ({failure_detail})'
    if len(checks):
        status_line += f'; smoke {sum(bool(row[1]) for row in checks)}/{len(checks)}'
    try:
        flags_line = flag_summary_line(flags_reader())
    except (OSError, ValueError, TypeError):
        flags_line = 'Non-default flags: unavailable'
    return [story_line, depth_line, status_line, date_line, backup_age_reader(), flags_line, disabled_line, supply_line,
            *health_lines, *local_feed_lines, *content_lines], (0 if passed else 1)


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    base = args[0] if args else os.environ.get('BRIEFING_LIVE_URL') or os.environ.get('BRIEFING_URL')
    if len(args) > 1 or not base:
        print('\n'.join(['Stories: unavailable', 'Depth: unavailable',
                         'Status: FAIL (provide a live URL or set BRIEFING_LIVE_URL)',
                         'Date: unavailable', 'Last backup age: unavailable',
                         flag_summary_line(), 'Feeds disabled: unavailable',
                         'Daily run: unavailable · age unavailable', 'Feeds: unavailable',
                         'Jobs: unavailable', 'Usage: unavailable', 'Audio: unavailable',
                         *feed_health_lines(read_feed_health())]))
        return 2
    lines, result = summarize(base)
    print('\n'.join(lines))
    return result


if __name__ == '__main__':
    raise SystemExit(main())
