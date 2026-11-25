"""Fixture-friendly orchestration for the opt-in multi-reader daily run.

The callbacks are deliberately small boundaries around retrieval, writing and
audio. Production adapters can use the normal ledger and generation pipeline;
tests can use deterministic fakes without provider calls.
"""
from __future__ import annotations

from datetime import date
from dataclasses import dataclass
import os
import time


DEFAULT_READER_RESERVE = 0.40
DEFAULT_READER_LIMIT = 3


class DailyBudgetError(RuntimeError):
    pass


@dataclass
class DailyBudget:
    cap: float
    reserve: float = DEFAULT_READER_RESERVE
    spent: float = 0.0

    def charge(self, amount: float, *, purpose: str) -> None:
        if amount < 0 or self.spent + amount > self.cap + 1e-9:
            raise DailyBudgetError(f"daily budget exhausted during {purpose}")
        self.spent = round(self.spent + amount, 8)

    @property
    def remaining(self) -> float:
        return max(0.0, round(self.cap - self.spent, 8))


def accounts_enabled() -> bool:
    return os.environ.get('BRIEFING_ACCOUNTS', '').strip().lower() == 'on'


def _account_id(profile: dict) -> str:
    value = profile.get('account_id') or profile.get('id')
    if not isinstance(value, str) or not value:
        raise ValueError('reader account id is required')
    return value


def _topic_ids(profile: dict) -> tuple[str, ...]:
    topics = profile.get('topics')
    if not isinstance(topics, list):
        raise ValueError('reader topics are required')
    values = {
        topic.get('id') for topic in topics
        if isinstance(topic, dict) and isinstance(topic.get('id'), str) and topic.get('id')
    }
    return tuple(sorted(values))


def _rotation(profiles: list[dict], run_date: date) -> list[dict]:
    ordered = sorted(profiles, key=_account_id)
    if not ordered:
        return []
    start = run_date.toordinal() % len(ordered)
    return ordered[start:] + ordered[:start]


def run_daily(*, profiles: list[dict], run_date: date, retrieve_topic, write_reader,
              render_audio, budget: DailyBudget, single_reader=None,
              deadline_seconds: float = 300.0, now=None) -> dict:
    """Build one shared evidence pool and fan it out to reader callbacks.

    With accounts disabled, ``single_reader`` is called directly and its return
    value is wrapped without changing the legacy callback's result. With the
    flag enabled, each callback receives only its reader's topic subset.
    """
    if not accounts_enabled():
        if not callable(single_reader) or len(profiles) != 1:
            raise ValueError('single-reader callback is required when accounts are off')
        return {'mode': 'single', 'readers': [single_reader(profiles[0])]}
    if not isinstance(profiles, list):
        raise ValueError('profiles must be a list')
    started = time.monotonic() if now is None else now
    if deadline_seconds <= 0:
        raise ValueError('deadline_seconds must be positive')
    unique_topics = sorted({topic for profile in profiles for topic in _topic_ids(profile)})
    pool = {}
    retrieval_failures = {}
    for topic in unique_topics:
        if time.monotonic() - started >= deadline_seconds:
            retrieval_failures[topic] = 'daily time limit reached'
            continue
        try:
            pool[topic] = retrieve_topic(topic, budget)
        except DailyBudgetError:
            retrieval_failures[topic] = 'shared budget reached'
            break
        except Exception:
            retrieval_failures[topic] = 'evidence was unavailable'

    results = []
    for profile in _rotation(profiles, run_date):
        account_id = _account_id(profile)
        topics = _topic_ids(profile)
        evidence = {topic: pool[topic] for topic in topics if topic in pool}
        if not evidence:
            results.append({'account_id': account_id, 'status': 'not_today',
                            'message': "There was not enough evidence or shared budget to make your briefing today.",
                            'topics': list(topics), 'retrieval_failures':
                            {topic: retrieval_failures.get(topic, 'not available') for topic in topics}})
            continue
        if time.monotonic() - started >= deadline_seconds:
            results.append({'account_id': account_id, 'status': 'not_today',
                            'message': 'The daily run reached its time limit before your briefing was complete.',
                            'topics': list(topics)})
            continue
        try:
            edition = write_reader(profile, evidence, budget)
            audio = render_audio(profile, edition, budget)
            results.append({'account_id': account_id, 'status': 'complete',
                            'edition': edition, 'audio': audio, 'topics': list(topics)})
        except DailyBudgetError:
            results.append({'account_id': account_id, 'status': 'not_today',
                            'message': "There was not enough shared budget to make your briefing today.",
                            'topics': list(topics)})
        except Exception:
            results.append({'account_id': account_id, 'status': 'failed',
                            'message': 'Your briefing could not be completed today.',
                            'topics': list(topics)})
    return {'mode': 'multi', 'topics': unique_topics, 'retrieval_failures': retrieval_failures,
            'spent': budget.spent, 'remaining': budget.remaining, 'readers': results}
