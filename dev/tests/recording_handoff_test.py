"""Disposable tests for approval-gated recording proposals and content checks."""

import hashlib
import json
import math
import subprocess
import sys
from array import array
import tempfile
import unittest
import wave
from pathlib import Path

from dev import check_content, prepare_recording, propose_recording


def digest(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def script_hash(sections):
    return digest("\n\n".join(f"{section['heading']}\n{section['body']}" for section in sections))


def write_pack(content_dir):
    content_dir.mkdir(parents=True, exist_ok=True)
    source = {"id": "source-fictional", "title": "Fictional reference"}
    topic = {"id": "topic-fictional", "title": "Fictional topic"}

    def section(section_id, heading, body):
        return {"id": section_id, "heading": heading, "body": body,
                "source_ids": [source["id"]], "topic_ids": [topic["id"]]}

    daily_sections = [section("daily-one", "Fictional first heading", "A fictional opening sentence."),
                      section("daily-two", "Fictional second heading", "A fictional closing sentence.")]
    weekly_sections = [section("weekly-one", "Fictional weekly heading", "A fictional weekly summary.")]
    dive_sections = [section("dive-one", "Fictional explanation heading", "A fictional explanation.")]

    def audio(revision, sections):
        return {"state": "pending", "revision_id": revision, "script_hash": script_hash(sections)}

    editions = [
        {"id": "daily-test-001", "kind": "daily", "revision_id": "daily-r1",
         "sections": daily_sections, "script_hash": script_hash(daily_sections),
         "audio": audio("daily-r1", daily_sections),
         "coverage": [{"topic_id": topic["id"]}]},
        {"id": "weekly-test-001", "kind": "weekly", "revision_id": "weekly-r1",
         "sections": weekly_sections, "script_hash": script_hash(weekly_sections),
         "audio": audio("weekly-r1", weekly_sections),
         "coverage": [{"topic_id": topic["id"]}]},
    ]
    dive = {"id": "dive-test-001", "topic_id": topic["id"], "revision_id": "dive-r1",
            "sections": dive_sections, "script_hash": script_hash(dive_sections),
            "audio": audio("dive-r1", dive_sections)}
    for filename, key, rows in (("topics.json", "topics", [topic]),
                                ("sources.json", "sources", [source]),
                                ("editions.json", "editions", editions),
                                ("deep-dives.json", "deep_dives", [dive])):
        (content_dir / filename).write_text(json.dumps({key: rows}, indent=2), encoding="utf-8")
    return editions[0], editions[1], dive


def write_tone(path, seconds=40):
    tone = array("h", (round(80 * math.sin(2 * math.pi * 440 * i / 44100)) for i in range(44100)))
    frames = round(seconds * 44100)
    full_seconds, remainder = divmod(frames, 44100)
    with wave.open(str(path), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(44100)
        for _ in range(full_seconds):
            audio.writeframesraw(tone.tobytes())
        if remainder:
            audio.writeframesraw(tone[:remainder].tobytes())


class RecordingHandoffTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.content = self.root / "source-content"
        self.daily, self.weekly, self.dive = write_pack(self.content)
        self.staged = self.root / "staged-candidate"
        self.staged.mkdir()
        self.audio = self.staged / "recording.wav"
        write_tone(self.audio)
        measurements = prepare_recording.wav_info(self.audio)
        _, _, script_digest, word_count = prepare_recording.current_item(self.daily["id"], self.content)
        chapters = [{"id": section["id"], "title": section["heading"], "start_seconds": start}
                    for section, start in zip(self.daily["sections"], (0, 20))]
        self.audio_hash = check_content.file_sha256(self.audio)
        report = {"status": "technical_validation_passed_human_review_pending",
                  "item_id": self.daily["id"], "revision_id": self.daily["revision_id"],
                  "script_sha256": script_digest, "script_word_count": word_count,
                  "audio_file": self.audio.name, "audio_sha256": self.audio_hash,
                  **measurements, "chapters": chapters}
        (self.staged / "metadata.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        self.approval_path = self.root / "approval.json"
        self.write_approval()
        self.proposal = self.root / "proposal"

    def tearDown(self):
        self.temp.cleanup()

    def write_approval(self, **changes):
        report = json.loads((self.staged / "metadata.json").read_text())
        approval = {"test_fixture": True, "decision": "approved", "scope": "complete_recording",
                    "scope_checks": {"listening_quality": True, "spoken_completeness": True,
                                     "chapter_alignment": True},
                    "item_id": self.daily["id"], "revision_id": self.daily["revision_id"],
                    "script_sha256": report["script_sha256"], "audio_sha256": self.audio_hash,
                    "chapters": report["chapters"], "reviewer": "Fictional reviewer (test fixture)",
                    "review_date_utc": "2026-11-16T12:00:00Z", "decision_reference": "fictional-private-review-1",
                    "provider": "fictional-provider", "model": "fictional-model", "voice": "fictional-voice",
                    "rights_decision": {"approved": True, "basis": "Fictional test permission"},
                    "disclosure_decision": {"decision": "required", "wording": "Fictional synthetic voice"}}
        approval.update(changes)
        self.approval_path.write_text(json.dumps(approval, indent=2), encoding="utf-8")

    def test_approved_fixture_writes_isolated_proposal_and_keeps_other_audio_pending(self):
        result = propose_recording.propose(self.staged, self.approval_path, self.proposal, self.content)
        candidate_content = self.proposal / "content"
        counts = check_content.validate_content(candidate_content)
        candidate_editions = json.loads((candidate_content / "editions.json").read_text())["editions"]
        candidate_daily = next(row for row in candidate_editions if row["id"] == self.daily["id"])
        candidate_weekly = next(row for row in candidate_editions if row["id"] == self.weekly["id"])
        candidate_dive = json.loads((candidate_content / "deep-dives.json").read_text())["deep_dives"][0]
        self.assertEqual(result["status"], "proposed_awaiting_review")
        self.assertTrue(result["test_fixture"])
        self.assertEqual(counts["editions"], 2)
        self.assertEqual(candidate_daily["audio"]["state"], "recorded")
        self.assertEqual(candidate_daily["audio"]["audio_sha256"], self.audio_hash)
        self.assertNotIn("provider", candidate_daily["audio"], "provider details stay out of app content")
        self.assertEqual(candidate_weekly["audio"]["state"], "pending")
        self.assertEqual(candidate_dive["audio"]["state"], "pending")
        self.assertTrue((candidate_content / "audio" / "daily-test-001.wav").is_file())
        source_daily = json.loads((self.content / "editions.json").read_text())["editions"][0]
        self.assertEqual(source_daily["audio"]["state"], "pending", "source content remains unchanged")

    def test_fractional_duration_rounds_up_in_candidate_and_validates(self):
        fractional_audio = self.staged / "fractional.wav"
        write_tone(fractional_audio, 40.125)
        measurements = prepare_recording.wav_info(fractional_audio)
        self.assertNotEqual(measurements["duration_seconds"] % 1, 0)
        report_path = self.staged / "metadata.json"
        report = json.loads(report_path.read_text())
        report.update(measurements)
        report["audio_file"] = fractional_audio.name
        report["audio_sha256"] = check_content.file_sha256(fractional_audio)
        report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        self.audio_hash = report["audio_sha256"]
        self.write_approval()
        fractional_proposal = self.root / "fractional-proposal"
        propose_recording.propose(self.staged, self.approval_path, fractional_proposal, self.content)
        candidate_content = fractional_proposal / "content"
        check_content.validate_content(candidate_content)
        candidate_daily = json.loads((candidate_content / "editions.json").read_text())["editions"][0]
        self.assertEqual(candidate_daily["audio"]["duration_seconds"],
                         math.ceil(measurements["duration_seconds"]))
        self.assertIsInstance(candidate_daily["audio"]["duration_seconds"], int)
        self.assertEqual(report["duration_seconds"], measurements["duration_seconds"],
                         "private staged report retains exact fractional seconds")

    def test_direct_script_help_runs_without_install(self):
        result = subprocess.run([sys.executable, "dev/propose_recording.py", "--help"],
                                cwd=prepare_recording.ROOT, capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--staged-dir", result.stdout)

    def test_pending_approval_is_rejected(self):
        self.write_approval(decision="pending")
        with self.assertRaisesRegex(ValueError, "not approved"):
            propose_recording.propose(self.staged, self.approval_path, self.proposal, self.content)
        self.assertFalse(self.proposal.exists())

    def test_placeholder_approval_fields_are_rejected(self):
        self.write_approval(reviewer="  PENDING ")
        with self.assertRaisesRegex(ValueError, "concrete reviewer"):
            propose_recording.propose(self.staged, self.approval_path, self.proposal, self.content)
        self.assertFalse(self.proposal.exists())

    def test_audition_scope_cannot_approve_full_recording(self):
        self.write_approval(scope="audition")
        with self.assertRaisesRegex(ValueError, "complete recording"):
            propose_recording.propose(self.staged, self.approval_path, self.proposal, self.content)

    def test_approval_must_bind_exact_chapter_list(self):
        self.write_approval(chapters=[])
        with self.assertRaisesRegex(ValueError, "chapter list does not match"):
            propose_recording.propose(self.staged, self.approval_path, self.proposal, self.content)

    def test_changed_staged_audio_is_rejected(self):
        with self.audio.open("ab") as audio:
            audio.write(b"x")
        with self.assertRaisesRegex(ValueError, "bytes changed"):
            propose_recording.propose(self.staged, self.approval_path, self.proposal, self.content)
        self.assertFalse(self.proposal.exists())

    def test_changed_text_under_same_revision_is_rejected(self):
        data = json.loads((self.content / "editions.json").read_text())
        item = data["editions"][0]
        item["sections"][0]["body"] += " Changed after staging."
        item["script_hash"] = script_hash(item["sections"])
        item["audio"]["script_hash"] = item["script_hash"]
        (self.content / "editions.json").write_text(json.dumps(data), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "script hash does not match current text"):
            propose_recording.propose(self.staged, self.approval_path, self.proposal, self.content)
        self.assertFalse(self.proposal.exists())

    def test_stale_revision_is_rejected(self):
        data = json.loads((self.content / "editions.json").read_text())
        item = data["editions"][0]
        item["revision_id"] = "daily-r2"
        item["audio"]["revision_id"] = "daily-r2"
        (self.content / "editions.json").write_text(json.dumps(data), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "revision is stale"):
            propose_recording.propose(self.staged, self.approval_path, self.proposal, self.content)
        self.assertFalse(self.proposal.exists())

    def test_content_validator_requires_present_recorded_bytes_and_hash(self):
        data = json.loads((self.content / "editions.json").read_text())
        item = data["editions"][0]
        item["audio"].update({"state": "recorded", "url": "/content/audio/missing.wav",
                              "audio_sha256": "0" * 64})
        (self.content / "editions.json").write_text(json.dumps(data), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "recording missing"):
            check_content.validate_content(self.content)

    def test_content_validator_detects_recorded_bytes_hash_mismatch(self):
        audio_dir = self.content / "audio"
        audio_dir.mkdir()
        (audio_dir / "present.wav").write_bytes(self.audio.read_bytes())
        data = json.loads((self.content / "editions.json").read_text())
        item = data["editions"][0]
        item["audio"].update({"state": "recorded", "url": "/content/audio/present.wav",
                              "audio_sha256": "0" * 64})
        (self.content / "editions.json").write_text(json.dumps(data), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "SHA-256 does not match"):
            check_content.validate_content(self.content)

    def test_proposal_path_inside_another_checkout_is_rejected(self):
        other_repo = self.root / "other-checkout"
        other_repo.mkdir()
        (other_repo / ".git").write_text("gitdir: /private/test-only\n")
        other_content = other_repo / "content"
        other_content.mkdir()
        output = other_repo / "proposal"
        with self.assertRaisesRegex(ValueError, "outside the supplied content directory's repository"):
            propose_recording.propose(self.staged, self.approval_path, output, other_content)
        self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
