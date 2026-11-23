import sys as _briefing_sys
from pathlib import Path as _BriefingPath
_briefing_sys.path.insert(0, str(_BriefingPath(__file__).resolve().parents[2]))
from dev.test_guard import enforce_test_environment as _enforce_test_environment
_enforce_test_environment()

import sys
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from dev import acceptance_real_build
from dev.acceptance_real_build import assess_story, substantive_stories


class RealBuildAcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.sources = [{'id': 'source-1', 'url': 'https://fictional.example.test/source-1',
                         'evidence_chars': 400}]
        self.story = {'id': 'fictional-story', 'cluster_id': 'fictional-cluster',
                      'heading': 'Fictional update', 'source_ids': ['source-1'],
                      'body': 'A fictional review ended on May 12. [1] It covered the deposit service. [1]'}

    def test_concise_story_is_substantive_with_two_distinct_source_resolved_facts(self):
        result = assess_story(self.story, self.sources)
        self.assertLess(result['body_chars'], 400)
        self.assertTrue(result['substantive'], result)
        self.assertEqual(result['cited_sentences'], 2)
        self.assertEqual(result['evidence_chars'], 400)
        accepted, rows = substantive_stories({'sections': [self.story]}, self.sources)
        self.assertEqual(accepted, [self.story])
        self.assertTrue(rows[0]['substantive'])

    def test_story_fails_when_it_has_fewer_than_two_cited_facts(self):
        story = {**self.story, 'body': 'A single fictional claim. [1]'}
        result = assess_story(story, [{'id': 'source-1', 'url': 'https://fictional.example.test/source-1',
                                       'evidence_chars': 900}])
        self.assertFalse(result['substantive'])
        self.assertEqual(result['reasons'], ['1 distinct cited sentence(s); requires 2'])

    def test_repeating_one_cited_fact_does_not_satisfy_two_fact_gate(self):
        story = {**self.story,
                 'body': 'The review ended on May 12. [1] The review ended on May 12! [1]'}
        result = assess_story(story, self.sources)
        self.assertEqual(result['cited_sentences'], 1)
        self.assertFalse(result['substantive'])
        self.assertEqual(result['reasons'], ['1 distinct cited sentence(s); requires 2'])

    def test_two_sources_on_one_sentence_do_not_count_as_two_cited_sentences(self):
        story = {**self.story, 'source_ids': ['source-1', 'source-2'],
                 'body': 'One fictional sentence cites two sources. [1] [2]'}
        result = assess_story(story, self.sources + [{'id': 'source-2',
                                                       'url': 'https://fictional.example.test/source-2',
                                                       'evidence_chars': 250}])
        self.assertEqual(result['cited_sentences'], 1)
        self.assertFalse(result['substantive'])

    def test_unresolved_citation_source_fails_even_when_two_facts_are_present(self):
        story = {**self.story,
                 'body': 'The review ended on May 12. [1] The order covered deposits. [2]'}
        result = assess_story(story, self.sources)
        self.assertFalse(result['substantive'])
        self.assertIn('1 cited sentence(s) reference unresolved sources', result['reasons'])

    def test_citation_source_without_a_resolvable_url_is_rejected(self):
        result = assess_story(self.story, [{'id': 'source-1', 'evidence_chars': 400}])
        self.assertFalse(result['substantive'])
        self.assertIn('2 cited sentence(s) reference unresolved sources', result['reasons'])

    def test_placeholder_text_is_never_counted_as_story_content(self):
        story = {**self.story, 'body': ('Fresh items were found, but none of 12 candidate(s) passed today. ' * 8) + '[1] [2]'}
        result = assess_story(story, self.sources)
        self.assertFalse(result['substantive'])
        self.assertIn('placeholder or no-candidate result', result['reasons'])

    def test_sections_without_story_identity_are_not_counted(self):
        story = {**self.story, 'cluster_id': None}
        accepted, rows = substantive_stories({'sections': [story]}, self.sources)
        self.assertEqual(accepted, [])
        self.assertIn('not a generated story item', rows[0]['reasons'])

    def test_inspect_rejects_three_story_edition_with_one_non_substantive_story(self):
        stories = [
            {**self.story, 'id': f'fictional-story-{index}',
             'cluster_id': f'fictional-cluster-{index}', 'heading': f'Fictional story {index}',
             'body': body}
            for index, body in enumerate((
                'A fictional review ended on May 12. [1] It covered the deposit service. [1]',
                'A fictional agreement closed on June 4. [1] It applied to transfer records. [1]',
                'A fictional review ended on June 8. [1]',
            ), 1)
        ]
        job = {'status': 'complete', 'edition_ready': {
            'edition': {'sections': stories}, 'sources': self.sources}}
        output = io.StringIO()
        with patch.object(acceptance_real_build, '_scorecard', return_value={}), \
             contextlib.redirect_stdout(output):
            passed = acceptance_real_build._inspect(Path('/tmp'), {'topics': []}, job, 0.5)

        self.assertFalse(passed)
        self.assertIn('Acceptance: 2 substantive stories; requires at least 3.', output.getvalue())
        self.assertIn('1 distinct cited sentence(s); requires 2', output.getvalue())

    def test_failed_build_artifact_keeps_only_sanitized_terminal_diagnostic(self):
        profile = {'version': 1, 'words': 'fictional profile', 'topics': []}
        job = {'status': 'failed', 'failure_reason': 'edition_writer:ValueError'}
        with tempfile.TemporaryDirectory() as temporary:
            state = Path(temporary) / 'state'
            state.mkdir()
            report = Path(temporary) / 'content.json'
            output = io.StringIO()
            with patch.object(acceptance_real_build, '_scorecard', return_value={}), \
                    contextlib.redirect_stdout(output):
                passed = acceptance_real_build._inspect(
                    state, profile, job, 0.5, content_report=report)
            artifact = json.loads(report.read_text(encoding='utf-8'))

        self.assertFalse(passed)
        self.assertEqual(artifact['diagnostic'], {
            'job_status': 'failed', 'failure_code': 'edition_writer:ValueError'})
        self.assertNotIn('failure_reason', artifact)

    def test_runner_uses_copied_state_and_polls_a_local_generation_server(self):
        profile = {'version': 1, 'words': 'fictional acceptance profile', 'minutes': 12, 'topics': []}
        body = ('A fictional office published a new service rule. [1] '
                'The rule applies to two local routes. [1]')
        edition = {'sections': [{'id': f'story-{index}', 'cluster_id': f'cluster-{index}',
                                 'heading': f'Fictional story {index}', 'body': body,
                                 'source_ids': ['source-1']} for index in range(1, 4)]}
        response = {'edition_ready': {'edition': edition,
                                      'sources': [{'id': 'source-1', 'title': 'Fictional source',
                                                   'url': 'https://example.test/story', 'evidence_chars': 400}]},
                    'status': 'complete', 'elapsed_seconds': 2.5}
        fake_server = '''
import json, os, sys
if os.environ.get('BRIEFING_REQUIRE_USAGE_BATCH') != '1':
    raise SystemExit('release batch guard was not enabled in preview process')
from http.server import BaseHTTPRequestHandler, HTTPServer
JOB = %r
PROFILE_WORDS = %r
class Handler(BaseHTTPRequestHandler):
    def send_json(self, status, value):
        data = json.dumps(value).encode()
        self.send_response(status); self.send_header('Content-Length', str(len(data)))
        self.end_headers(); self.wfile.write(data)
    def do_GET(self):
        if self.path == '/api/profile': self.send_json(200, {'profile': {'ok': True}})
        elif self.path == '/api/generate/fixture-job': self.send_json(200, JOB)
        else: self.send_json(404, {'error': 'missing'})
    def do_POST(self):
        payload = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        if payload.get('profile', {}).get('words') != PROFILE_WORDS:
            self.send_json(400, {'error': 'wrong-profile'})
        else: self.send_json(202, {'job_id': 'fixture-job', 'status': 'queued'})
    def log_message(self, *args): pass
HTTPServer((sys.argv[1], int(sys.argv[2])), Handler).serve_forever()
''' % (response, profile['words'])
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            state_source = root / 'source-state'
            state_source.mkdir()
            original_profile = json.dumps(profile)
            (state_source / 'profile.json').write_text(original_profile, encoding='utf-8')
            fake_repo = root / 'fake-repo'
            (fake_repo / 'dev').mkdir(parents=True)
            (fake_repo / 'dev' / 'preview_server.py').write_text(fake_server, encoding='utf-8')
            output = io.StringIO()
            with patch.object(acceptance_real_build, 'ROOT', fake_repo), contextlib.redirect_stdout(output):
                result = acceptance_real_build.run(state_source, timeout_seconds=3, poll_seconds=0.01)
            self.assertEqual(result, 0, output.getvalue())
            self.assertIn('Acceptance: 3 substantive stories', output.getvalue())
            self.assertIn('Full body:', output.getvalue())
            self.assertEqual((state_source / 'profile.json').read_text(encoding='utf-8'), original_profile)


if __name__ == '__main__':
    unittest.main()
