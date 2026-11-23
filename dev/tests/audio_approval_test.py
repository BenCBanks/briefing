import sys as _briefing_sys
from pathlib import Path as _BriefingPath
_briefing_sys.path.insert(0, str(_BriefingPath(__file__).resolve().parents[2]))
from dev.test_guard import enforce_test_environment as _enforce_test_environment
_enforce_test_environment()

import os
import tempfile
import unittest
from unittest.mock import patch

from dev.ingest.audio_approval import (
    issue_fictional_listening_approval_for_test,
    validate_trusted_listening_approval,
)


def _identity(profile_id='a' * 24):
    return {
        'profile_id': profile_id,
        'episode_id': 'fictional-episode',
        'revision_id': 'fictional-revision',
        'script_sha256': 'b' * 64,
        'candidate_batch_id': 'fictional-batch',
        'asset_key': 'c' * 64,
        'voice_id': 'fictional-voice',
        'provider_id': 'fictional-provider',
        'model_id': 'fictional-model',
        'normalizer_id': 'fictional-normalizer',
        'tts_output_token_limit': 100,
        'max_segment_attempts': 2,
    }


class AudioApprovalTests(unittest.TestCase):
    def test_fictional_issuer_requires_isolated_test_context_and_binds_profile(self):
        identity = _identity()
        with tempfile.TemporaryDirectory() as temporary:
            with patch.dict(os.environ, {
                'BRIEFING_TESTING': '1',
                'BRIEFING_STATE_DIR': temporary,
            }, clear=True):
                approval = issue_fictional_listening_approval_for_test(identity)
                self.assertTrue(validate_trusted_listening_approval(approval, identity))
                self.assertEqual(approval.identity['profile_id'], identity['profile_id'])
                with self.assertRaisesRegex(ValueError, 'does not match'):
                    validate_trusted_listening_approval(
                        approval, {**identity, 'profile_id': 'd' * 24})

            with patch.dict(os.environ, {}, clear=True):
                with self.assertRaisesRegex(RuntimeError, 'isolated test environment'):
                    issue_fictional_listening_approval_for_test(identity)


if __name__ == '__main__':
    unittest.main()
