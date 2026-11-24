import unittest

from dev.any_topic import PROMPTS, evaluate_prompt, render_report, validate_prompts


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
        self.assertIn('Prompts marked `not_measured` were not sent', report)
        self.assertIn('| retrieval_error | discovery |', report)
        self.assertIn('| not_measured | provider_unavailable |', report)


if __name__ == '__main__':
    unittest.main()
