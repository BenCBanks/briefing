#!/usr/bin/env python3
"""Run generation in a preview server backed by a temporary copy of local state."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from dev.ingest.paths import STATE_DIR, checked_state_path

DEFAULT_STATE = STATE_DIR
PLACEHOLDER = re.compile(
    r'no verified stories were available|none of \d+ candidate(?:\(s\)|s)? passed|'
    r'no fresh source-linked story passed|only \d+ stories? met our checks', re.I)
CITATION = re.compile(r'\[((?:\d+(?:\s*,\s*\d+)*|[A-Z]))\]')
CITED_SENTENCE = re.compile(r'[^.!?\r\n]+[.!?]\s*\[(\d+(?:\s*,\s*\d+)*)\]')
NUMERIC_CITATION = re.compile(r'\[\d+(?:\s*,\s*\d+)*\]')
MIN_STORIES, MIN_CITED_SENTENCES = 3, 2


def assess_story(story: dict, sources: list[dict]) -> dict:
    body = story.get('body') if isinstance(story.get('body'), str) else ''
    source_list = story.get('source_ids') if isinstance(story.get('source_ids'), list) else []
    source_ids = set(source_list)
    source_by_id = {}
    for source in sources:
        if not isinstance(source, dict) or not source.get('id'):
            continue
        try:
            url = urlsplit(str(source.get('url') or ''))
            resolved = (url.scheme in {'http', 'https'} and bool(url.hostname) and
                        not url.username and not url.password)
        except ValueError:
            continue
        if resolved:
            source_by_id[source['id']] = source
    evidence_chars = sum(max(0, source.get('evidence_chars', 0))
                         for source in sources if isinstance(source, dict)
                         and source.get('id') in source_ids
                         and isinstance(source.get('evidence_chars', 0), int)
                         and not isinstance(source.get('evidence_chars', 0), bool))
    distinct_facts = set()
    unresolved_citations = 0
    for match in CITED_SENTENCE.finditer(body):
        numbers = [int(value.strip()) for value in match.group(1).split(',')]
        if not numbers or any(number < 1 or number > len(source_list) or
                              source_list[number - 1] not in source_by_id for number in numbers):
            unresolved_citations += 1
            continue
        sentence = NUMERIC_CITATION.sub('', match.group(0)).casefold()
        fact = ' '.join(re.findall(r'\w+', sentence))
        if fact:
            distinct_facts.add(fact)
    cited_sentences = len(distinct_facts)
    reasons = []
    if not story.get('cluster_id'):
        reasons.append('not a generated story item')
    if not body.strip():
        reasons.append('empty body')
    elif PLACEHOLDER.search(body):
        reasons.append('placeholder or no-candidate result')
    if cited_sentences < MIN_CITED_SENTENCES:
        reasons.append(f'{cited_sentences} distinct cited sentence(s); requires {MIN_CITED_SENTENCES}')
    if unresolved_citations:
        reasons.append(f'{unresolved_citations} cited sentence(s) reference unresolved sources')
    return {'story_id': story.get('id'), 'heading': story.get('heading', ''),
            'body_chars': len(body), 'cited_sentences': cited_sentences,
            'evidence_chars': evidence_chars, 'substantive': not reasons, 'reasons': reasons}


def substantive_stories(edition: dict, sources: list[dict]) -> tuple[list[dict], list[dict]]:
    accepted, assessments = [], []
    for section in edition.get('sections', []) if isinstance(edition, dict) else []:
        if not isinstance(section, dict):
            continue
        assessment = assess_story(section, sources)
        assessments.append(assessment)
        if assessment['substantive']:
            accepted.append(section)
    return accepted, assessments


def _request_json(url: str, payload: dict | None = None, timeout: float = 10) -> tuple[int, dict]:
    data = json.dumps(payload).encode('utf-8') if payload is not None else None
    request = Request(url, data=data, method='POST' if data is not None else 'GET',
                      headers={'Accept': 'application/json', 'Content-Type': 'application/json',
                               'User-Agent': 'BriefingRealBuildAcceptance/1.0'})
    try:
        with urlopen(request, timeout=timeout) as response:
            raw, status = response.read(), response.status
    except HTTPError as error:
        raw, status = error.read(), error.code
    except (URLError, TimeoutError, OSError) as error:
        raise RuntimeError(f'HTTP request failed: {error}') from error
    try:
        result = json.loads(raw.decode('utf-8'))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RuntimeError(f'HTTP {status} returned invalid JSON') from error
    if not isinstance(result, dict):
        raise RuntimeError(f'HTTP {status} returned a non-object JSON response')
    return status, result


def _copy_state(source: Path, destination: Path, profile_file: Path | None = None) -> dict:
    if not source.is_dir():
        raise RuntimeError(f'Briefing state directory is missing: {source}')
    shutil.copytree(source, destination, ignore=shutil.ignore_patterns('*.tmp', 'generation.lock'))
    try:
        profile_path = profile_file.expanduser() if profile_file else destination / 'profile.json'
        profile = json.loads(profile_path.read_text(encoding='utf-8'))
    except (OSError, ValueError, TypeError) as error:
        raise RuntimeError('Copied state has no readable profile.json') from error
    if not isinstance(profile, dict) or not isinstance(profile.get('topics'), list):
        raise RuntimeError('Copied active profile is invalid or has no topics list')
    if profile_file:
        (destination / 'profile.json').write_text(json.dumps(profile, ensure_ascii=False), encoding='utf-8')
    return profile


def _scorecard(state_dir: Path, profile: dict, job: dict) -> dict:
    relative = job.get('scorecard_path')
    if not isinstance(relative, str) or not relative:
        return {}
    from dev.ingest.paths import profile_identifier
    path = state_dir / 'generated' / 'profiles' / profile_identifier(profile) / relative
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def _print_candidates(report: dict) -> None:
    candidates = report.get('candidate_results', []) if isinstance(report, dict) else []
    any_failures = False
    for row in candidates if isinstance(candidates, list) else []:
        if not isinstance(row, dict) or row.get('status') in {'passed', 'generated'}:
            continue
        any_failures = True
        print(f"Candidate {row.get('candidate', '?')} ({row.get('topic_id', 'unknown')}): "
              f"{row.get('reason') or row.get('status') or 'failed checks'}")
        for check in row.get('failed_checks', []) if isinstance(row.get('failed_checks'), list) else []:
            if isinstance(check, dict):
                print(f"  check {check.get('number', '?')}: {check.get('detail', check.get('status', 'failed'))}")
        if row.get('failed_critical'):
            print('  critical checks: ' + ', '.join(map(str, row['failed_critical'])))
    if not any_failures:
        print('No candidate-level failure details were recorded.')


def _print_story(index: int, story: dict, sources: list[dict], report: dict, elapsed: float) -> dict:
    result = assess_story(story, sources)
    by_id = {row.get('id'): row for row in sources if isinstance(row, dict)}
    markers = list(dict.fromkeys(CITATION.findall(story.get('body', ''))))
    source_ids = story.get('source_ids', [])
    print(f"\n=== Story {index}: {story.get('heading', '(untitled)')} ===")
    print(f'Elapsed: {elapsed:.2f}s')
    print(f"Scorecard: retained after per-story checks; edition critical_failed={report.get('critical_failed', 'unknown')}; "
          f"failed_critical={report.get('failed_critical', [])}")
    print(f"Evidence chars: {result['evidence_chars']}; cited sentences: {result['cited_sentences']}; "
          f"body chars: {result['body_chars']}")
    print('Citations:')
    for marker in markers:
        numbers = [int(value.strip()) for value in marker.split(',') if value.strip().isdigit()]
        for number in numbers:
            source_id = source_ids[number - 1] if 0 < number <= len(source_ids) else None
            source = by_id.get(source_id, {})
            title = source.get('title') or source.get('publisher') or source.get('kind') or 'unresolved source'
            suffix = f" — {source.get('url')}" if source.get('url') else ''
            print(f'  [{number}] {title}{suffix}')
    if not markers:
        print('  (none)')
    print('Full body:')
    print(story.get('body', ''))
    if result['reasons']:
        print('Acceptance reasons: ' + '; '.join(result['reasons']))
    return result


def _inspect(state_dir: Path, profile: dict, job: dict, elapsed: float,
             require_housing_stories: bool = False, content_report: Path | None = None) -> bool:
    report = _scorecard(state_dir, profile, job)
    print(f"\nGeneration status: {job.get('status')} — "
          f"{job.get('failure_reason') or job.get('error') or 'no failure reason'}")
    ready = job.get('edition_ready') if isinstance(job.get('edition_ready'), dict) else {}
    edition = ready.get('edition') if isinstance(ready.get('edition'), dict) else {}
    sources = ready.get('sources') if isinstance(ready.get('sources'), list) else []
    if content_report is not None:
        from dev.ingest.paths import profile_identifier
        history_path = state_dir / 'generated/profiles' / profile_identifier(profile) / 'editions-history.json'
        try:
            history = json.loads(history_path.read_text())
        except FileNotFoundError:
            history = []
        except (OSError, ValueError):
            history = None  # The content checker reports unavailable history as WARN.
        status = job.get('status') if job.get('status') in {'complete', 'failed'} else 'unknown'
        raw_failure = job.get('failure_reason') or job.get('error')
        failure_code = (raw_failure if isinstance(raw_failure, str) and
                        re.fullmatch(r'[A-Za-z0-9_.:-]{1,160}', raw_failure) else None)
        try:
            content_report.write_text(json.dumps({'payload': {'edition': edition, 'sources': sources},
                                                  'history': history, 'profile': profile,
                                                  'diagnostic': {
                                                      'job_status': status,
                                                      'failure_code': failure_code,
                                                  }}))
            content_report.chmod(0o600)
        except OSError:
            print('WARN: content-check report could not be written (non-blocking).')
    stories = [row for row in edition.get('sections', [])
               if isinstance(row, dict) and row.get('cluster_id')]
    topics = {row.get('id'): row for row in profile.get('topics', []) if isinstance(row, dict)}
    housing_ids = {topic_id for topic_id, row in topics.items()
                   if any(term in ' '.join((row.get('label', ''), row.get('scope', ''))).lower()
                          for term in ('housing', 'home', 'construction', 'affordable', 'rent'))}
    housing_stories = sum(bool(set(row.get('topic_ids', [])) & housing_ids) for row in stories)
    assessments = [_print_story(i, story, sources, report, elapsed)
                   for i, story in enumerate(stories, 1)]
    accepted = sum(assessment['substantive'] is True for assessment in assessments)
    story_results = report.get('story_results', []) if isinstance(report, dict) else []
    if story_results:
        print('\nStory scorecard results:')
        for row in story_results:
            if isinstance(row, dict):
                print(f"  {row.get('story', '?')}: {row.get('status')}; "
                      f"failed critical={row.get('failed_critical', [])}; checks={row.get('checks', [])}")
    if job.get('status') != 'complete' or accepted < MIN_STORIES:
        print('\nCandidate failure reasons:')
        _print_candidates(report)
    if require_housing_stories:
        print(f'Housing-mapped stories: {housing_stories}; housing profile topics={sorted(housing_ids)}')
    print(f'\nAcceptance: {accepted} substantive stories; requires at least {MIN_STORIES}.')
    return (job.get('status') == 'complete' and accepted >= MIN_STORIES and
            (not require_housing_stories or bool(housing_ids) and housing_stories >= MIN_STORIES))


def run(state_source: Path = DEFAULT_STATE, timeout_seconds: int = 240,
        poll_seconds: float = 1.0, profile_file: Path | None = None,
        require_housing_stories: bool = False, content_report: Path | None = None) -> int:
    state_source = checked_state_path(state_source)
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix='briefing-real-build-') as temporary:
        root = Path(temporary)
        state_copy = root / 'state'
        profile = _copy_state(Path(state_source).expanduser(), state_copy, profile_file)
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        base = f'http://127.0.0.1:{port}'
        env = os.environ.copy()
        env['BRIEFING_STATE_DIR'] = str(state_copy)
        with (root / 'preview.log').open('wb') as log:
            server = subprocess.Popen([sys.executable, str(ROOT / 'dev/preview_server.py'),
                                       '127.0.0.1', str(port)], cwd=ROOT, env=env,
                                      stdout=log, stderr=log)
            try:
                ready_by = time.monotonic() + 30
                while True:
                    if server.poll() is not None:
                        server_log = (root / 'preview.log').read_text(errors='replace')[-2000:]
                        raise RuntimeError(f'preview server exited early; log: {server_log}')
                    try:
                        status, _ = _request_json(base + '/api/profile', timeout=2)
                        if status == 200:
                            break
                    except RuntimeError:
                        pass
                    if time.monotonic() >= ready_by:
                        raise RuntimeError('preview server did not become ready')
                    time.sleep(0.1)
                status, created = _request_json(base + '/api/generate', {'profile': profile})
                if status != 202 or not created.get('job_id'):
                    raise RuntimeError(f'POST /api/generate failed with HTTP {status}: {created}')
                job_url = base + '/api/generate/' + quote(created['job_id'], safe='')
                deadline = time.monotonic() + timeout_seconds
                while time.monotonic() < deadline:
                    status, job = _request_json(job_url)
                    if status != 200:
                        raise RuntimeError(f'job poll returned HTTP {status}: {job}')
                    if job.get('status') in {'complete', 'failed'}:
                        elapsed = float(job.get('elapsed_seconds') or time.monotonic() - started)
                        return 0 if _inspect(state_copy, profile, job, elapsed,
                                             require_housing_stories, content_report) else 1
                    time.sleep(poll_seconds)
                print(f'Generation timed out after {timeout_seconds}s.')
                return 1
            finally:
                server.terminate()
                try:
                    server.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    server.kill()
                    server.wait(timeout=5)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state-dir', type=Path, default=DEFAULT_STATE,
                        help='state directory to copy (default: ~/.local/state/briefing)')
    parser.add_argument('--timeout', type=int, default=240)
    parser.add_argument('--content-report', type=Path, help='write private temporary content-check inputs before cleanup')
    parser.add_argument('--profile-json', type=Path,
                        help='Use this profile in the copied state instead of its saved profile.')
    parser.add_argument('--require-housing-stories', action='store_true',
                        help='Require at least three substantive stories mapped to housing topics.')
    args = parser.parse_args(argv)
    if args.timeout < 1:
        parser.error('--timeout must be positive')
    try:
        return run(args.state_dir, timeout_seconds=args.timeout, profile_file=args.profile_json,
                   require_housing_stories=args.require_housing_stories, content_report=args.content_report)
    except (OSError, RuntimeError, ValueError) as error:
        print(f'Acceptance build failed: {error}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
