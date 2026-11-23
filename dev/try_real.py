#!/usr/bin/env python3
"""Run one real briefing or episode against isolated temporary state.

This is the short path for editorial checks. Provider calls use the normal
global usage ledger, so the configured daily and
monthly caps remain authoritative.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from decimal import Decimal
import json
import os
from pathlib import Path
import tempfile
import time
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
DEFAULT_MINUTES = 5
MAX_WAIT_SECONDS = 600


def fictional_profile() -> dict:
    """Return a profile whose labels make a real run easy to identify."""
    topics = [
        # IDs use the small public catalog so the run has feed candidates;
        # labels remain fictional editorial interests for easy identification.
        ('digital', 'AI chips', 'AI accelerators, chip design and computing hardware'),
        # Map the fictional labels to populated public catalog topic IDs.  The
        # labels remain the reader-facing profile; IDs only select candidates.
        ('finance', 'Climate policy', 'Climate regulation, emissions and energy policy'),
        ('science', 'Space launches', 'Orbital launches, launch providers and missions'),
    ]
    return {
        'version': 1,
        'words': '',
        'minutes': 12,
        'background': None,
        'topics': [
            {'id': topic_id, 'label': label, 'scope': scope,
             'include': [label.casefold()], 'exclude': [], 'depth': 'standard',
             'cadence': 'daily', 'mustFollow': True, 'sourcePhrase': label}
            for topic_id, label, scope in topics
        ],
        'muted': [],
    }


@contextmanager
def isolated_run():
    """Set a fresh state directory without touching live briefing state."""
    previous = {key: os.environ.get(key) for key in
                ('BRIEFING_STATE_DIR', 'BRIEFING_EPISODES', 'BRIEFING_TESTING', 'BRIEFING_DAILY_NARRATION')}
    with tempfile.TemporaryDirectory(prefix='briefing-try-real-') as directory:
        state = Path(directory)
        (state / 'profile.json').write_text(json.dumps(fictional_profile()), encoding='utf-8')
        os.environ['BRIEFING_STATE_DIR'] = str(state)
        os.environ['BRIEFING_EPISODES'] = 'real'
        # Exercise the same narrated publication path readers use.
        os.environ['BRIEFING_DAILY_NARRATION'] = 'on'
        os.environ.pop('BRIEFING_TESTING', None)
        try:
            yield state
        finally:
            for key, value in previous.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value


def _money(value) -> Decimal:
    try:
        return Decimal(str(value or '0'))
    except Exception:
        return Decimal('0')


def ledger_snapshot() -> dict:
    from dev.ingest.usage import process_ledger
    return process_ledger().summary()


def ledger_cost_delta(before: dict, after: dict) -> Decimal:
    before_day = before.get('daily', {}) if isinstance(before, dict) else {}
    after_day = after.get('daily', {}) if isinstance(after, dict) else {}
    return _money(after_day.get('estimated_cost_usd')) - _money(before_day.get('estimated_cost_usd'))


def _words(text: str) -> int:
    import re
    return len(re.findall(r"\b[\w’'-]+\b", str(text or '')))


def _print_result(kind: str, result: dict, before: dict, started: float) -> None:
    after = ledger_snapshot()
    cost = ledger_cost_delta(before, after)
    wall = round(time.monotonic() - started, 2)
    print(f'\n=== {kind} ===')
    reported_words = result.get('word_count', result.get('spoken_words'))
    print(f"Duration: {result.get('duration_minutes', result.get('duration_seconds', 0))} minutes")
    print(f'Ledger cost: ${cost:.5f}')
    print(f'Wall time: {wall:.2f}s')
    if kind == 'episode':
        timing = result.get('timing')
        if isinstance(timing, dict):
            print('Stage timing: ' + json.dumps(timing, sort_keys=True))
        audio = result.get('audio') if isinstance(result.get('audio'), dict) else {}
        print('Audio: ' + str(audio.get('status', 'not_recorded')))
        text = result.get('transcript') or ''
    else:
        daily = result.get('daily_briefing') if isinstance(result.get('daily_briefing'), dict) else {}
        text = ' '.join(str(row.get('text') or '') for row in daily.get('briefing_script', [])
                        if isinstance(row, dict))
        print('Audio: not rendered by this editorial script check')
    if not isinstance(reported_words, int) or reported_words <= 0:
        reported_words = _words(text)
    print(f'Word count: {reported_words}')
    warnings = result.get('soft_warnings') or result.get('content_soft_warnings')
    if warnings:
        print('Soft warnings: ' + json.dumps(warnings, sort_keys=True))
    print('\nScript:\n' + str(text).strip())
    if wall > 355:
        print('\nRuntime diagnosis: this exceeded the six-minute reader budget.')
        print('Inspect stage timing above; retrieval and provider calls are the usual bounded stages.')


def _print_funnel(scorecard: dict, selected: dict) -> None:
    diagnostics = scorecard.get('candidate_pool_diagnostics', {}) if isinstance(scorecard, dict) else {}
    reasons = scorecard.get('funnel_drop_reasons', {}) if isinstance(scorecard, dict) else {}
    if not diagnostics:
        return
    print('\nPer-topic supply funnel:')
    print('| Topic | Fetched | Topic-matched | Relevance-passed | Unseen | Selected | Drop reasons |')
    print('|---|---:|---:|---:|---:|---:|---|')
    for topic_id, row in diagnostics.items():
        drops = reasons.get(topic_id, {})
        drop_text = ', '.join(f'{key}: {value}' for key, value in sorted(drops.items())) or 'none'
        print(f"| {topic_id} | {row.get('fetched', 0)} | {row.get('topic_matched', 0)} | "
              f"{row.get('relevance_passed', 0)} | {row.get('unseen', 0)} | "
              f"{selected.get(topic_id, row.get('selected', 0))} | {drop_text} |")


def _print_failed_daily_cost(before: dict, started: float) -> None:
    after = ledger_snapshot()
    cost = ledger_cost_delta(before, after)
    print(f'Ledger cost: ${cost:.5f}')
    print(f'Wall time: {round(time.monotonic() - started, 2):.2f}s')


def run_episode(question: str, minutes: int) -> int:
    with isolated_run() as state:
        from dev.ingest.episodes.generate import generate_episode
        from dev.ingest.paths import profile_identifier
        from dev.ingest.episodes_audio import render_episode_audio

        profile_id = profile_identifier(fictional_profile())
        before = ledger_snapshot()
        started = time.monotonic()
        funnel = {}
        def progress(stage, counts):
            funnel.update(counts if isinstance(counts, dict) else {})
            if stage in {'researching', 'reading', 'evidence', 'checking'}:
                print('Episode funnel: ' + json.dumps({'stage': stage, **funnel}, sort_keys=True), flush=True)
        try:
            episode = generate_episode(question, minutes, 'conversational',
                                       state_dir=state / 'retrieval', progress=progress)
            # A real episode check includes the same TTS path readers use.  A
            # failed render leaves the verified script available for review.
            try:
                rendered = render_episode_audio(episode, profile_id, state_dir=state)
                episode['audio'] = (rendered.get('episode') or {}).get('audio',
                                    {'status': rendered.get('status', 'unknown')})
            except Exception as error:
                episode['audio'] = {'status': 'failed', 'reason': type(error).__name__}
            _print_result('episode', episode, before, started)
            return 0
        except Exception as error:
            _print_result('episode', {'transcript': '', 'audio': {'status': 'not_recorded'},
                                      'timing': {}}, before, started)
            print(f'Generation failed: {type(error).__name__}')
            print(f'Failure code: {str(error)}')
            if funnel:
                print('Episode funnel final: ' + json.dumps(funnel, sort_keys=True))
            code = getattr(error, 'code', None)
            diagnostics = getattr(error, 'diagnostics', None)
            if code:
                print(f'Failure code: {code}')
            if isinstance(diagnostics, dict) and diagnostics:
                funnel = {key: diagnostics.get(key) for key in (
                    'queries', 'sources_found', 'sources_read', 'claims_extracted',
                    'claims_matched', 'claims_verified', 'claims_kept', 'evidence_sentences',
                    'discovered', 'retrieval', 'source_selection') if key in diagnostics}
                if funnel:
                    print('Evidence funnel: ' + json.dumps(funnel, sort_keys=True))
                summary = {key: diagnostics.get(key) for key in (
                    'issues', 'semantic_on_topic', 'semantic_coherent',
                    'unsupported_ids', 'checks') if key in diagnostics}
                if summary:
                    print('Verification summary: ' + json.dumps(summary, sort_keys=True))
                safe = {key: diagnostics[key] for key in
                        ('stage', 'call_name', 'reason', 'finish_reason', 'attempts')
                        if key in diagnostics}
                if safe:
                    print('Failure diagnostics: ' + json.dumps(safe, sort_keys=True))
            return 1


def _poll_daily(job_id: str) -> dict:
    from dev.ingest.generation_api import job_response
    deadline = time.monotonic() + MAX_WAIT_SECONDS
    while time.monotonic() < deadline:
        status, job = job_response(job_id)
        if status != 200:
            raise RuntimeError('daily job disappeared')
        if job.get('status') in {'complete', 'failed'}:
            return job
        time.sleep(0.5)
    raise TimeoutError('daily generation exceeded ten-minute runner limit')


def run_daily() -> int:
    with isolated_run() as state:
        from dev.ingest import core
        from dev.ingest.generation_api import start_generation
        original_ingest = core.ingest_enabled

        def diagnostic_ingest(*args, **kwargs):
            counts = original_ingest(*args, **kwargs)
            results = counts.get('source_results', {})
            errors = counts.get('source_error_types', {})
            print('\nFeed results:')
            for source in args[0] if args else []:
                source_id = source.get('id')
                outcome = results.get(source_id, 'missing')
                detail = errors.get(source_id)
                print(f"  {source_id}: {outcome}" + (f" ({detail})" if detail else ''))
            print('Feed totals: ' + json.dumps({key: counts.get(key) for key in
                                                  ('enabled_sources', 'fetched_sources', 'errors',
                                                   'timed_out_sources', 'elapsed_seconds')}, sort_keys=True))
            return counts

        core.ingest_enabled = diagnostic_ingest
        before = ledger_snapshot()
        started = time.monotonic()
        profile = fictional_profile()
        status, created = start_generation(profile, directory=state, is_default=True,
                                           publish_default=True)
        if status != 202 or not created.get('job_id'):
            print(f'Daily generation failed to start: HTTP {status}')
            return 1
        try:
            job = _poll_daily(created['job_id'])
        except Exception as error:
            print(f'Daily generation failed: {type(error).__name__}')
            _print_failed_daily_cost(before, started)
            return 1
        scorecard_path = state / 'jobs' / f"{created['job_id']}.scorecard.json"
        try:
            scorecard = json.loads(scorecard_path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            scorecard = {}
        selected = job.get('topic_story_counts', {})
        if job.get('status') != 'complete':
            print('Daily generation failed: ' + str(job.get('error') or job.get('failure_reason')))
            narration = job.get('daily_narration')
            if isinstance(narration, dict):
                print('Daily narration diagnostics: ' + json.dumps({key: narration.get(key)
                      for key in ('status', 'reason', 'retry_reason', 'stories', 'story_word_counts')
                      if key in narration}, sort_keys=True))
            _print_failed_daily_cost(before, started)
            _print_funnel(scorecard, selected)
            return 1
        edition = (job.get('edition_ready') or {}).get('edition')
        if not isinstance(edition, dict):
            print('Daily generation failed: edition missing')
            _print_failed_daily_cost(before, started)
            _print_funnel(scorecard, selected)
            return 1
        _print_result('daily', edition, before, started)
        _print_funnel(scorecard, selected)
        return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='kind', required=True)
    sub.add_parser('daily', help='generate one real daily edition')
    episode = sub.add_parser('episode', help='generate one real on-demand episode')
    episode.add_argument('question')
    episode.add_argument('--minutes', type=int, choices=(3, 5, 10, 15), default=DEFAULT_MINUTES)
    args = parser.parse_args(argv)
    if args.kind == 'daily':
        return run_daily()
    return run_episode(args.question, args.minutes)


if __name__ == '__main__':
    raise SystemExit(main())
