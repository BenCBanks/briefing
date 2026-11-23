"""Fictional, offline coverage for deterministic episode-script checks."""
import sys as _briefing_sys
from pathlib import Path as _BriefingPath
_briefing_sys.path.insert(0, str(_BriefingPath(__file__).resolve().parents[2]))
from dev.test_guard import enforce_test_environment as _enforce_test_environment
_enforce_test_environment()

import json
from copy import deepcopy
from pathlib import Path
import unittest

from dev.ingest.episodes.script_checks import (check_episode_script, score_episode_quality,
                                                score_episode_script,
                                                score_weekly_recap_listening)

FIXTURES = Path(__file__).parent / "fixtures"
EDITION_PACK = Path(__file__).resolve().parents[2] / "content/editions.json"


def fixture(name):
    return json.loads((FIXTURES / name).read_text())


def five_minute_case():
    data = fixture("episode-quality-five-minute.json")
    episode = {
        "requested_minutes": data["requested_minutes"],
        "could_not_confirm": [],
        "chapters": [{"title": row["title"], "sentences": [
            {"text": text, "kind": "claim", "evidence_ids": [row["evidence_id"]]}
            for text in row["sentences"]
        ]} for row in data["chapters"]],
    }
    evidence = {row["evidence_id"]: {"text": " ".join(row["sentences"])}
                for row in data["chapters"]}
    return episode, data["research_plan"], evidence


