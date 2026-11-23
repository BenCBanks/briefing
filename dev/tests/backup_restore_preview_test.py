import sys as _briefing_sys
from pathlib import Path as _BriefingPath
_briefing_sys.path.insert(0, str(_BriefingPath(__file__).resolve().parents[2]))
from dev.test_guard import enforce_test_environment as _enforce_test_environment
_enforce_test_environment()

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import socket
import subprocess
import tarfile
import tempfile
import time
import unittest
from urllib.error import HTTPError
from urllib.request import urlopen

from dev.backup_state import create_backup, verify_backup
from dev.ingest.paths import profile_identifier


ROOT = Path(__file__).resolve().parents[2]
LEGACY_FIXTURE = Path(__file__).resolve().parent / 'fixtures/legacy-state'
EPISODE_ID = 'a' * 32


def _write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True) + '\n', encoding='utf-8')


def _restore_verified_archive(archive_path, target):
    """Extract only verified regular files, stripping the archive's briefing/ root."""
    manifest = verify_backup(archive_path)
    target.mkdir(parents=True)
    with tarfile.open(archive_path, 'r:gz') as archive:
        for member in archive.getmembers():
            if member.name == 'backup-manifest.json':
                continue
            relative = PurePosixPath(member.name).parts[1:]
            destination = target.joinpath(*relative)
            destination.parent.mkdir(parents=True, exist_ok=True)
            stream = archive.extractfile(member)
            if stream is None:
                raise AssertionError(f'Verified member was unreadable: {member.name}')
            with destination.open('xb') as output:
                shutil.copyfileobj(stream, output)
    for relative, expected in manifest['files'].items():
        restored = target / relative
        data = restored.read_bytes()
        if len(data) != expected['size'] or hashlib.sha256(data).hexdigest() != expected['sha256']:
            raise AssertionError(f'Restored checksum mismatch: {relative}')
    return manifest


def _free_port():
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        return listener.getsockname()[1]


def _get_json(base, path):
    try:
        with urlopen(base + path, timeout=2) as response:
            return response.status, json.loads(response.read())
    except HTTPError as error:
        return error.code, json.loads(error.read())


