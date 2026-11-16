"""Keep quiet-topic notes out of the article and its exact narration script."""

import hashlib
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from dev.check_content import CONTENT, validate_content


class CanonicalEditionTests(unittest.TestCase):
    def test_current_daily_has_one_script_for_reader_and_narration(self):
        editions = json.loads((CONTENT / "editions.json").read_text())["editions"]
        daily = next(item for item in editions if item["kind"] == "daily")
        quiet = {row["topic_id"] for row in daily["coverage"] if row["state"] in {"quiet", "limited"}}
        self.assertEqual(len(daily["sections"]), 4)
        self.assertIn("sports", quiet)
        self.assertTrue(all(not set(section["topic_ids"]) <= quiet for section in daily["sections"]))
        self.assertEqual(daily["audio"]["script_hash"], daily["script_hash"])

    def test_validator_rejects_quiet_story_even_with_fresh_hash(self):
        with tempfile.TemporaryDirectory(prefix="briefing-quiet-validator-") as root:
            target = Path(root)
            for name in ("topics", "deep-dives", "sources", "editions"):
                shutil.copy2(CONTENT / f"{name}.json", target / f"{name}.json")
            path = target / "editions.json"
            data = json.loads(path.read_text())
            daily = next(item for item in data["editions"] if item["kind"] == "daily")
            daily["sections"].append({"id": "quiet-sports-test", "heading": "Quiet sports note",
                                      "body": "No event was selected.", "topic_ids": ["sports"], "source_ids": []})
            script = "\n\n".join(f"{item['heading']}\n{item['body']}" for item in daily["sections"])
            digest = hashlib.sha256(script.encode()).hexdigest()
            daily["script_hash"] = daily["audio"]["script_hash"] = digest
            path.write_text(json.dumps(data))
            with self.assertRaisesRegex(ValueError, "quiet topic belongs in coverage"):
                validate_content(target)


if __name__ == "__main__":
    unittest.main()
