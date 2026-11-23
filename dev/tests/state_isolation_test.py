"""Guard against test suites resolving application state into the live tree."""

import sys as _briefing_sys
from pathlib import Path as _BriefingPath
_briefing_sys.path.insert(0, str(_BriefingPath(__file__).resolve().parents[2]))
from dev.test_guard import enforce_test_environment as _enforce_test_environment
_enforce_test_environment()

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import shutil
from unittest.mock import patch

from dev.ingest.paths import LIVE_STATE_DIR, checked_state_path

ROOT = Path(__file__).resolve().parents[2]


# Files a leaking test could clobber. The live service and the reader's phone legitimately
# touch other files (client-profiles/last_seen, ingested-items, feed-health), so only these
# are compared: comparing the whole tree made this test flaky whenever the live app was used.
_GUARDED = ('profile.json', 'flags.json', 'usage-ledger.json', 'status.json',
            'generated/current.json', 'generated/editions.json', 'generated/default/current.json',
            'generated/default/editions.json')


def _mtimes(root):
    result = {}
    for name in _GUARDED:
        path = root / name
        try:
            result[name] = path.stat().st_mtime_ns
        except FileNotFoundError:
            result[name] = None
    return result


class StateIsolationTests(unittest.TestCase):
    def test_test_mode_rejects_live_state_and_children(self):
        with self.assertRaisesRegex(RuntimeError, 'test state path resolves into live state'):
            checked_state_path(LIVE_STATE_DIR)
        with self.assertRaisesRegex(RuntimeError, 'test state path resolves into live state'):
            checked_state_path(LIVE_STATE_DIR / 'generated')

    def test_focused_suite_leaves_live_state_mtimes_unchanged(self):
        before = _mtimes(LIVE_STATE_DIR)
        with tempfile.TemporaryDirectory(prefix='briefing-isolated-test-') as temporary:
            root = Path(temporary)
            home = root / 'home'
            home.mkdir()
            env = dict(os.environ)
            env.update({'BRIEFING_TESTING': '1',
                        'BRIEFING_STATE_DIR': str(root / 'state'),
                        'BRIEFING_TEST_LEDGER_DIR': str(root / 'ledger'),
                        'BRIEFING_LIVE_STATE_DIR': str(LIVE_STATE_DIR),
                        'HOME': str(home), 'PYTHONPATH': str(Path(__file__).resolve().parents[2])})
            completed = subprocess.run(
                [sys.executable, '-m', 'unittest', '-q', 'dev.tests.flags_test'],
                cwd=Path(__file__).resolve().parents[2], env=env,
                capture_output=True, text=True, timeout=60, check=False)
            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        after = _mtimes(LIVE_STATE_DIR)
        self.assertEqual(after, before, 'focused fixture suite changed live state mtimes')

    def test_test_entrypoints_refuse_missing_or_live_state_environment(self):
        env = dict(os.environ)
        python_helper = [sys.executable, str(Path(__file__).with_name('flags_test.py'))]
        node = shutil.which('node')
        self.assertIsNotNone(node)
        node_helper = [node, str(Path(__file__).with_name('signals_routes.cjs'))]

        for label, helper in (('Python', python_helper), ('Node', node_helper)):
            unsafe_environments = []
            no_test_mode = dict(env, BRIEFING_STATE_DIR='/tmp/briefing-guard-safe')
            no_test_mode.pop('BRIEFING_TESTING', None)
            unsafe_environments.append(('missing-test-mode', no_test_mode,
                                        'set BRIEFING_TESTING=1'))
            no_state_dir = dict(env, BRIEFING_TESTING='1')
            no_state_dir.pop('BRIEFING_STATE_DIR', None)
            unsafe_environments.append(('missing-state-dir', no_state_dir,
                                        'set BRIEFING_STATE_DIR'))
            live_env = dict(env, BRIEFING_TESTING='1', BRIEFING_STATE_DIR=str(LIVE_STATE_DIR))
            unsafe_environments.append(('live-state', live_env,
                                        'BRIEFING_STATE_DIR must not overlap'))
            # '~' must expand to the REAL home for this case (the gate runs with a fake HOME).
            home_relative_live_env = dict(env, BRIEFING_TESTING='1',
                                          BRIEFING_STATE_DIR='~/.local/state/briefing',
                                          HOME=str(LIVE_STATE_DIR.parents[2]))
            unsafe_environments.append(('home-relative-live-state', home_relative_live_env,
                                        'BRIEFING_STATE_DIR must not overlap'))

            for environment, unsafe_env, expected_message in unsafe_environments:
                with self.subTest(helper=label, environment=environment):
                    result = subprocess.run(helper, cwd=ROOT, env=unsafe_env,
                                            capture_output=True, text=True, timeout=60)
                    self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                    self.assertIn('Refusing to run briefing tests', result.stderr)
                    self.assertIn(expected_message, result.stderr)

    def test_test_state_paths_reject_live_root_even_when_state_env_is_safe(self):
        with tempfile.TemporaryDirectory(prefix='briefing-safe-state-') as temporary:
            with patch.dict(os.environ, {
                    'BRIEFING_TESTING': '1', 'BRIEFING_STATE_DIR': temporary}):
                with self.assertRaisesRegex(RuntimeError, 'resolves into live state'):
                    checked_state_path(LIVE_STATE_DIR / 'profile.json')


if __name__ == '__main__':
    unittest.main()