class BackupRestorePreviewTests(unittest.TestCase):
    def test_verified_fixture_backup_restores_and_serves_preview_apis(self):
        with tempfile.TemporaryDirectory(prefix='briefing-backup-e2e-') as temporary:
            root = Path(temporary)
            source = root / 'briefing'
            backup_dir = root / 'backups'
            restore = root / 'restored-state'
            home = root / 'home'
            source.mkdir()
            home.mkdir()

            profile = json.loads((LEGACY_FIXTURE / 'profile.json').read_text(encoding='utf-8'))
            profile.update({'version': 1, 'minutes': 10, 'topics': [
                {'id': 'payments', 'label': 'Payments',
                 'scope': 'Fictional payment infrastructure',
                 'include': ['payment systems'], 'exclude': []},
            ]})
            profile_id = profile_identifier(profile)
            _write_json(source / 'profile.json', profile)
            current_source = LEGACY_FIXTURE / 'generated/current.json'
            current = json.loads(current_source.read_text(encoding='utf-8'))
            generated_dir = source / 'generated' / 'profiles' / profile_id
            _write_json(generated_dir / 'current.json', current)
            status = {'last_run_at': datetime.now(timezone.utc).isoformat(), 'ok': True,
                      'items_fetched': 3, 'stories_written': 2,
                      'feeds_disabled': [], 'reason': None}
            _write_json(source / 'status.json', status)
            episode = {
                'id': EPISODE_ID, 'title': 'Fictional restored episode',
                'topic': 'Fictional payments', 'requested_minutes': 3,
                'chapters': [{'id': 'chapter-1', 'title': 'Fixture chapter', 'sentences': [
                    {'text': 'This fictional chapter was restored from a verified backup.',
                     'source_numbers': [1]}]}],
                'sources': [{'id': 'fixture-source', 'outlet': 'Fictional Desk',
                             'title': 'Fixture source', 'url': 'https://example.invalid/story'}],
            }
            job = {
                'job_id': EPISODE_ID, 'profile_id': profile_id,
                'topic': 'Fictional payments', 'length': 3, 'style': 'explainer',
                'status': 'done', 'stage': 'done', 'audio_status': 'disabled',
                'created_at': datetime.now(timezone.utc).isoformat(),
                'updated_at': datetime.now(timezone.utc).isoformat(),
                'finished_at': datetime.now(timezone.utc).isoformat(),
                'counts': {'chapters': 1, 'sentences': 1, 'sources': 1, 'audio_seconds': 0},
                'episode': episode,
            }
            _write_json(source / 'episode-jobs' / 'profiles' / profile_id / f'{EPISODE_ID}.json', job)

            backup = create_backup(source, backup_dir,
                now=datetime(2026, 11, 19, 12, tzinfo=timezone.utc))
            self.assertTrue(backup['verified'])
            dry_target = root / 'dry-run-target'
            dry_run = subprocess.run(
                ['python3', str(ROOT / 'dev/backup_state.py'), '--restore-dry-run',
                 str(backup['archive']), '--restore-target', str(dry_target)],
                cwd=ROOT, env={**os.environ, 'HOME': str(home)},
                check=False, capture_output=True, text=True)
            self.assertEqual(dry_run.returncode, 0, dry_run.stderr)
            self.assertIn('Dry run only; no files were restored.', dry_run.stdout)
            self.assertFalse(dry_target.exists(), 'CLI dry run must not create its target')
            manifest = _restore_verified_archive(backup['archive'], restore)
            self.assertGreaterEqual(len(manifest['files']), 4)
            for relative, metadata in manifest['files'].items():
                original = source / relative
                restored = restore / relative
                self.assertEqual(hashlib.sha256(original.read_bytes()).hexdigest(), metadata['sha256'])
                self.assertEqual(hashlib.sha256(restored.read_bytes()).hexdigest(), metadata['sha256'])

            port = _free_port()
            base = f'http://127.0.0.1:{port}'
            env = os.environ.copy()
            env.update({
                'HOME': str(home), 'BRIEFING_STATE_DIR': str(restore),
                'BRIEFING_TESTING': '1', 'BRIEFING_EPISODES': 'fake',
                'BRIEFING_TEST_LEDGER_DIR': str(root / 'usage-ledger'),
                'PYTHONPATH': str(ROOT),
            })
            process = subprocess.Popen(
                ['python3', str(ROOT / 'dev/preview_server.py'), '127.0.0.1', str(port)],
                cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
            try:
                deadline = time.monotonic() + 10
                while True:
                    if process.poll() is not None:
                        error = process.stderr.read() if process.stderr else ''
                        self.fail(f'Fixture preview exited early ({process.returncode}): {error}')
                    try:
                        status_code, _ = _get_json(base, '/api/status')
                        if status_code == 200:
                            break
                    except (OSError, HTTPError, ValueError):
                        if time.monotonic() >= deadline:
                            error = process.stderr.read() if process.stderr else ''
                            self.fail(f'Fixture preview did not start: {error}')
                        time.sleep(0.05)

                status_code, profile_payload = _get_json(base, '/api/profile')
                self.assertEqual(status_code, 200, profile_payload)
                self.assertEqual(profile_payload['profile'], profile)

                status_code, edition_payload = _get_json(base, '/api/edition?profile=active&history=0')
                self.assertEqual(status_code, 200)
                self.assertTrue(edition_payload['generated'])
                self.assertEqual(edition_payload['edition']['id'], current['editions']['editions'][0]['id'])
                self.assertEqual(len(edition_payload['edition']['sections']), 2)

                status_code, episodes_payload = _get_json(base, '/api/episodes')
                self.assertEqual(status_code, 200)
                self.assertEqual([row['job_id'] for row in episodes_payload['jobs']], [EPISODE_ID])
                status_code, episode_payload = _get_json(base, f'/api/episodes/{EPISODE_ID}')
                self.assertEqual(status_code, 200)
                self.assertEqual(episode_payload['episode']['title'], 'Fictional restored episode')

                status_code, restored_status = _get_json(base, '/api/status')
                self.assertEqual(status_code, 200)
                self.assertEqual(restored_status['ok'], status['ok'])
                self.assertEqual(restored_status['stories_written'], status['stories_written'])
            finally:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
                if process.stderr:
                    process.stderr.close()

            for relative, metadata in manifest['files'].items():
                original = source / relative
                restored = restore / relative
                self.assertEqual(hashlib.sha256(original.read_bytes()).hexdigest(), metadata['sha256'])
                self.assertEqual(hashlib.sha256(restored.read_bytes()).hexdigest(), metadata['sha256'])


if __name__ == '__main__':
    unittest.main()
