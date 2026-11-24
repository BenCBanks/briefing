"""Fixture timing and concurrency checks for bounded episode research."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from dev.test_guard import enforce_test_environment
enforce_test_environment()

from types import SimpleNamespace
import threading
import time
import unittest
from unittest.mock import patch

from dev.ingest.retrieval import Candidate
from dev.ingest.retrieval.episode_rounds import research_episode


class FixtureProvider:
    name = 'fixture-search'

    def __init__(self, candidates):
        self.candidates = candidates

    def search(self, query, *, max_results, freshness, budget):
        budget.claim()
        return self.candidates[:max_results]


def candidates(count):
    return [Candidate(f'https://publisher-{index}.example.test/story',
                      f'Fictional research result {index}', 'Fictional outlet', None,
                      'fixture-search', index) for index in range(count)]


class EpisodeRoundsTests(unittest.TestCase):
    def research(self, fetcher, *, rows=None, minutes=5):
        rows = rows or candidates(12)
        return research_episode(['How does the fictional system work?'], minutes,
            providers=[FixtureProvider(rows)], registry=[],
            config=SimpleNamespace(domain_tiers={}), deadline=time.monotonic() + 2,
            check=lambda: None, progress=lambda *_: None, fetcher=fetcher)

    def test_source_reads_run_concurrently_and_report_the_bounded_stage_budget(self):
        lock = threading.Lock()
        active = maximum = 0

        def fetcher(candidate, **kwargs):
            nonlocal active, maximum
            with lock:
                active += 1
                maximum = max(maximum, active)
            time.sleep(.04)
            with lock:
                active -= 1
            return 'The fictional source describes one part of the system. It explains how the service works.'

        started = time.monotonic()
        result = self.research(fetcher)
        elapsed = time.monotonic() - started
        self.assertGreater(maximum, 1, 'independent source reads overlap')
        self.assertLess(elapsed, .25, 'parallel reads complete faster than a serial batch')
        self.assertGreaterEqual(len(result['reads']), 8)
        self.assertEqual(result['research_budget_seconds'], 165)
        self.assertEqual(result['source_read_deadline_seconds'], 12)
        self.assertEqual(result['max_concurrent_reads'], 8)
        self.assertLessEqual(result['research_elapsed_seconds'], elapsed + .02)

    def test_timed_out_host_is_dropped_without_blocking_fast_sources(self):
        slow = Candidate('https://slow.example.test/story', 'Slow fictional result',
                         'Slow outlet', None, 'fixture-search', 0)
        fast = candidates(5)
        lock = threading.Lock()
        active_hosts = set()

        def fetcher(candidate, **kwargs):
            host = candidate.url.split('/')[2]
            if host == 'slow.example.test':
                time.sleep(.12)
                return 'The slow source eventually responds. It should have been dropped early.'
            with lock:
                active_hosts.add(host)
            return 'The fast fictional source explains one useful detail. It supplies a second sentence.'

        with patch('dev.ingest.retrieval.episode_rounds.SOURCE_READ_DEADLINE_SECONDS', .025):
            started = time.monotonic()
            result = self.research(fetcher, rows=[slow, *fast])
            elapsed = time.monotonic() - started
        self.assertLess(elapsed, .10)
        self.assertNotIn(slow.url, result['reads'])
        self.assertGreaterEqual(len(result['reads']), 3)
        self.assertEqual(result['read_timeouts'], 1)
        self.assertEqual(result['slow_hosts_dropped'], 1)
        self.assertIn('publisher-0.example.test', active_hosts)


if __name__ == '__main__':
    unittest.main()
