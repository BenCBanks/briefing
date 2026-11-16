"""Guard narration export against selecting a superseded or ambiguous revision."""

import json
import tempfile
import unittest
from pathlib import Path

from dev import check_content, export_narration


class NarrationRevisionSelection(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.content = Path(self.temp.name)
        self.original_content = export_narration.CONTENT
        self.original_check_content = check_content.CONTENT
        export_narration.CONTENT = self.content
        check_content.CONTENT = self.content

    def tearDown(self):
        export_narration.CONTENT = self.original_content
        check_content.CONTENT = self.original_check_content
        self.temp.cleanup()

    def write_items(self, editions):
        (self.content / "editions.json").write_text(json.dumps({"editions": editions}))
        (self.content / "deep-dives.json").write_text(json.dumps({"deep_dives": []}))

    def test_chooses_current_revision_even_when_old_one_comes_first(self):
        old = {"id": "daily-1", "revision_id": "r1", "title": "Old"}
        current = {"id": "daily-1", "revision_id": "r2", "title": "Current",
                   "corrections": [{"previous_revision_id": "r1"}]}
        self.write_items([old, current])
        self.assertEqual(export_narration.find_item("daily-1")["title"], "Current")
        self.assertEqual(len(check_content.rows("editions", "editions", revisioned=True)), 2)

    def test_rejects_two_plausible_current_revisions(self):
        self.write_items([{"id": "daily-1", "revision_id": "r1"},
                          {"id": "daily-1", "revision_id": "r2"}])
        with self.assertRaisesRegex(ValueError, "exactly one current revision"):
            export_narration.find_item("daily-1")
        with self.assertRaisesRegex(ValueError, "ambiguous current revisions"):
            check_content.rows("editions", "editions", revisioned=True)

    def test_rejects_duplicate_revision_identifier(self):
        self.write_items([{"id": "daily-1", "revision_id": "r1"},
                          {"id": "daily-1", "revision_id": "r1"}])
        with self.assertRaisesRegex(ValueError, "duplicate revisions"):
            export_narration.find_item("daily-1")
        with self.assertRaisesRegex(ValueError, "duplicate item revision"):
            check_content.rows("editions", "editions", revisioned=True)

    def test_rejects_withdrawn_revisions_without_a_current_replacement(self):
        self.write_items([{"id": "daily-1", "revision_id": "r1", "status": "withdrawn"}])
        with self.assertRaisesRegex(ValueError, "exactly one current revision"):
            export_narration.find_item("daily-1")
        self.assertEqual(len(check_content.rows("editions", "editions", revisioned=True)), 1,
                         "A withdrawn item may remain in the pack as an explanatory record")


if __name__ == "__main__":
    unittest.main()
