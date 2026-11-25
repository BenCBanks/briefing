import hashlib
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from dev import accounts
from dev.tools import invites


class InviteToolTests(unittest.TestCase):
    def test_create_stores_only_hashes_and_prints_code_once(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {
            'BRIEFING_STATE_DIR': temp,
        }, clear=False):
            with patch('builtins.print') as output:
                codes = invites.create(2, 12)
            self.assertEqual(len(codes), 2)
            self.assertEqual(output.call_count, 0, 'library creation does not print secrets')
            stored = accounts._read('invites.json', {})
            self.assertEqual(set(stored), {hashlib.sha256(code.encode()).hexdigest() for code in codes})
            self.assertNotIn(codes[0], stored)

    def test_list_excludes_used_and_expired_and_revoke_is_digest_safe(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {
            'BRIEFING_STATE_DIR': temp,
        }, clear=False):
            codes = invites.create(2, 12)
            digest = hashlib.sha256(codes[0].encode()).hexdigest()
            stored = accounts._read('invites.json', {})
            stored[digest]['remaining'] = 0
            stored[hashlib.sha256(codes[1].encode()).hexdigest()]['expires'] = 1
            accounts._write('invites.json', stored)
            self.assertEqual(invites.list_unused(now=2**31), [])
            stored[hashlib.sha256(codes[1].encode()).hexdigest()]['expires'] = 2**31
            accounts._write('invites.json', stored)
            self.assertTrue(invites.revoke(codes[1]))
            self.assertFalse(invites.list_unused(now=2**30))
            self.assertFalse(invites.revoke('not-a-real-invite'))

    def test_bounds_reject_unreasonable_operator_requests(self):
        with self.assertRaises(ValueError):
            invites.create(0, 24)
        with self.assertRaises(ValueError):
            invites.create(1, 721)


if __name__ == '__main__':
    unittest.main()
