"""Keep quiet-topic notes out of the article and its exact narration script."""

import sys as _briefing_sys
from pathlib import Path as _BriefingPath
_briefing_sys.path.insert(0, str(_BriefingPath(__file__).resolve().parents[2]))
from dev.test_guard import enforce_test_environment as _enforce_test_environment
_enforce_test_environment()

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
            self._fill_missing_explanations(target)
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

    @staticmethod
    def _fill_missing_explanations(content_dir):
        topics = json.loads((content_dir / "topics.json").read_text())["topics"]
        topic_ids = {row["id"] for row in topics}
        dives_path = content_dir / "deep-dives.json"
        data = json.loads(dives_path.read_text())
        covered = {row["topic_id"] for row in data["deep_dives"]}
        for topic in topics:
            if topic["id"] in covered:
                continue
            revision = f"fixture-{topic['id']}-r1"
            heading, body = "Fictional overview", "A fictional example explains the general topic without reporting a real event."
            script_hash = hashlib.sha256(f"{heading}\n{body}".encode()).hexdigest()
            data["deep_dives"].append({
                "id": f"fixture-dive-{topic['id']}", "topic_id": topic["id"],
                "revision_id": revision, "script_hash": script_hash,
                "audio": {"state": "pending", "revision_id": revision,
                          "script_hash": script_hash},
                "sections": [{"id": f"fixture-section-{topic['id']}", "heading": heading,
                              "body": body, "source_ids": []}],
            })
        dives_path.write_text(json.dumps(data))

        editions_path = content_dir / "editions.json"
        editions = json.loads(editions_path.read_text())
        for edition in editions["editions"]:
            covered = {row["topic_id"] for row in edition["coverage"]}
            edition["coverage"].extend(
                {"topic_id": topic_id, "state": "context", "reason": "Fictional fixture coverage"}
                for topic_id in sorted(topic_ids - covered)
            )
        editions_path.write_text(json.dumps(editions))


if __name__ == "__main__":
    unittest.main()
