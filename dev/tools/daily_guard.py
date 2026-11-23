#!/usr/bin/env python3
"""Decide whether the scheduled daily retry should run; never generates content."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dev.content_check import check_content, unpack
from dev.ingest.paths import STATE_DIR, checked_state_path


DEFAULT_STATE_DIR = STATE_DIR
READY_AUDIO_STATES = {'ready', 'recorded'}


def read_json(path):
    try:
        value = json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, TypeError):
        return None
    return value if isinstance(value, (dict, list)) else None


def load_edition_bundle(state_dir):
    generated = Path(state_dir) / 'generated'
    for path in (generated / 'default' / 'current.json',
                 generated / 'default' / 'editions.json',
                 generated / 'current.json'):
        value = read_json(path)
        if not isinstance(value, dict):
            continue
        try:
            edition, _sources = unpack(value)
        except (ValueError, TypeError, KeyError):
            continue
        if edition:
            return value, edition
    return None, None


def _history(state_dir, bundle):
    value = read_json(Path(state_dir) / 'generated' / 'default' / 'editions-history.json')
    if isinstance(value, list):
        return value
    embedded = bundle.get('history') if isinstance(bundle, dict) else None
    return embedded if isinstance(embedded, list) else []


def _spoken_word_count(edition):
    daily = edition.get('daily_briefing') if isinstance(edition, dict) else None
    daily = daily if isinstance(daily, dict) else {}
    script = daily.get('briefing_script')
    if isinstance(script, list):
        text = ' '.join(row.get('text', '') for row in script
                        if isinstance(row, dict) and isinstance(row.get('text'), str))
        return len(re.findall(r"\b[\w’'-]+\b", text))
    count = daily.get('spoken_words', edition.get('spoken_word_count', 0))
    return count if type(count) is int and count >= 0 else 0


def _audio_available(edition):
    audio = edition.get('audio') if isinstance(edition, dict) else None
    if not isinstance(audio, dict):
        return False
    state = audio.get('audio_status') or audio.get('state')
    url = audio.get('audio_url') or audio.get('url')
    return state in READY_AUDIO_STATES and isinstance(url, str) and bool(url.strip())


def decide(*, status, content_checks, edition, audio_budget_allows):
    """Return retry decision and fixed reason labels from already-read values."""
    status = status if isinstance(status, dict) else {}
    run = status.get('daily_run') if isinstance(status.get('daily_run'), dict) else {}
    reasons = []
    if status.get('ok') is not True or run.get('status') in {'failed', 'timeout'}:
        reasons.append('status_not_ok')
    if (not isinstance(content_checks, list) or not content_checks or
            any(not isinstance(row, dict) or row.get('passed') is not True
                for row in content_checks)):
        reasons.append('content_check_failed')
    words = _spoken_word_count(edition) if isinstance(edition, dict) else 0
    if words / 150 < 3:
        reasons.append('spoken_under_3_minutes')
    tts_enabled = os.environ.get('BRIEFING_TTS', 'enabled').strip().lower() not in {
        '0', 'false', 'off', 'disabled'}
    if not _audio_available(edition) and tts_enabled and audio_budget_allows:
        reasons.append('audio_missing_within_cap')
    return bool(reasons), reasons


def audio_budget_allows(edition):
    """Estimate this edition's TTS cost and compare it with the daily reserve."""
    from dev.ingest.daily_audio import daily_episode_from_edition
    from dev.ingest.usage import process_ledger

    ledger = process_ledger()
    episode = daily_episode_from_edition(edition)
    chars = sum(len(chapter['spoken_text']) for chapter in episode['chapters'])
    estimated_seconds = max(5 * 60, chars / 12)
    estimate = ledger.estimate_tts_cost(chars, estimated_seconds)
    return estimate <= ledger.daily_audio_budget_remaining()


def inspect_state(state_dir, budget_checker=audio_budget_allows):
    state_dir = checked_state_path(state_dir)
    state_dir = Path(state_dir)
    status = read_json(state_dir / 'status.json')
    status = status if isinstance(status, dict) else {}
    bundle, edition = load_edition_bundle(state_dir)
    profile = read_json(state_dir / 'profile.json')
    history = _history(state_dir, bundle) if bundle else []
    try:
        checks = check_content(bundle, history, profile) if bundle and profile else []
    except (ValueError, TypeError, KeyError):
        checks = []
    within_audio_cap = False
    if edition and not _audio_available(edition) and os.environ.get('BRIEFING_TTS', 'enabled').strip().lower() not in {
            '0', 'false', 'off', 'disabled'}:
        try:
            within_audio_cap = bool(budget_checker(edition))
        except Exception:
            # An unreadable ledger is never evidence that the reserve can cover audio.
            within_audio_cap = False
    retry, reasons = decide(status=status, content_checks=checks, edition=edition,
                            audio_budget_allows=within_audio_cap)
    return retry, reasons


def main(argv=None, *, state_dir=None, budget_checker=audio_budget_allows):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state-dir', type=Path, default=DEFAULT_STATE_DIR)
    args = parser.parse_args(argv)
    retry, reasons = inspect_state(state_dir or args.state_dir, budget_checker=budget_checker)
    decision = 'retry' if retry else 'skip'
    suffix = ','.join(reasons) if reasons else 'healthy'
    print(f'daily_guard decision={decision} reasons={suffix}')
    return 0 if retry else 1


if __name__ == '__main__':
    raise SystemExit(main())
