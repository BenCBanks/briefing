import sys as _briefing_sys
from pathlib import Path as _BriefingPath
_briefing_sys.path.insert(0, str(_BriefingPath(__file__).resolve().parents[2]))
from dev.test_guard import enforce_test_environment as _enforce_test_environment
_enforce_test_environment()

from datetime import datetime, timedelta, timezone
import json
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
import tempfile
import unittest

from dev.tools.retention_cleanup import apply_cleanup, format_report, main, plan_cleanup

NOW = datetime(2026, 11, 18, 12, 0, tzinfo=timezone.utc)
PROFILE = 'fictional-profile'


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')


def bundle(when, edition_id):
    return {'editions': {'editions': [{'id': edition_id, 'kind': 'daily',
             'generated_at': when.isoformat(), 'local_date': when.date().isoformat(),
             'sections': [{'cluster_id': 'fictional-cluster'}]}]}, 'sources': {'sources': []}}


def make_state(root, *, saved_list=True):
    (root / 'cache').mkdir(parents=True, exist_ok=True)
    (root / 'cache' / 'fixture-cache.bin').write_bytes(b'fixture cache bytes')
    generated = root / 'generated/profiles' / PROFILE
    old = NOW - timedelta(days=45)
    recent = NOW - timedelta(days=5)
    current = NOW - timedelta(hours=1)
    write_json(generated / 'current.json', bundle(current, 'current-edition'))
    write_json(generated / 'editions-history.json', [bundle(old, 'old-edition'), bundle(recent, 'recent-edition')])
    write_json(generated / 'previous.json', bundle(old, 'previous-edition'))
    write_json(generated / 'previous-sources.json', {'sources': []})

    jobs = root / 'episode-jobs/profiles' / PROFILE
    write_json(jobs / 'expired-job.json', {'job_id': 'expired-job', 'created_at': (NOW - timedelta(days=100)).isoformat()})
    stream = jobs / '.expired-job-stream'
    stream.mkdir()
    (stream / 'chapter-one.wav').write_bytes(b'fixture partial chapter audio')
    write_json(jobs / 'recent-job.json', {'job_id': 'recent-job', 'created_at': (NOW - timedelta(days=10)).isoformat()})

    audio = root / 'episodes/profiles' / PROFILE / 'audio'
    for episode_id, when in (('expired-audio', NOW - timedelta(days=45)),
                             ('saved-audio', NOW - timedelta(days=60)),
                             ('recent-audio', NOW - timedelta(days=4)),
                             ('saved-recent', NOW - timedelta(days=4))):
        asset = audio / episode_id / ('a' * 64)
        write_json(asset / 'manifest.json', {'episode_id': episode_id, 'generated_at': when.isoformat()})
        (asset / 'episode.wav').write_bytes(b'fixture-audio-bytes')
    if saved_list:
        write_json(root / 'retention-saved-episodes.json',
                   {'profiles': {PROFILE: ['saved-audio', 'saved-recent']}})


