#!/usr/bin/env python3
"""Read-only HTTP smoke checks for a deployed Briefing web app."""

from datetime import date, datetime
import json
from pathlib import Path
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from dev.acceptance_real_build import substantive_stories


TIMEOUT_SECONDS = 10
EASTERN = ZoneInfo('America/New_York')


def eastern_today(now=None):
    """Return the edition calendar date in America/New_York."""
    now = now or datetime.now(EASTERN)
    if now.tzinfo is None:
        now = now.replace(tzinfo=ZoneInfo('UTC'))
    return now.astimezone(EASTERN).date()


def http_get(url):
    request = Request(url, headers={'Accept': 'application/json, text/html, */*', 'User-Agent': 'BriefingSmoke/1.0'})
    try:
        with urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            return response.status, response.read()
    except HTTPError as error:
        return error.code, error.read()
    except (URLError, TimeoutError, OSError) as error:
        raise RuntimeError(f'GET {url} failed: {error}') from error


def run_checks(base_url, today=None, fetch=http_get):
    """Return (name, passed, detail) rows; fetch(url) returns (status, body)."""
    base = base_url.rstrip('/')
    parsed = urlsplit(base)
    if parsed.scheme not in {'http', 'https'} or not parsed.netloc:
        raise ValueError('Base URL must start with http:// or https:// and include a host.')
    expected_date = today or eastern_today()
    checks = []
    edition_payload = None

    def check(name, operation):
        try:
            detail = operation()
            checks.append((name, True, detail))
        except (AssertionError, KeyError, TypeError, ValueError, RuntimeError, json.JSONDecodeError) as error:
            checks.append((name, False, str(error)))

    def get_bytes(path):
        status, body = fetch(f'{base}{path}')
        if status != 200:
            raise RuntimeError(f'HTTP {status} for {path}')
        return body

    def get_json(path):
        try:
            value = json.loads(get_bytes(path).decode('utf-8'))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError(f'{path} did not return valid JSON: {error}') from error
        if not isinstance(value, dict):
            raise ValueError(f'{path} did not return a JSON object.')
        return value

    def check_edition():
        nonlocal edition_payload
        edition_payload = get_json('/api/edition')
        return _validate_edition(edition_payload)

    check('daily edition', check_edition)

    def check_active_edition():
        nonlocal edition_payload
        active_payload = get_json('/api/edition?profile=active')
        detail = _validate_edition(active_payload)
        edition_payload = active_payload
        return f'{detail} from active profile'

    check('active profile edition', check_active_edition)

    def check_history():
        history_payload = get_json('/api/edition?profile=active&history=1')
        history = history_payload.get('history')
        if not isinstance(history, list) or not history:
            raise ValueError('Edition history is missing or empty.')
        valid = [row for row in history if isinstance(row, dict) and isinstance(row.get('edition'), dict) and isinstance(row.get('sources'), list)]
        if len(valid) != len(history):
            raise ValueError('One or more history entries are missing edition or source data.')
        return f'{len(valid)} historical edition(s) with sources'

    check('edition history', check_history)

    def check_status():
        status = get_json('/api/status')
        if status.get('ok') is not True:
            raise ValueError(f"/api/status reports ok={status.get('ok')!r}.")
        return 'API reports ok=true'

    check('status', check_status)

    def check_current_date():
        if edition_payload is None or not isinstance(edition_payload.get('edition'), dict):
            raise RuntimeError('Skipped because the daily edition response did not load.')
        value = edition_payload['edition'].get('local_date')
        try:
            edition_date = date.fromisoformat(value)
        except (TypeError, ValueError) as error:
            raise ValueError(f'Daily edition has invalid local_date: {value!r}.') from error
        if edition_date != expected_date:
            raise ValueError(f'Daily edition date is {edition_date}; expected today, {expected_date} (America/New_York).')
        return f'edition date is today ({expected_date})'

    check('freshness', check_current_date)

    def check_shell(path, label, required_text):
        body = get_bytes(path).decode('utf-8', errors='replace')
        missing = [text for text in required_text if text not in body]
        if missing:
            raise ValueError(f'{label} shell is missing: {", ".join(missing)}.')
        return 'HTTP 200; app shell and client entry point present'

    def check_play_route():
        check_shell('/#today', 'Play', ['src/app.js', 'id="player-root"'])
        app = get_bytes('/src/app.js').decode('utf-8', errors='replace')
        interactions = get_bytes('/src/screens/story-interactions.js').decode('utf-8', errors='replace')
        player = get_bytes('/src/player.js').decode('utf-8', errors='replace')
        if 'createStoryInteractionController' not in app or 'function startListening(' not in interactions or 'function listen(item' not in player:
            raise ValueError('Play route or player handler is missing.')
        return 'HTTP 200; Today shell and listening handlers present'

    check('play route', check_play_route)

    def check_transcript_route():
        edition = edition_payload.get('edition') if isinstance(edition_payload, dict) else None
        sections = edition.get('sections', []) if isinstance(edition, dict) else []
        section = next((row for row in sections if isinstance(row, dict) and row.get('id')), None)
        if not section:
            raise RuntimeError('Skipped because no edition section is available.')
        edition_id = quote(str(edition.get('id', '')), safe='')
        section_id = quote(str(section['id']), safe='')
        route = f'/#edition/daily/{edition_id}/{section_id}'
        check_shell(route, 'Transcript', ['src/app.js', 'id="content"'])
        app = get_bytes('/src/app.js').decode('utf-8', errors='replace')
        interactions = get_bytes('/src/screens/story-interactions.js').decode('utf-8', errors='replace')
        edition_client = get_bytes('/src/edition/edition.js').decode('utf-8', errors='replace')
        if 'createStoryInteractionController' not in app or 'function openTranscript(' not in interactions or 'export function renderEdition(' not in edition_client:
            raise ValueError('Transcript or edition rendering handler is missing.')
        return 'HTTP 200; edition deep-link shell and transcript/rendering handlers present'

    check('transcript route', check_transcript_route)
    return checks


