import unittest
from contextlib import redirect_stdout
import io
import tempfile
from pathlib import Path
from decimal import Decimal

from dev.any_topic import (PROMPTS, evaluate_prompt, likely_insufficient_reason,
                           render_report, run_pass, validate_prompts)


class AnyTopicCoverageTest(unittest.TestCase):
    def test_prompt_set_covers_all_requested_types(self):
        self.assertEqual(len(PROMPTS), 60)
        self.assertEqual(len({case.id for case in PROMPTS}), 60)
        self.assertEqual(len(validate_prompts()), 60)
        self.assertEqual({case.category for case in PROMPTS}, {
            'Explainer', 'How-to and advice', 'Current events', 'Comparison',
            'Opinion and debate', 'Niche and technical', 'Very recent',
            'One-word broad topic', 'Ambiguous', 'Sensitive health, legal, or money',
            'Odd phrasing',
        })
        self.assertEqual(sum(case.freshness is not None for case in PROMPTS), 6)

    def test_retrieval_sufficient_uses_the_production_evidence_threshold(self):
        case = PROMPTS[0]
        candidates = [object()]
        result = evaluate_prompt(
            case,
            search=lambda _prompt, _freshness: (candidates, {'gdelt': {'status': 'ok'}}),
            fetch=lambda *_args, **_kwargs: 'ignored by injected evidence builder',
            registry=[],
            evidence_builder=lambda reads, _registry, _now: ([{}], {f'e{i}': {} for i in range(5)}),
        )
        self.assertEqual(result['outcome'], 'retrieval_sufficient')
        self.assertEqual(result['drop_stage'], 'none')
        self.assertEqual(result['candidates'], 1)
        self.assertEqual(result['articles_read'], 1)
        self.assertEqual(result['evidence_sentences'], 5)
        self.assertEqual(result['generation_outcome'], 'not run')
        self.assertEqual(result['model_cost_usd'], 0)

    def test_no_search_results_are_recorded_at_discovery(self):
        result = evaluate_prompt(
            PROMPTS[0],
            search=lambda _prompt, _freshness: ([], {'gdelt': {'status': 'no results'}}),
            fetch=lambda *_args, **_kwargs: self.fail('no candidate should be read'),
            registry=[],
        )
        self.assertEqual(result['outcome'], 'insufficient_evidence')
        self.assertEqual(result['drop_stage'], 'discovery')
        self.assertEqual(result['provider_status'], 'no results')

    def test_unreadable_candidates_drop_at_article_read(self):
        result = evaluate_prompt(
            PROMPTS[0],
            search=lambda _prompt, _freshness: ([object()], {'gdelt': {'status': 'ok'}}),
            fetch=lambda *_args, diagnostics, **_kwargs: diagnostics.append({'reason': 'robots_disallowed'}),
            registry=[],
            evidence_builder=lambda reads, _registry, _now: ([], {}),
        )
        self.assertEqual(result['outcome'], 'insufficient_evidence')
        self.assertEqual(result['drop_stage'], 'article_read')
        self.assertEqual(result['reason'], 'robots_disallowed')

    def test_small_evidence_pack_drops_at_sufficiency_and_report_is_honest(self):
        result = evaluate_prompt(
            PROMPTS[0],
            search=lambda _prompt, _freshness: ([object()], {'gdelt': {'status': 'ok'}}),
            fetch=lambda *_args, **_kwargs: 'a body was read',
            registry=[],
            evidence_builder=lambda reads, _registry, _now: ([{}], {'e1': {}, 'e2': {}}),
        )
        self.assertEqual(result['drop_stage'], 'evidence_sufficiency')
        report = render_report([result], '2026-11-24-retrieval')
        self.assertIn('retrieval estimate, not an episode outcome', report)
        self.assertIn('`not run`', report)
        self.assertIn('$0.00000', report)
        self.assertIn('evidence_sufficiency', report)

    def test_report_distinguishes_prompts_not_measured_after_rate_limit(self):
        measured = evaluate_prompt(
            PROMPTS[0],
            search=lambda *_args: ([], {'gdelt': {'status': 'provider rate limited'}}),
            fetch=lambda *_args, **_kwargs: self.fail('no candidate should be read'),
            registry=[],
        )
        skipped = {
            **measured,
            'id': PROMPTS[1].id,
            'category': PROMPTS[1].category,
            'prompt': PROMPTS[1].prompt,
            'outcome': 'not_measured',
            'drop_stage': 'provider_unavailable',
            'provider_status': 'not attempted after rate limit',
            'reason': 'not attempted after provider rate limit',
        }
        report = render_report([measured, skipped], '2026-11-24-test')
        self.assertIn('Retrieval errors: **1**. Not measured: **1**.', report)
        self.assertIn('rate-limited prompts are retried', report)
        self.assertIn('| retrieval_error | discovery |', report)
        self.assertIn('| not_measured | provider_unavailable |', report)

    def test_rate_limited_prompt_retries_and_completed_results_resume(self):
        calls = []

        def search(prompt, _freshness):
            calls.append(prompt)
            status = 'provider rate limited' if len(calls) == 1 else 'no results'
            return [], {'gdelt': {'status': status}}

        search.retry_delay = lambda: 0
        with tempfile.TemporaryDirectory(prefix='any-topic-test-') as temp:
            progress = Path(temp) / 'progress.jsonl'
            with redirect_stdout(io.StringIO()):
                rows = run_pass(PROMPTS, search=search,
                                fetch=lambda *_args, **_kwargs: None, registry=[],
                                spacing_seconds=0, retries=1, sleep=lambda _delay: None,
                                resume_path=progress)
            self.assertEqual(len(rows), 60)
            self.assertEqual(len(calls), 61)
            self.assertEqual(rows[0]['outcome'], 'insufficient_evidence')
            with redirect_stdout(io.StringIO()):
                resumed = run_pass(PROMPTS, search=search,
                                   fetch=lambda *_args, **_kwargs: None, registry=[],
                                   spacing_seconds=0, resume_path=progress)
            self.assertEqual(len(resumed), 60)
            self.assertEqual(len(calls), 61)

    def test_grounded_run_reserves_cost_and_labels_unmeasured_prompts(self):
        calls = []

        def search(prompt, _freshness):
            calls.append(prompt)
            return [], {'gemini': {'status': 'ok'}}, Decimal('0.20')

        with redirect_stdout(io.StringIO()):
            rows = run_pass(PROMPTS[:3], search=search,
                            fetch=lambda *_args, **_kwargs: None, registry=[],
                            max_cost=Decimal('0.25'), max_next_cost=Decimal('0.10'))
        self.assertEqual(len(calls), 1)
        self.assertEqual(rows[0]['model_cost_usd'], 0.2)
        self.assertEqual([row['outcome'] for row in rows], [
            'insufficient_evidence', 'not_measured', 'not_measured'])
        self.assertTrue(all(row['drop_stage'] == 'cost_cap' for row in rows[1:]))

    def test_likely_reason_points_to_reed_fallback_target(self):
        self.assertEqual(likely_insufficient_reason({
            'outcome': 'insufficient_evidence', 'category': 'One-word broad topic',
            'drop_stage': 'discovery'}), 'too broad or ambiguous')
        self.assertEqual(likely_insufficient_reason({
            'outcome': 'insufficient_evidence', 'category': 'Comparison',
            'drop_stage': 'article_read'}), 'comparison needs evidence for both sides')
        self.assertIn('niche', likely_insufficient_reason({
            'outcome': 'insufficient_evidence', 'category': 'Niche and technical',
            'drop_stage': 'article_read'}))
        self.assertIn('recent', likely_insufficient_reason({
            'outcome': 'insufficient_evidence', 'category': 'Very recent',
            'drop_stage': 'discovery'}))


if __name__ == '__main__':
    unittest.main()
