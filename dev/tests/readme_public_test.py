import sys as _briefing_sys
from pathlib import Path as _BriefingPath
_briefing_sys.path.insert(0, str(_BriefingPath(__file__).resolve().parents[2]))
from dev.test_guard import enforce_test_environment as _enforce_test_environment
_enforce_test_environment()

import re
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]
README = ROOT / 'README.md'


class PublicReadmeTests(unittest.TestCase):
    def test_relative_markdown_links_resolve_inside_repository(self):
        content = README.read_text(encoding='utf-8')
        links = re.findall(r'(?<!!)\[[^\]]+\]\(([^)]+)\)', content)
        self.assertTrue(links, 'README should keep discoverable documentation links')
        for target in links:
            with self.subTest(target=target):
                self.assertNotRegex(target, r'^(?:https?:)?//')
                relative = target.split('#', 1)[0]
                self.assertTrue(relative, 'relative links need a target')
                resolved = (ROOT / relative).resolve()
                self.assertTrue(resolved.is_relative_to(ROOT), 'link must stay inside the repository')
                self.assertTrue(resolved.exists(), 'linked file or directory must exist')

    def test_readme_avoids_machine_paths_and_personal_identifiers(self):
        content = README.read_text(encoding='utf-8').lower()
        for private_marker in ('/home/', '/users/', '~/pigserver', 'bencbanks', 'ben banks'):
            with self.subTest(marker=private_marker):
                self.assertNotIn(private_marker, content)


if __name__ == '__main__':
    unittest.main()