def _validate_edition(payload):
    if payload.get('generated') is not True:
        raise ValueError('No generated daily edition is available.')
    edition = payload.get('edition')
    if not isinstance(edition, dict) or edition.get('kind') != 'daily':
        raise ValueError('Response does not contain a generated daily edition.')
    sections = edition.get('sections')
    if not isinstance(sections, list):
        raise ValueError('Daily edition sections are missing.')
    sources = payload.get('sources')
    if not isinstance(sources, list):
        raise ValueError('Daily edition sources are missing.')
    substantive, assessments = substantive_stories(edition, sources)
    if len(substantive) < 3:
        summary = [f"{row.get('heading') or row.get('story_id') or '(untitled)'}: "
                   f"{'; '.join(row['reasons']) or 'not substantive'}"
                   for row in assessments if not row['substantive']]
        detail = '; '.join(summary[:3])
        raise ValueError(f'Only {len(substantive)} substantive stories; at least 3 are required.'
                         + (f' {detail}' if detail else ''))
    return f'{len(substantive)} substantive stories with cited sentences and 400+ evidence chars'


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1:
        print('Usage: python3 dev/smoke_live.py BASE_URL', file=sys.stderr)
        return 2
    try:
        checks = run_checks(args[0])
    except ValueError as error:
        print(f'Invalid base URL: {error}', file=sys.stderr)
        return 2
    for name, passed, detail in checks:
        print(f"{'PASS' if passed else 'FAIL'} {name}: {detail}")
    failures = sum(not passed for _, passed, _ in checks)
    print(f'Smoke check: {len(checks) - failures}/{len(checks)} passed')
    return 1 if failures else 0


if __name__ == '__main__':
    raise SystemExit(main())