class RetentionCleanupTests(unittest.TestCase):
    def test_cli_defaults_to_dry_run(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / 'state'
            make_state(root)
            output = StringIO()
            with redirect_stdout(output):
                code = main(['--state-dir', str(root), '--as-of', NOW.isoformat()])
            self.assertEqual(code, 0)
            self.assertIn('DRY RUN', output.getvalue())
            self.assertIn('No changes made', output.getvalue())
            self.assertTrue((root / 'episode-jobs/profiles' / PROFILE / 'expired-job.json').exists())

    def test_dry_run_reports_retention_candidates_and_never_changes_fixture_state(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / 'state'
            make_state(root)
            before = (root / 'generated/profiles' / PROFILE / 'editions-history.json').read_bytes()
            plan = plan_cleanup(root, now=NOW)
            report = '\n'.join(format_report(plan))
            self.assertEqual(plan['counts']['edition_records'], 1)
            self.assertEqual(plan['counts']['episode_files'], 1)
            self.assertEqual(plan['counts']['audio_assets'], 1)
            self.assertEqual(plan['counts']['audio_skipped_saved'], 1)
            self.assertGreater(plan['disk_usage']['state_total'], 0)
            self.assertEqual(plan['disk_usage']['caches'], len(b'fixture cache bytes'))
            self.assertIn('DRY RUN', report)
            self.assertIn('State breakdown: generated', report)
            self.assertIn('episode job records', report)
            self.assertIn('caches', report)
            self.assertIn('Suggestion: review eligible paths', report)
            self.assertIn('filesystem free', report)
            self.assertEqual((root / 'generated/profiles' / PROFILE / 'editions-history.json').read_bytes(), before)
            self.assertTrue((root / 'episode-jobs/profiles' / PROFILE / 'expired-job.json').exists())
            self.assertTrue((root / 'episode-jobs/profiles' / PROFILE / '.expired-job-stream').exists())
            self.assertTrue((root / 'episodes/profiles' / PROFILE / 'audio/expired-audio').exists())
            missing_saved_list = plan_cleanup(root, now=NOW,
                saved_ids_file=root / 'missing-saved-ids.json')
            self.assertIn('Suggestion: update the saved episode ID list',
                          '\n'.join(format_report(missing_saved_list)))

    def test_apply_prunes_old_records_and_preserves_current_recent_and_saved_audio(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / 'state'
            make_state(root)
            plan = plan_cleanup(root, now=NOW)
            result = apply_cleanup(plan)
            self.assertGreater(result['bytes_removed'], 0)
            self.assertEqual(result['stale_actions_skipped'], 0)
            history = json.loads((root / 'generated/profiles' / PROFILE / 'editions-history.json').read_text())
            self.assertEqual([row['editions']['editions'][0]['id'] for row in history], ['recent-edition'])
            self.assertTrue((root / 'generated/profiles' / PROFILE / 'current.json').exists())
            self.assertFalse((root / 'generated/profiles' / PROFILE / 'previous.json').exists())
            self.assertFalse((root / 'episode-jobs/profiles' / PROFILE / 'expired-job.json').exists())
            self.assertFalse((root / 'episode-jobs/profiles' / PROFILE / '.expired-job-stream').exists())
            audio = root / 'episodes/profiles' / PROFILE / 'audio'
            self.assertFalse((audio / 'expired-audio' / ('a' * 64)).exists())
            self.assertTrue((audio / 'saved-audio').exists())
            self.assertTrue((audio / 'recent-audio').exists())
            second = plan_cleanup(root, now=NOW)
            self.assertEqual(second['counts']['edition_records'], 0)
            self.assertEqual(second['counts']['episode_files'], 0)
            self.assertEqual(second['counts']['audio_assets'], 0)

    def test_missing_saved_ids_list_safely_holds_expired_audio(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / 'state'
            make_state(root, saved_list=False)
            plan = plan_cleanup(root, now=NOW)
            self.assertEqual(plan['counts']['audio_assets'], 0)
            self.assertEqual(plan['counts']['audio_skipped_unknown_saved_list'], 2)
            self.assertIn('missing or unreadable', plan['saved_list_warning'])
            self.assertTrue((root / 'episodes/profiles' / PROFILE / 'audio/expired-audio').exists())

    def test_symlinked_history_and_audio_are_never_followed(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / 'state'
            root.mkdir()
            outside = Path(temp) / 'outside.json'
            outside.write_text(json.dumps([bundle(NOW - timedelta(days=100), 'outside')]))
            history_dir = root / 'generated/profiles' / PROFILE
            history_dir.mkdir(parents=True)
            (history_dir / 'editions-history.json').symlink_to(outside)
            audio_parent = root / 'episodes/profiles' / PROFILE / 'audio'
            audio_parent.mkdir(parents=True)
            outside_audio = Path(temp) / 'outside-audio'
            outside_audio.mkdir()
            write_json(outside_audio / 'manifest.json', {'generated_at': (NOW - timedelta(days=90)).isoformat()})
            (audio_parent / 'linked-episode').symlink_to(outside_audio, target_is_directory=True)
            write_json(root / 'retention-saved-episodes.json', {'profiles': {PROFILE: []}})
            plan = plan_cleanup(root, now=NOW)
            self.assertEqual(plan['counts']['edition_records'], 0)
            self.assertEqual(plan['counts']['audio_assets'], 0)
            apply_cleanup(plan)
            self.assertTrue(outside.exists())
            self.assertTrue((outside_audio / 'manifest.json').exists())

    def test_apply_skips_a_file_modified_since_dry_run(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / 'state'
            make_state(root)
            plan = plan_cleanup(root, now=NOW)
            target = root / 'episode-jobs/profiles' / PROFILE / 'expired-job.json'
            target.write_text(json.dumps({'job_id': 'changed', 'created_at': NOW.isoformat()}))
            result = apply_cleanup(plan)
            self.assertGreaterEqual(result['stale_actions_skipped'], 1)
            self.assertTrue(target.exists())


if __name__ == '__main__':
    unittest.main()
