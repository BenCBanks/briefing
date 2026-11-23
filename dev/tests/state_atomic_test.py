import sys as _briefing_sys
from pathlib import Path as _BriefingPath
_briefing_sys.path.insert(0, str(_BriefingPath(__file__).resolve().parents[2]))
from dev.test_guard import enforce_test_environment as _enforce_test_environment
_enforce_test_environment()

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from dev.ingest.generation_api import _atomic_json
from dev.ingest.episodes_audio import _atomic_json as _atomic_episode_json
from dev.ingest.state_io import classify_exact_bytes_state, serialize_json_bytes


class AtomicStateWriteTests(unittest.TestCase):
    def test_interrupted_replace_preserves_previous_state_and_edition(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for name, old_value, new_value in (
                    ('status.json', {'ok': True}, {'ok': False}),
                    ('generated/default/current.json', {'edition': 'old'}, {'edition': 'new'}),
                    ('episodes/episode-a/episode.json', {'revision': 'old'}, {'revision': 'new'})):
                with self.subTest(name=name):
                    target = root / name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text(json.dumps(old_value), encoding='utf-8')
                    with patch('dev.ingest.state_io.os.replace', side_effect=OSError('simulated interruption')):
                        with self.assertRaisesRegex(OSError, 'simulated interruption'):
                            writer = (_atomic_episode_json if name.startswith('episodes/') else _atomic_json)
                            writer(target, new_value)
                    self.assertEqual(json.loads(target.read_text(encoding='utf-8')), old_value)
                    self.assertEqual(list(target.parent.glob(f'.{target.name}.*.tmp')), [])

    def test_success_replaces_json_with_private_file_permissions(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / 'profile.json'
            _atomic_json(target, {'topics': ['fictional-topic']})
            self.assertEqual(json.loads(target.read_text(encoding='utf-8')),
                             {'topics': ['fictional-topic']})
            self.assertEqual(target.stat().st_mode & 0o777, 0o600)

    def test_serialized_state_bytes_keep_existing_json_format(self):
        value = {'episode': 'fictional', 'unicode': 'café'}
        self.assertEqual(
            serialize_json_bytes(value, ensure_ascii=False, indent=None,
                                 separators=(',', ':')),
            (json.dumps(value, ensure_ascii=False, indent=None,
                        separators=(',', ':')) + '\n').encode('utf-8'))

    def test_exact_job_state_classifier_requires_distinct_old_and_new_hashes(self):
        old = b'{"status":"pending"}\n'
        new = b'{"status":"recorded"}\n'
        old_hash = __import__('hashlib').sha256(old).hexdigest()
        new_hash = __import__('hashlib').sha256(new).hexdigest()
        self.assertEqual(classify_exact_bytes_state(old, old_sha256=old_hash,
                                                    new_sha256=new_hash), 'old')
        self.assertEqual(classify_exact_bytes_state(new, old_sha256=old_hash,
                                                    new_sha256=new_hash), 'new')
        self.assertEqual(classify_exact_bytes_state(b'other', old_sha256=old_hash,
                                                    new_sha256=new_hash), 'third')
        with self.assertRaisesRegex(ValueError, 'distinct hashes'):
            classify_exact_bytes_state(old, old_sha256=old_hash, new_sha256=old_hash)


if __name__ == '__main__':
    unittest.main()