class EpisodeScriptChecksTests(unittest.TestCase):
    @staticmethod
    def fictional_weekly_recap():
        return {
            "kind": "weekly",
            "evidence_limited": True,
            "evidence_limit_note": "Only these fictional reports were available for the recap.",
            "sections": [
                {"heading": "A higher pump", "body": (
                    "Here's your ten-minute weekly briefing about fictional coastal repair planning. "
                    "Harbor staff moved a temporary pump above the old market after floodwater reached "
                    "the storage room during a late spring storm. That change keeps the electrical panel "
                    "clear, while residents still need a marked route to the higher entrance.")},
                {"heading": "Mark the route", "body": (
                    "Turning to the marked route. County crews added reflective signs at both ends of "
                    "the uphill path after evening volunteers reported difficulty finding it. Library "
                    "staff now keep printed directions by the front desk for visitors who arrive after "
                    "the gate is closed.")},
                {"heading": "Inspect the connection", "body": (
                    "Next, the repair schedule. Public works will inspect the pump connection before "
                    "the next heavy rain and share the result at its monthly meeting. The higher pump, "
                    "marked route, and reflective signs give residents clearer steps during later storms.")},
            ],
        }

    def test_weekly_recap_scope_closing_and_evidence_limited_length(self):
        edition = self.fictional_weekly_recap()
        report = score_weekly_recap_listening(edition)
        checks = {row["name"]: row["passed"] for row in report["checks"]}
        self.assertTrue(report["passed"], report)
        self.assertTrue(checks["weekly_scope"], report)
        self.assertTrue(checks["weekly_closing_grounded"], report)
        self.assertTrue(checks["weekly_duration"], report)
        self.assertTrue(checks["no_banned_filler"], report)
        self.assertLess(report["word_count"], 300, report)
        self.assertTrue(any("evidence_limited_shorter_recap" in row for row in report["warnings"]), report)

        missing_scope = deepcopy(edition)
        missing_scope["sections"][0]["body"] = missing_scope["sections"][0]["body"].replace(" weekly", "", 1)
        report = score_weekly_recap_listening(missing_scope)
        self.assertFalse(next(row for row in report["checks"] if row["name"] == "weekly_scope")["passed"], report)

        repeated_close = deepcopy(edition)
        earlier_claim = repeated_close["sections"][0]["body"].split(". ")[1]
        repeated_close["sections"][-1]["body"] = repeated_close["sections"][-1]["body"].rsplit(". ", 1)[0] + ". " + earlier_claim
        report = score_weekly_recap_listening(repeated_close)
        self.assertFalse(next(row for row in report["checks"] if row["name"] == "weekly_closing_grounded")["passed"], report)

        invented_close = deepcopy(edition)
        invented_close["sections"][-1]["body"] = invented_close["sections"][-1]["body"].rsplit(". ", 1)[0] + ". A new ferry terminal will open beside the mountain observatory after an unexpected election."
        report = score_weekly_recap_listening(invented_close)
        self.assertFalse(next(row for row in report["checks"] if row["name"] == "weekly_closing_grounded")["passed"], report)

        unmarked_short = deepcopy(edition)
        unmarked_short.pop("evidence_limited")
        unmarked_short.pop("evidence_limit_note")
        report = score_weekly_recap_listening(unmarked_short)
        self.assertFalse(next(row for row in report["checks"] if row["name"] == "weekly_duration")["passed"], report)

    def test_weekly_source_from_prior_week_keeps_its_truthful_spoken_date(self):
        edition = self.fictional_weekly_recap()
        edition["week_start"] = "2026-11-16"
        edition["sections"][1]["source_published_at"] = "2026-11-09"
        edition["sections"][1]["body"] = (
            "Turning to the library repair. The fictional library opened its repair desk on November ninth "
            "for residents seeking help with the marked route.")

        report = score_weekly_recap_listening(edition)
        check = next(row for row in report["checks"] if row["name"] == "weekly_source_date_truth")
        self.assertTrue(check["passed"], report)

        invented_date = deepcopy(edition)
        invented_date["sections"][1]["body"] = invented_date["sections"][1]["body"].replace(
            "November ninth", "November seventeenth")
        report = score_weekly_recap_listening(invented_date)
        check = next(row for row in report["checks"] if row["name"] == "weekly_source_date_truth")
        self.assertFalse(check["passed"], report)

        current_week_claim = deepcopy(edition)
        current_week_claim["sections"][1]["body"] = current_week_claim["sections"][1]["body"].replace(
            "on November ninth", "this week")
        report = score_weekly_recap_listening(current_week_claim)
        check = next(row for row in report["checks"] if row["name"] == "weekly_source_date_truth")
        self.assertFalse(check["passed"], report)

    def test_weekly_recap_listening_check_covers_all_pack_scripts(self):
        editions = json.loads(EDITION_PACK.read_text())["editions"]
        weekly = [edition for edition in editions if edition.get("kind") == "weekly"]
        self.assertTrue(weekly, "the fictional content pack includes a weekly recap script")
        for edition in weekly:
            with self.subTest(edition=edition["id"]):
                report = score_weekly_recap_listening(edition)
                self.assertTrue(report["passed"], report)
                self.assertGreaterEqual(report["estimated_minutes"], 8.1, report)
                self.assertLessEqual(report["estimated_minutes"], 10, report)
                self.assertEqual(report["target_words"], 1350)

    def test_weekly_recap_lint_rejects_repeated_openers_filler_and_wrong_length(self):
        editions = json.loads(EDITION_PACK.read_text())["editions"]
        edition = deepcopy(next(row for row in editions if row.get("kind") == "weekly"))
        edition["sections"][1]["body"] = "For context, the fictional service confirms a clear next step."
        edition["sections"][2]["body"] = "For context, another fictional service confirms a separate next step."
        edition["sections"][3]["body"] = "The source does not say when that review will happen."
        edition["sections"][4]["body"] = "The review will continue, so watch this space."
        edition["sections"][5]["body"] = "A short closing fact remains."
        report = score_weekly_recap_listening(edition)
        checks = {row["name"]: row["passed"] for row in report["checks"]}
        self.assertFalse(checks["weekly_duration"], report)
        self.assertFalse(checks["host_formula_variety"], report)
        self.assertFalse(checks["no_source_absence_filler"], report)
        self.assertFalse(checks["concrete_segment_endings"], report)

    def test_quality_rubric_passes_grounded_spoken_script(self):
        data = fixture("episode-quality-good.json")
        report = score_episode_quality(data["episode"], data["research_plan"], data["evidence"])
        self.assertTrue(report["passed"], report)
        self.assertEqual(report["score"], 8)
        self.assertEqual(report["issues"], [])

    def test_quality_rubric_lists_specific_issues_for_bad_script(self):
        data = fixture("episode-quality-bad.json")
        report = score_episode_quality(data["episode"], data["research_plan"], data["evidence"])
        self.assertFalse(report["passed"], report)
        self.assertTrue(report["partial"])
        self.assertGreaterEqual(report["score"], 0)
        self.assertTrue({"opening_hook", "chapter_answers_question", "numbers_sourced",
                         "no_cross_chapter_repetition", "spoken_style", "no_list_reading",
                         "honest_uncertainty"}.issubset(report["issues"]))

    def test_practical_certainty_cannot_borrow_an_unrelated_future_statement(self):
        data = fixture("episode-quality-good.json")
        baseline = score_episode_quality(data["episode"], data["research_plan"], data["evidence"])
        self.assertTrue(baseline["passed"], baseline)
        episode = deepcopy(data["episode"])
        episode["chapters"][0]["sentences"][0]["text"] = (
            "If residents enter the code, the locker will definitely eliminate missed deliveries for everyone.")

        claim = episode["chapters"][0]["sentences"][0]["text"]
        for extra_evidence in ("", " The town will review the trial before deciding whether to extend it."):
            with self.subTest(extra_evidence=extra_evidence):
                evidence = deepcopy(data["evidence"])
                evidence["e1"]["text"] += extra_evidence
                report = score_episode_quality(episode, data["research_plan"], evidence)
                self.assertEqual(report["issues"], ["honest_uncertainty"], report)
                self.assertGreaterEqual(report["score"], report["bar"], report)
                self.assertFalse(report["passed"], report)
                self.assertTrue(report["partial"], report)

        # Supported certainty and equivalent wording remain eligible; this lint
        # does not replace the independent claim/evidence semantic verifier.
        for supported in (claim, claim.replace("definitely", "certainly")):
            with self.subTest(supported=supported):
                evidence = deepcopy(data["evidence"])
                evidence["e1"]["text"] += " " + supported
                report = score_episode_quality(episode, data["research_plan"], evidence)
                self.assertTrue(report["passed"], report)
                self.assertEqual(report["issues"], [])

    def test_unresolved_question_needs_spoken_disclosure_not_writer_metadata(self):
        data = fixture("episode-quality-good.json")
        episode = deepcopy(data["episode"])
        episode["could_not_confirm"] = ["Will the fictional trial continue?"]
        episode["uncertainty_acknowledged"] = True

        report = score_episode_quality(episode, data["research_plan"], data["evidence"])

        self.assertFalse(report["passed"], report)
        self.assertIn("honest_uncertainty", report["issues"])

    def test_quality_uncertain_will_preserves_supported_unresolved_outcome(self):
        data = fixture("episode-quality-good.json")
        data["evidence"]["e2"]["text"] = (
            "Library staff record missed collections and resident feedback during the trial. "
            "The town has not decided whether the locker trial should continue.")
        data["episode"]["chapters"][1]["sentences"][1]["text"] = (
            "The town has not decided whether the locker trial will continue.")
        report = score_episode_quality(data["episode"], data["research_plan"], data["evidence"])
        self.assertTrue(report["passed"], report)
        self.assertEqual(report["issues"], [])

    def test_quality_navigation_will_needs_no_factual_evidence(self):
        data = fixture("episode-quality-good.json")
        data["episode"]["chapters"][1]["sentences"].insert(0, {
            "text": "Next, we will look at the library review.",
            "kind": "navigation", "evidence_ids": [],
        })
        report = score_episode_quality(data["episode"], data["research_plan"], data["evidence"])
        self.assertTrue(report["passed"], report)
        self.assertEqual(report["issues"], [])

    def test_quality_supported_schedule_can_use_future_paraphrase(self):
        data = fixture("episode-quality-good.json")
        data["evidence"]["e2"]["text"] = (
            "Library staff record missed collections and resident feedback during the trial. "
            "The town's published schedule sets the locker trial review for Monday.")
        data["episode"]["chapters"][1]["sentences"][1]["text"] = (
            "Under the published schedule, the town will review the locker trial on Monday.")
        report = score_episode_quality(data["episode"], data["research_plan"], data["evidence"])
        self.assertTrue(report["passed"], report)
        self.assertEqual(report["issues"], [])

    def test_calibrated_five_minute_fixture_meets_documented_threshold(self):
        episode, plan, evidence = five_minute_case()
        report = score_episode_quality(episode, plan, evidence)
        self.assertTrue(report["passed"], report)
        self.assertGreaterEqual(report["score"], 7)
        self.assertEqual(report["bar"], 7)
        self.assertEqual(report["out_of"], 9)
        duration = next(row for row in report["checks"] if row["name"] == "requested_duration")
        self.assertTrue(duration["passed"], duration)

    def test_sensitive_topic_framing_fixtures_reject_sensational_and_unattributed_claims(self):
        cases = fixture("sensitive-topic-framing.json")["cases"]
        baseline = fixture("episode-quality-good.json")
        for case in cases:
            for field, expected in (("neutral", True), ("attributed", True),
                                    ("sensational", False), ("unattributed", False)):
                with self.subTest(topic=case["topic"], framing=field):
                    episode = deepcopy(baseline["episode"])
                    episode["topic"] = case["topic"]
                    episode["chapters"][0]["sentences"][0]["text"] = case[field]
                    report = score_episode_quality(episode, baseline["research_plan"],
                                                   baseline["evidence"])
                    framing = next(row for row in report["checks"]
                                   if row["name"] == "sensitive_topic_framing")
                    self.assertEqual(framing["passed"], expected, framing)
                    if field == "sensational":
                        self.assertIn("sensational wording", framing["detail"])
                    if field == "unattributed":
                        self.assertIn("unattributed contested claim", framing["detail"])

    def test_quality_rejects_generic_setup_before_the_direct_answer(self):
        baseline = fixture("episode-quality-good.json")
        episode = deepcopy(baseline["episode"])
        episode["chapters"][0]["sentences"][0]["text"] = (
            "This trial has several moving parts for residents to consider.")
        report = score_episode_quality(episode, baseline["research_plan"], baseline["evidence"])
        opening = next(row for row in report["checks"] if row["name"] == "opening_hook")
        self.assertFalse(opening["passed"], report)
        self.assertIn("direct answer", opening["detail"])

        positive = deepcopy(baseline["episode"])
        positive["chapters"][0]["sentences"][0]["text"] = (
            "There are three supported steps for collecting parcels from Alder Bay lockers.")
        positive_evidence = deepcopy(baseline["evidence"])
        positive_evidence["e1"]["text"] = (
            "There are three supported steps for collecting parcels from Alder Bay lockers. "
            + positive_evidence["e1"]["text"])
        positive_report = score_episode_quality(
            positive, baseline["research_plan"], positive_evidence)
        positive_opening = next(row for row in positive_report["checks"]
                               if row["name"] == "opening_hook")
        self.assertTrue(positive_opening["passed"], positive_report)
        self.assertTrue(positive_report["passed"], positive_report)

        specific = deepcopy(baseline["episode"])
        specific["chapters"][0]["sentences"][0]["text"] = (
            "This trial has reduced hospitalizations in the fictional study.")
        specific_evidence = deepcopy(baseline["evidence"])
        specific_evidence["e1"]["text"] = (
            "This trial has reduced hospitalizations in the fictional study. "
            + specific_evidence["e1"]["text"])
        specific_report = score_episode_quality(
            specific, baseline["research_plan"], specific_evidence)
        specific_opening = next(row for row in specific_report["checks"]
                               if row["name"] == "opening_hook")
        self.assertTrue(specific_opening["passed"], specific_report)
        self.assertTrue(specific_report["passed"], specific_report)

    def test_short_five_minute_script_fails_with_duration_issue(self):
        episode, plan, evidence = five_minute_case()
        for chapter in episode["chapters"]:
            chapter["sentences"] = chapter["sentences"][:3]
        report = score_episode_quality(episode, plan, evidence)
        self.assertFalse(report["passed"], report)
        self.assertIn("requested_duration", report["issues"])

    def test_repetition_and_list_reading_are_hard_failures_with_specific_issues(self):
        episode, plan, evidence = five_minute_case()
        episode["chapters"][1]["sentences"][0] = deepcopy(episode["chapters"][0]["sentences"][0])
        repeated = score_episode_quality(episode, plan, evidence)
        self.assertFalse(repeated["passed"], repeated)
        self.assertIn("no_cross_chapter_repetition", repeated["issues"])

        episode, plan, evidence = five_minute_case()
        episode["chapters"][1]["sentences"][0]["text"] = (
            "First, staff log missed pickups; second, they record repairs; third, they share comments; "
            "finally, town reviewers discuss access.")
        listed = score_episode_quality(episode, plan, evidence)
        self.assertFalse(listed["passed"], listed)
        self.assertIn("no_list_reading", listed["issues"])

    def test_good_fixture_passes_with_chapter_estimates_and_no_padding(self):
        episode = fixture("episode-script-checks-good.json")
        report = score_episode_script(episode)
        self.assertTrue(report["passed"], report)
        self.assertEqual(report["score"], report["out_of"])
        self.assertEqual(report["words_per_minute"], 140)
        self.assertEqual(len(report["chapters"]), 2)
        self.assertEqual(report["word_count"], sum(row["word_count"] for row in report["chapters"]))
        self.assertIn("shorter_than_requested", report["warnings"][0])
        self.assertEqual(episode["chapters"][0]["sentences"][0]["text"],
                         "Let's start with how residents get their packages.")
        checks = {row["name"]: row["passed"] for row in report["checks"]}
        self.assertTrue({"average_sentence_length", "max_sentence_length", "number_density",
                         "no_citation_markers", "no_reader_references", "no_urls",
                         "no_parentheses", "acronym_expansion", "opening_signpost", "story_transitions",
                         "closing_recap", "host_formula_variety", "no_source_absence_filler",
                         "no_repeated_claims", "concrete_ending", "concrete_segment_endings"}.issubset(checks))
        self.assertTrue(all(checks.values()), report)

    def test_five_minute_podcast_sample_is_ear_first_and_near_target(self):
        episode = fixture("episode-podcast-host-5min.json")
        report = score_episode_script(episode)
        self.assertTrue(episode["fictional_fixture"])
        self.assertTrue(report["passed"], report)
        self.assertGreaterEqual(report["word_count"], 630)
        self.assertLessEqual(report["word_count"], 770)

    def test_listening_lint_rejects_repeated_templates_source_silence_and_vague_ends(self):
        baseline = fixture("episode-script-checks-good.json")
        repeated_context = deepcopy(baseline)
        repeated_context["chapters"][0]["sentences"][1]["text"] = "For context, Alder Bay is testing parcel lockers at its library."
        repeated_context["chapters"][1]["sentences"][1]["text"] = "For context, residents use a code at the locker."
        report = score_episode_script(repeated_context)
        self.assertFalse(next(row for row in report["checks"]
                              if row["name"] == "host_formula_variety")["passed"], report)

        source_silence = deepcopy(baseline)
        source_silence["chapters"][0]["sentences"][1]["text"] = "The source does not say when residents can collect parcels."
        report = score_episode_script(source_silence)
        self.assertFalse(next(row for row in report["checks"]
                              if row["name"] == "no_source_absence_filler")["passed"], report)

        vague_end = deepcopy(baseline)
        vague_end["chapters"][-1]["sentences"][-1]["text"] = "The town will keep tracking progress and watch this space."
        report = score_episode_script(vague_end)
        self.assertFalse(next(row for row in report["checks"]
                              if row["name"] == "concrete_ending")["passed"], report)

    def test_segment_bridges_must_vary(self):
        episode = fixture("episode-script-checks-good.json")
        episode["chapters"][1]["sentences"][0]["text"] = "Turning to the library review."
        extra = deepcopy(episode["chapters"][1])
        extra["title"] = "Another library review"
        extra["sentences"][0]["text"] = "Turning to another library review."
        episode["chapters"].append(extra)
        report = score_episode_script(episode)
        check = next(row for row in report["checks"] if row["name"] == "host_formula_variety")
        self.assertFalse(check["passed"], report)

    def test_closing_cannot_repeat_the_preceding_claim_with_a_lead_in(self):
        episode = fixture("episode-script-checks-good.json")
        sentences = episode["chapters"][1]["sentences"]
        prior = next(row["text"] for row in reversed(sentences[:-1]) if row.get("kind") == "claim")
        sentences[-1]["text"] = f"Overall, {prior}"
        report = score_episode_script(episode)
        check = next(row for row in report["checks"] if row["name"] == "no_repeated_claims")
        self.assertFalse(check["passed"], report)
        self.assertFalse(report["passed"], report)

    def test_natural_transition_keeps_podcast_flow(self):
        episode = fixture("episode-script-checks-good.json")
        episode["chapters"][1]["sentences"][0]["text"] = (
            "That review matters because it determines whether the town keeps the lockers.")
        report = score_episode_script(episode)
        self.assertTrue(report["passed"], report)
        self.assertTrue(next(row for row in report["checks"]
                             if row["name"] == "story_transitions")["passed"], report)

    def test_listening_signposts_require_natural_opening_transitions_and_recap(self):
        baseline = fixture("episode-script-checks-good.json")
        cases = []
        missing_opening = deepcopy(baseline)
        missing_opening["chapters"][0]["sentences"].pop(0)
        cases.append((missing_opening, "opening_signpost"))
        duration_template = deepcopy(baseline)
        duration_template["chapters"][0]["sentences"][0]["text"] = (
            "Here is your five-minute briefing on Alder Bay's parcel locker trial.")
        cases.append((duration_template, "opening_signpost"))
        bare_question = deepcopy(baseline)
        bare_question["chapters"][0]["sentences"][0]["text"] = (
            "How do parcel lockers work for residents?")
        cases.append((bare_question, "opening_signpost"))
        missing_transition = deepcopy(baseline)
        missing_transition["chapters"][1]["sentences"].pop(0)
        cases.append((missing_transition, "story_transitions"))
        missing_closing = deepcopy(baseline)
        missing_closing["chapters"][-1]["sentences"].pop()
        cases.append((missing_closing, "closing_recap"))
        for episode, check_name in cases:
            with self.subTest(check=check_name):
                report = score_episode_script(episode)
                check = next(row for row in report["checks"] if row["name"] == check_name)
                self.assertFalse(check["passed"], report)

    def test_average_and_maximum_sentence_lengths_are_reported_separately(self):
        episode = fixture("episode-script-checks-good.json")
        long_but_valid = (
            "Alder Bay's fictional parcel locker trial continues through spring while town staff record "
            "every collection issue, resident comment, equipment repair, and missed pickup for the later public review.")
        for chapter in episode["chapters"]:
            for sentence in chapter["sentences"]:
                if sentence["kind"] == "claim":
                    sentence["text"] = long_but_valid
        report = score_episode_script(episode)
        checks = {row["name"]: row for row in report["checks"]}
        self.assertFalse(checks["average_sentence_length"]["passed"], report)
        self.assertTrue(checks["max_sentence_length"]["passed"], report)
        episode["chapters"][0]["sentences"][1]["text"] += " across the township."
        report = score_episode_script(episode)
        checks = {row["name"]: row for row in report["checks"]}
        self.assertFalse(checks["max_sentence_length"]["passed"], report)

    def test_spoken_artifact_checks_reject_citations_references_digits_headings_and_urls(self):
        base = fixture("episode-script-checks-good.json")
        cases = [
            ("Alder Bay reports twelve parcel lockers [1] beside its public library.", "no_citation_markers"),
            ("The town asks readers to see Figure 1 and click the source link above.", "no_reader_references"),
            ("Alder Bay reports twelve parcel lockers at https://alder.example.test/lockers.", "no_urls"),
            ("Chapter one: Alder Bay is testing parcel lockers at its public library.", "no_spoken_headings"),
            ("The trial uses a parcel locker (a secure pickup box) at the public library.", "no_parentheses"),
            ("Alder Bay reports 12 parcel lockers at its public library.", "spoken_numbers"),
        ]
        for text, check_name in cases:
            episode = deepcopy(base)
            episode["chapters"][0]["sentences"][1]["text"] = text
            report = score_episode_script(episode)
            check = next(row for row in report["checks"] if row["name"] == check_name)
            with self.subTest(check=check_name):
                self.assertFalse(check["passed"], report)

    def test_three_and_fifteen_minute_scripts_expand_acronyms_speak_dates_and_reject_citation_numbers(self):
        baseline = fixture("episode-script-checks-good.json")
        for minutes, length_word in ((3, "three"), (15, "fifteen")):
            with self.subTest(minutes=minutes):
                episode = deepcopy(baseline)
                episode["requested_minutes"] = minutes
                episode["chapters"][0]["sentences"][0]["text"] = (
                    f"Here is your {length_word}-minute briefing on Alder Bay's parcel locker trial.")
                episode["chapters"][0]["sentences"][1]["text"] = (
                    "The Federal Deposit Insurance Corporation, or FDIC, published fictional guidance.")
                episode["chapters"][1]["sentences"][1]["text"] = (
                    "FDIC staff said the fictional guidance took effect on November eighteenth, twenty twenty-six.")
                report = score_episode_script(episode)
                checks = {row["name"]: row for row in report["checks"]}
                self.assertTrue(checks["acronym_expansion"]["passed"], report)
                self.assertTrue(checks["spoken_numbers"]["passed"], report)
                self.assertTrue(checks["no_citation_markers"]["passed"], report)

                episode["chapters"][0]["sentences"][1]["text"] = (
                    "The town cites source one for the fictional parcel locker schedule.")
                report = score_episode_script(episode)
                citation_check = next(row for row in report["checks"]
                                      if row["name"] == "no_citation_markers")
                self.assertFalse(citation_check["passed"], report)

    def test_number_density_and_first_use_acronym_expansion(self):
        episode = fixture("episode-script-checks-good.json")
        episode["chapters"][0]["sentences"][1]["text"] = (
            "The town assigns one code, two tags, three notices, and four labels to each locker.")
        report = score_episode_script(episode)
        number_density = next(row for row in report["checks"] if row["name"] == "number_density")
        self.assertFalse(number_density["passed"], report)
        self.assertIn("chapter 1: 4", number_density["detail"])

        episode = fixture("episode-script-checks-good.json")
        episode["chapters"][0]["sentences"][1]["text"] = (
            "The Federal Deposit Insurance Corporation, or FDIC, insures deposits in federally backed accounts.")
        report = score_episode_script(episode)
        acronym = next(row for row in report["checks"] if row["name"] == "acronym_expansion")
        self.assertTrue(acronym["passed"], report)
        episode["chapters"][0]["sentences"][1]["text"] = (
            "FDIC insurance protects accounts in the fictional test.")
        report = score_episode_script(episode)
        acronym = next(row for row in report["checks"] if row["name"] == "acronym_expansion")
        self.assertFalse(acronym["passed"], report)

    def test_bad_fixture_flags_spoken_numbers_url_filler_and_repeated_read(self):
        report = score_episode_script(fixture("episode-script-checks-bad.json"))
        self.assertFalse(report["passed"])
        failed = {row["name"] for row in report["checks"] if not row["passed"]}
        self.assertTrue({"sentence_length", "spoken_numbers", "no_urls",
                         "no_banned_filler", "read_takeaway_is_fresh"}.issubset(failed))
        self.assertIn("digit_density", failed)

    def test_rewrite_callback_gets_one_attempt_and_rechecks_the_replacement(self):
        bad = fixture("episode-script-checks-bad.json")
        good = fixture("episode-script-checks-good.json")
        calls = []

        def rewrite(previous, report):
            calls.append((previous, report))
            return good

        result = check_episode_script(bad, rewrite)
        self.assertTrue(result["report"]["passed"], result["report"])
        self.assertEqual(result["repair_attempts"], 1)
        self.assertEqual(len(calls), 1)
        self.assertFalse(calls[0][1]["passed"])
        self.assertEqual(result["episode"]["id"], "fictional-script-good")

    def test_failed_repair_is_not_retried(self):
        calls = []
        bad = fixture("episode-script-checks-bad.json")

        def rewrite(previous, report):
            calls.append(True)
            return previous

        result = check_episode_script(bad, rewrite)
        self.assertFalse(result["report"]["passed"])
        self.assertEqual(result["repair_attempts"], 1)
        self.assertEqual(len(calls), 1)

    def test_passing_script_does_not_call_rewriter(self):
        def unexpected(*_args):
            self.fail("repair called for passing script")

        result = check_episode_script(fixture("episode-script-checks-good.json"), unexpected)
        self.assertTrue(result["report"]["passed"])
        self.assertEqual(result["repair_attempts"], 0)

    def test_overlong_requested_duration_fails(self):
        episode = fixture("episode-script-checks-good.json")
        episode["requested_minutes"] = 0.1
        report = score_episode_script(episode)
        self.assertFalse(report["passed"])
        self.assertFalse(next(row for row in report["checks"]
                              if row["name"] == "requested_duration")["passed"])

    def test_invalid_shape_is_reported(self):
        report = score_episode_script({"chapters": []})
        self.assertFalse(report["passed"])
        self.assertIn("episode needs at least one chapter", report["errors"])


if __name__ == "__main__":
    unittest.main()
