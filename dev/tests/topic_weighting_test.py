"""Fictional offline coverage for bounded five-minute topic allocation."""
import sys as _briefing_sys
from pathlib import Path as _BriefingPath
_briefing_sys.path.insert(0, str(_BriefingPath(__file__).resolve().parents[2]))
from dev.test_guard import enforce_test_environment as _enforce_test_environment
_enforce_test_environment()

import unittest

from dev.ingest import topic_weighting


FIVE_MINUTE_FIXTURE = {
    "fixture_only": True,
    "target_words": 750,
    "topics": [
        {"id": "payments", "weight": 1.5, "available_story_ids": ["pay-1", "pay-2", "pay-3"]},
        {"id": "health-policy", "weight": 0.5, "available_story_ids": ["health-1"]},
        {"id": "construction", "weight": 0.5, "available_story_ids": ["build-1", "build-2"]},
        {"id": "water", "weight": 0.5, "available_story_ids": ["water-1"]},
    ],
}


class TopicWeightingTests(unittest.TestCase):
    def test_uneven_five_minute_priority_keeps_exact_budget_and_each_available_topic(self):
        self.assertTrue(FIVE_MINUTE_FIXTURE["fixture_only"])
        topics = FIVE_MINUTE_FIXTURE["topics"]
        weights = {row["id"]: row["weight"] for row in topics}
        before = [list(row["available_story_ids"]) for row in topics]

        allocation = topic_weighting.allocate_words(topics, weights, FIVE_MINUTE_FIXTURE["target_words"])

        self.assertEqual(set(allocation), set(weights))
        self.assertTrue(all(value > 0 for value in allocation.values()))
        self.assertEqual(sum(allocation.values()), FIVE_MINUTE_FIXTURE["target_words"])
        self.assertLessEqual(max(allocation.values()), 375, "priority cannot take more than half the five-minute budget")
        self.assertGreater(allocation["payments"], allocation["health-policy"])
        self.assertEqual([row["available_story_ids"] for row in topics], before,
                         "allocation leaves the uneven fictional story supply untouched")

    def test_small_budget_is_not_padded_but_every_selected_topic_stays_in_report(self):
        topics = [{"id": topic_id} for topic_id in ("payments", "health-policy", "construction")]

        allocation = topic_weighting.allocate_words(topics, {}, total_words=2)
        report = topic_weighting.allocation_report(topics, {}, total_words=2)

        self.assertEqual(set(allocation), {row["id"] for row in topics})
        self.assertEqual(sum(allocation.values()), 2, "the requested word budget is never padded")
        self.assertEqual({row["topic_id"] for row in report["topics"]},
                         {row["id"] for row in topics}, "zero-word topics remain explicitly accounted for")


if __name__ == "__main__":
    unittest.main()
