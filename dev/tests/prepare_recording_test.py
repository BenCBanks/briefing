"""Provider-free checks for private daily recording preparation."""

import hashlib
import json
import math
import shutil
from array import array
import tempfile
import unittest
import wave
from pathlib import Path
from unittest import mock

from dev import prepare_recording


def write_test_tone(path, seconds, amplitude=50):
    """Write a disposable synthetic tone fixture; never an app audio asset."""
    tone = array("h", (round(amplitude * math.sin(2 * math.pi * 440 * frame / 44100))
                        for frame in range(44100)))
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(44100)
        for _ in range(seconds):
            output.writeframesraw(tone.tobytes())


class PrepareRecordingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.content = self.root / "content"
        self.content.mkdir()
        self.sections = [
            {"id": "one", "heading": "Fictional first heading", "body": "A technical test sentence."},
            {"id": "two", "heading": "Fictional second heading", "body": "Another technical test sentence."},
        ]
        script = "\n\n".join(f"{row['heading']}\n{row['body']}" for row in self.sections)
        self.item = {"id": "daily-test-001", "kind": "daily", "revision_id": "test-r1", "sections": self.sections,
                     "script_hash": hashlib.sha256(script.encode()).hexdigest(), "audio": {"state": "pending"}}
        self.write_content()
        self.export = self.root / "export"
        self.export.mkdir()
        text = "\n\n".join(f"{row['heading']}\n{row['body']}" for row in self.sections)
        (self.export / "segment-01.txt").write_text(text, encoding="utf-8")
        self.manifest_path = self.export / "manifest.json"
        self.write_manifest(text)
        self.audio = self.root / "fictional.wav"
        write_test_tone(self.audio, 30)

    def tearDown(self):
        self.temp.cleanup()

    def write_content(self):
        (self.content / "editions.json").write_text(json.dumps({"editions": [self.item]}), encoding="utf-8")

    def write_manifest(self, text, **overrides):
        data = {"item_id": self.item["id"], "revision_id": self.item["revision_id"],
                "script_sha256": self.item["script_hash"], "characters": len(text),
                "segments": [{"file": "segment-01.txt", "characters": len(text),
                              "sha256": hashlib.sha256(text.encode()).hexdigest(),
                              "section_ids": [row["id"] for row in self.sections]}]}
        data.update(overrides)
        self.manifest_path.write_text(json.dumps(data), encoding="utf-8")

    def run_prepare(self, **kwargs):
        out = self.root / "private-stage"
        chapters = kwargs.get("chapters")
        if chapters is None:
            chapters = self.root / "default-chapters.json"
            chapters.write_text(json.dumps([{"id": "one", "title": "First", "start_seconds": 0},
                                            {"id": "two", "title": "Second", "start_seconds": 20}]))
        result = prepare_recording.prepare(self.manifest_path, self.audio, out, self.content,
                                           chapters)
        return result, out

    def test_stages_verified_metadata_and_pcm_copy(self):
        chapters = self.root / "chapters.json"
        chapters.write_text(json.dumps([{"id": "one", "title": "First", "start_seconds": 0},
                                        {"id": "two", "title": "Second", "start_seconds": 12}]))
        result, out = self.run_prepare(chapters=chapters)
        self.assertEqual(result["duration_seconds"], 30.0)
        self.assertEqual(result["status"], "technical_validation_passed_human_review_pending")
        self.assertEqual(result["chapters"][1]["start_seconds"], 12.0)
        self.assertEqual(result["audio_bytes"], self.audio.stat().st_size)
        self.assertIn("private review master", result["format_use"])
        self.assertLess(result["rms_dbfs"], -50)
        self.assertLess(result["sample_peak_dbfs"], -50)
        self.assertEqual(result["signal_level"], "measured")
        self.assertAlmostEqual(result["rms_dbfs"], -59.3, delta=0.1)
        self.assertAlmostEqual(result["sample_peak_dbfs"], -56.3, delta=0.1)
        self.assertEqual(hashlib.sha256((out / "recording.wav").read_bytes()).hexdigest(), result["audio_sha256"])
        self.assertFalse(result.get("recorded"))

    def test_rejects_same_revision_script_edit_even_if_manifest_hash_is_old(self):
        self.item["sections"][0]["body"] += " Edited without a revision bump."
        self.write_content()
        with self.assertRaisesRegex(ValueError, "script does not match"):
            self.run_prepare()
        self.assertFalse((self.root / "private-stage").exists())

    def test_rejects_stale_manifest_revision(self):
        self.write_manifest((self.export / "segment-01.txt").read_text(), revision_id="old-r0")
        with self.assertRaisesRegex(ValueError, "revision is stale"):
            self.run_prepare()

    def test_rejects_weekly_edition(self):
        self.item["kind"] = "weekly"
        self.write_content()
        with self.assertRaisesRegex(ValueError, "daily editions only"):
            self.run_prepare()

    def test_rejects_missing_chapter_timings(self):
        with self.assertRaisesRegex(ValueError, "requires chapter timings"):
            prepare_recording.prepare(self.manifest_path, self.audio, self.root / "stage", self.content)

    def test_rejects_45_second_audio_for_full_length_daily_script(self):
        self.item["sections"][0]["body"] += " " + "fictional " * 1600
        script = "\n\n".join(f"{row['heading']}\n{row['body']}" for row in self.sections)
        self.item["script_hash"] = hashlib.sha256(script.encode()).hexdigest()
        self.write_content()
        (self.export / "segment-01.txt").write_text(script, encoding="utf-8")
        self.write_manifest(script)
        short = self.root / "short-daily.wav"
        write_test_tone(short, 45)
        chapters = self.root / "long-chapters.json"
        chapters.write_text(json.dumps([{"id": "one", "title": "First", "start_seconds": 0},
                                        {"id": "two", "title": "Second", "start_seconds": 20}]))
        with self.assertRaisesRegex(ValueError, "minimum for .* words at 200 words per minute"):
            prepare_recording.prepare(self.manifest_path, short, self.root / "short-stage", self.content, chapters)

    def test_ten_minute_daily_candidate_passes_word_count_duration_gate(self):
        minimum = prepare_recording.minimum_duration_seconds(1600)
        self.assertEqual(minimum, 480)
        self.assertGreaterEqual(600, minimum)

    def test_measures_nonzero_pcm_rms_and_sample_peak(self):
        signal = self.root / "signal.wav"
        with wave.open(str(signal), "wb") as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(44100)
            for _ in range(3):
                output.writeframesraw(b"\xe8\x03" * 44100 * 10)
        result = prepare_recording.wav_info(signal)
        expected_dbfs = 20 * math.log10(1000 / 32768)
        self.assertTrue(math.isclose(result["rms_dbfs"], expected_dbfs, abs_tol=0.001))
        self.assertTrue(math.isclose(result["sample_peak_dbfs"], expected_dbfs, abs_tol=0.001))
        self.assertEqual(result["signal_level"], "measured")

    def test_refuses_staging_path_inside_source_checkout(self):
        output = prepare_recording.ROOT / "dev" / "must-not-be-created"
        with self.assertRaisesRegex(ValueError, "outside the source checkout"):
            prepare_recording.prepare(self.manifest_path, self.audio, output, self.content)
        self.assertFalse(output.exists())

    def test_rejects_digital_silence_before_staging(self):
        silence = self.root / "silence.wav"
        with wave.open(str(silence), "wb") as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(44100)
            output.writeframes(b"\0\0" * 44100 * 30)
        chapters = self.root / "silence-chapters.json"
        chapters.write_text(json.dumps([{"id": "one", "title": "First", "start_seconds": 0},
                                        {"id": "two", "title": "Second", "start_seconds": 20}]))
        stage = self.root / "silence-stage"
        with self.assertRaisesRegex(ValueError, "digital silence"):
            prepare_recording.prepare(self.manifest_path, silence, stage, self.content, chapters)
        self.assertFalse(stage.exists())

    def test_rejects_source_audio_mutated_during_copy(self):
        stage = self.root / "mutated-stage"
        copyfile = shutil.copyfile

        def copy_then_mutate(source, destination):
            copyfile(source, destination)
            with Path(source).open("r+b") as changed:
                changed.seek(-2, 2)
                changed.write(b"\x01\x00")

        chapters = self.root / "mutation-chapters.json"
        chapters.write_text(json.dumps([{"id": "one", "title": "First", "start_seconds": 0},
                                        {"id": "two", "title": "Second", "start_seconds": 20}]))
        with mock.patch.object(prepare_recording.shutil, "copyfile", side_effect=copy_then_mutate):
            with self.assertRaisesRegex(ValueError, "changed during staging"):
                prepare_recording.prepare(self.manifest_path, self.audio, stage, self.content, chapters)
        self.assertFalse(stage.exists())

    def test_minimum_duration_helper_has_short_script_floor(self):
        self.assertEqual(prepare_recording.minimum_duration_seconds(10), 30)

    def test_rejects_duplicate_section_ids(self):
        self.item["sections"][1]["id"] = self.item["sections"][0]["id"]
        script = "\n\n".join(f"{row['heading']}\n{row['body']}" for row in self.sections)
        self.item["script_hash"] = hashlib.sha256(script.encode()).hexdigest()
        self.write_content()
        with self.assertRaisesRegex(ValueError, "duplicate section IDs"):
            self.run_prepare()

    def test_rejects_short_audio_for_full_daily_edition(self):
        short = self.root / "short.wav"
        write_test_tone(short, 10)
        chapters = self.root / "short-chapters.json"
        chapters.write_text(json.dumps([{"id": "one", "title": "First", "start_seconds": 0},
                                        {"id": "two", "title": "Second", "start_seconds": 5}]))
        with self.assertRaisesRegex(ValueError, "30-second minimum"):
            prepare_recording.prepare(self.manifest_path, short, self.root / "short-stage", self.content, chapters)

    def test_rejects_truncated_audio_even_when_riff_size_is_rewritten(self):
        data = self.audio.read_bytes()[:-100]
        malformed = self.root / "truncated.wav"
        malformed.write_bytes(data[:4] + (len(data) - 8).to_bytes(4, "little") + data[8:])
        chapters = self.root / "truncated-chapters.json"
        chapters.write_text(json.dumps([{"id": "one", "title": "First", "start_seconds": 0},
                                        {"id": "two", "title": "Second", "start_seconds": 20}]))
        with self.assertRaisesRegex(ValueError, "payload is shorter"):
            prepare_recording.prepare(self.manifest_path, malformed, self.root / "truncated-stage", self.content, chapters)

    def test_rejects_segment_bytes_changed_after_export(self):
        (self.export / "segment-01.txt").write_text("tampered", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "segment hash mismatch"):
            self.run_prepare()

    def test_rejects_nonmonotonic_nan_chapters(self):
        chapters = self.root / "chapters.json"
        chapters.write_text(json.dumps([{"id": "one", "title": "First", "start_seconds": 0},
                                        {"id": "two", "title": "Second", "start_seconds": math.nan}]))
        with self.assertRaisesRegex(ValueError, "finite"):
            self.run_prepare(chapters=chapters)

    def test_refuses_existing_staging_directory(self):
        out = self.root / "private-stage"
        out.mkdir()
        with self.assertRaisesRegex(ValueError, "refusing to overwrite"):
            prepare_recording.prepare(self.manifest_path, self.audio, out, self.content)


if __name__ == "__main__":
    unittest.main()
