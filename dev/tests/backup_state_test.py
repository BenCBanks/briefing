import sys as _briefing_sys
from pathlib import Path as _BriefingPath
_briefing_sys.path.insert(0, str(_BriefingPath(__file__).resolve().parents[2]))
from dev.test_guard import enforce_test_environment as _enforce_test_environment
_enforce_test_environment()

from datetime import datetime, timedelta, timezone
from contextlib import redirect_stdout
from io import BytesIO
from io import StringIO
from pathlib import Path
import tempfile
import tarfile
import unittest

from dev.backup_state import BackupError, create_backup, main, restore_dry_run, verify_backup


class BackupStateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / 'briefing'
        self.destination = self.root / 'archives'
        (self.source / 'profiles' / 'fictional').mkdir(parents=True)
        (self.source / 'profiles' / 'fictional' / 'profile.json').write_text(
            '{"topic":"fiction"}', encoding='utf-8')

    def tearDown(self):
        self.temp.cleanup()

    def test_backup_is_verified_excludes_transient_caches_and_prunes_to_retention(self):
        (self.source / 'cache').mkdir()
        (self.source / 'cache' / 'discard.json').write_text('temporary', encoding='utf-8')
        (self.source / 'profiles' / 'fictional' / 'compiled.pyc').write_bytes(b'cache')
        outside = self.root / 'outside-secret.txt'
        outside.write_text('must not follow a symlink', encoding='utf-8')
        (self.source / 'outside-link').symlink_to(outside)

        first = create_backup(self.source, self.destination,
                              now=datetime(2026, 11, 18, tzinfo=timezone.utc), keep=14)
        self.assertTrue(first['verified'])
        manifest = verify_backup(first['archive'])
        self.assertEqual(set(manifest['files']), {'profiles/fictional/profile.json'})
        self.assertEqual(first['files'], 1)

        for index in range(1, 15):
            create_backup(self.source, self.destination,
                          now=datetime(2026, 11, 18, tzinfo=timezone.utc) + timedelta(minutes=index),
                          keep=14)
        retained = sorted(self.destination.glob('briefing-state-*.tar.gz'))
        self.assertEqual(len(retained), 14)
        self.assertNotIn(first['archive'], retained)

    def test_restore_dry_run_checks_integrity_and_reports_add_replace_without_writing(self):
        result = create_backup(self.source, self.destination,
                               now=datetime(2026, 11, 18, tzinfo=timezone.utc))
        target = self.root / 'restore-target'
        profile = target / 'profiles' / 'fictional' / 'profile.json'
        profile.parent.mkdir(parents=True)
        profile.write_text('{"topic":"old"}', encoding='utf-8')
        extra = target / 'keep-me.txt'
        extra.write_text('target-only', encoding='utf-8')

        preview = restore_dry_run(result['archive'], target)
        self.assertTrue(preview['verified'])
        self.assertEqual(preview['changes'], [
            {'path': 'profiles/fictional/profile.json', 'action': 'replace'}])
        self.assertEqual(profile.read_text(encoding='utf-8'), '{"topic":"old"}')
        self.assertEqual(extra.read_text(encoding='utf-8'), 'target-only')
        output = StringIO()
        with redirect_stdout(output):
            status = main(['--restore-dry-run', str(result['archive']), '--restore-target', str(target)])
        self.assertEqual(status, 0)
        self.assertIn('Dry run only; no files were restored.', output.getvalue())
        self.assertEqual(profile.read_text(encoding='utf-8'), '{"topic":"old"}')

        corrupt = self.destination / 'tampered.tar.gz'
        with tarfile.open(result['archive'], 'r:gz') as source, tarfile.open(corrupt, 'w:gz') as output:
            for member in source.getmembers():
                stream = source.extractfile(member)
                data = stream.read() if stream else b''
                if member.name.startswith('briefing/'):
                    data += b'changed'
                updated = tarfile.TarInfo(member.name)
                updated.mode = member.mode
                updated.size = len(data)
                output.addfile(updated, BytesIO(data))
        with self.assertRaisesRegex(BackupError, 'integrity check failed'):
            restore_dry_run(corrupt, target)

    def test_unsafe_manifest_path_is_rejected(self):
        result = create_backup(self.source, self.destination,
                               now=datetime(2026, 11, 18, tzinfo=timezone.utc))
        archive = result['archive']
        bad = self.destination / 'unsafe.tar.gz'
        with tarfile.open(archive, 'r:gz') as source, tarfile.open(bad, 'w:gz') as output:
            for member in source.getmembers():
                stream = source.extractfile(member)
                data = stream.read() if stream else b''
                if member.name == 'backup-manifest.json':
                    import json
                    manifest = json.loads(data.decode())
                    manifest['files']['../outside'] = {'sha256': '0' * 64, 'size': 0}
                    data = (json.dumps(manifest) + '\n').encode()
                updated = tarfile.TarInfo(member.name)
                updated.mode = member.mode
                updated.size = len(data)
                output.addfile(updated, BytesIO(data))
        with self.assertRaisesRegex(BackupError, 'unsafe path'):
            verify_backup(bad)


if __name__ == '__main__':
    unittest.main()
