#!/usr/bin/env python3
"""Validate an externally supplied daily recording and stage a private review copy.

This tool does not call a provider and never edits application content or bundles.
The supported delivery format is bounded, uncompressed PCM WAV (16-bit, 44.1/48 kHz,
mono/stereo), which is readable by current iOS and web audio players. A later delivery
format can be added only with a reliable local metadata probe.
"""

import argparse
from array import array
import hashlib
import json
import math
import os
import re
import shutil
import stat
import sys
import wave
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONTENT = ROOT / "content"
MAX_AUDIO_BYTES = 250 * 1024 * 1024
FAST_PLAUSIBLE_WORDS_PER_MINUTE = 200


def fail(message):
    raise ValueError(message)


def sha256_file(path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def current_item(item_id, content_dir):
    path = content_dir / "editions.json"
    if not path.is_file():
        fail("Daily edition content pack is missing editions.json")
    matches = [x for x in json.loads(path.read_text(encoding="utf-8")).get("editions", [])
               if x.get("id") == item_id]
    if not matches:
        fail(f"No current content item named {item_id!r}")
    revisions = [item.get("revision_id") for item in matches]
    if any(not revision for revision in revisions) or len(set(revisions)) != len(revisions):
        fail("Content has missing or duplicate revisions for this stable ID")
    superseded = {correction.get("previous_revision_id") for item in matches
                  for correction in item.get("corrections", []) if correction.get("previous_revision_id")}
    live = [item for item in matches if item["revision_id"] not in superseded
            and item.get("status") not in {"superseded", "withdrawn"}
            and item.get("superseded") is not True and item.get("withdrawn") is not True
            and not item.get("superseded_by") and not item.get("withdrawn_at")]
    if len(live) != 1:
        fail(f"Content needs exactly one current revision; found {len(live)}")
    item = live[0]
    if item.get("kind") != "daily":
        fail("Recording preparation accepts daily editions only")
    sections = item.get("sections") or []
    if not sections or any(not all(k in s for k in ("id", "heading", "body")) for s in sections):
        fail("Current content has no valid narration sections")
    section_ids = [section["id"] for section in sections]
    if len(set(section_ids)) != len(section_ids):
        fail("Current content has duplicate section IDs")
    script = "\n\n".join(f"{s['heading']}\n{s['body']}" for s in sections)
    digest = hashlib.sha256(script.encode("utf-8")).hexdigest()
    if digest != item.get("script_hash"):
        fail("Current content script does not match its recorded SHA-256")
    word_count = len(re.findall(r"\b[\w’'-]+\b", script))
    return item, sections, digest, word_count


def validate_manifest(manifest_path, item, sections, script_hash):
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("item_id") != item["id"]:
        fail("Export manifest item ID does not match the current daily item")
    if manifest.get("revision_id") != item["revision_id"]:
        fail("Export manifest revision is stale")
    if manifest.get("script_sha256") != script_hash:
        fail("Export manifest script hash does not match current content")
    segment_list = manifest.get("segments")
    if not isinstance(segment_list, list) or not segment_list:
        fail("Export manifest has no segments")
    exported_ids = []
    exported_text = []
    manifest_dir = manifest_path.parent.resolve()
    for segment in segment_list:
        name = segment.get("file", "")
        path = (manifest_dir / name).resolve()
        if Path(name).name != name or path.parent != manifest_dir or not path.is_file():
            fail("Manifest segment path is missing or unsafe")
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != segment.get("sha256"):
            fail(f"Exported text segment hash mismatch: {name}")
        if len(data.decode("utf-8")) != segment.get("characters"):
            fail(f"Exported text segment length mismatch: {name}")
        exported_ids.extend(segment.get("section_ids") or [])
        exported_text.append(data.decode("utf-8"))
    if exported_ids != [section["id"] for section in sections]:
        fail("Exported segment order/sections do not match the current content pack")
    expected = "\n\n".join(f"{s['heading']}\n{s['body']}" for s in sections)
    if "\n\n".join(exported_text) != expected:
        fail("Exported narration text does not exactly match current section text")
    if manifest.get("characters") != len(expected):
        fail("Export manifest character count does not match current narration text")
    return manifest


def wav_info(audio_path):
    file_size = audio_path.stat().st_size
    if file_size > MAX_AUDIO_BYTES:
        fail("Audio exceeds the 250 MiB staging limit")
    try:
        with audio_path.open("rb") as source:
            header = source.read(12)
    except OSError as error:
        fail(f"Could not read WAV header: {error}")
    if len(header) != 12 or header[:4] != b"RIFF" or header[8:] != b"WAVE":
        fail("Audio must use a valid little-endian RIFF/WAVE container")
    if int.from_bytes(header[4:8], "little") + 8 != file_size:
        fail("WAV RIFF size does not match the actual file; file may be truncated or malformed")
    try:
        with wave.open(str(audio_path), "rb") as audio:
            channels, width, rate = audio.getnchannels(), audio.getsampwidth(), audio.getframerate()
            frames, compression = audio.getnframes(), audio.getcomptype()
            if channels not in (1, 2) or width != 2 or rate not in (44100, 48000) or compression != "NONE":
                fail("WAV must be uncompressed 16-bit PCM at 44.1 or 48 kHz, mono or stereo")
            frame_bytes = channels * width
            decoded_bytes = sample_count = sum_squares = peak = 0
            while True:
                chunk = audio.readframes(65536)
                if not chunk:
                    break
                decoded_bytes += len(chunk)
                samples = array("h")
                try:
                    samples.frombytes(chunk)
                except ValueError:
                    fail("WAV PCM payload contains a partial sample")
                if sys.byteorder != "little":
                    samples.byteswap()
                sample_count += len(samples)
                sum_squares += sum(sample * sample for sample in samples)
                if samples:
                    peak = max(peak, max(abs(sample) for sample in samples))
    except (wave.Error, EOFError) as error:
        fail(f"Audio must be a valid PCM WAV file: {error}")
    if decoded_bytes != frames * frame_bytes or decoded_bytes % frame_bytes:
        fail("WAV PCM payload is shorter than its declared frame count or malformed")
    duration = frames / rate
    if sample_count != frames * channels:
        fail("WAV PCM sample count does not match its declared frame count")
    rms = math.sqrt(sum_squares / sample_count) if sample_count else 0
    levels = {"rms_dbfs": 20 * math.log10(rms / 32768) if rms else None,
              "sample_peak_dbfs": 20 * math.log10(peak / 32768) if peak else None,
              "signal_level": "measured" if peak else "digital_silence",
              "level_measurement": "unweighted PCM RMS and absolute sample peak; dBFS"}
    return {"container": "wav", "codec": "pcm_s16le", "channels": channels,
            "sample_rate_hz": rate, "duration_seconds": duration,
            "audio_bytes": file_size,
            "format_use": "private review master; final delivery format remains undecided",
            **levels}


def minimum_duration_seconds(word_count):
    """Return a deliberately generous lower duration bound for full-edition audio."""
    return max(30, math.ceil(word_count * 60 / FAST_PLAUSIBLE_WORDS_PER_MINUTE))


def validate_chapters(chapters_path, duration, sections):
    if chapters_path is None:
        fail("Complete daily recording validation requires chapter timings")
    data = json.loads(chapters_path.read_text(encoding="utf-8"))
    if not isinstance(data, list) or len(data) < 2:
        fail("Chapter file must contain at least two chapter entries")
    ids = [section["id"] for section in sections]
    result = []
    previous = -1.0
    for chapter in data:
        if not isinstance(chapter, dict) or chapter.get("id") not in ids or not isinstance(chapter.get("title"), str) or not chapter["title"].strip():
            fail("Each chapter needs a known section ID and a non-empty title")
        start = chapter.get("start_seconds")
        if isinstance(start, bool) or not isinstance(start, (int, float)):
            fail("Chapter start_seconds must be numeric")
        if not math.isfinite(start):
            fail("Chapter start_seconds must be finite")
        if start <= previous or start < 0 or start >= duration:
            fail("Chapter starts must increase strictly and fall within the measured audio duration")
        result.append({"id": chapter["id"], "title": chapter["title"].strip(), "start_seconds": float(start)})
        previous = start
    if result[0]["start_seconds"] != 0 or [row["id"] for row in result] != ids:
        fail("Chapters must start at zero and list every content section in order")
    return result


def prepare(manifest_path, audio_path, out_dir, content_dir, chapters_path=None):
    manifest_path, audio_path = Path(manifest_path), Path(audio_path)
    out_dir = Path(out_dir)
    if out_dir.resolve().is_relative_to(ROOT):
        fail("Private staging output must be outside the source checkout")
    if not manifest_path.is_file() or not audio_path.is_file():
        fail("Manifest and audio file must both exist")
    if audio_path.suffix.lower() != ".wav":
        fail("Only validated PCM WAV is currently supported")
    if out_dir.exists():
        fail(f"Staging output already exists; refusing to overwrite: {out_dir}")
    item, sections, digest, word_count = current_item(manifest_item_id(manifest_path), Path(content_dir))
    manifest = validate_manifest(manifest_path, item, sections, digest)
    source_audio_hash = sha256_file(audio_path)
    info = wav_info(audio_path)
    if sha256_file(audio_path) != source_audio_hash:
        fail("Source audio changed while it was being validated")
    if info["signal_level"] == "digital_silence":
        fail("Audio contains digital silence and cannot be staged as a narration recording")
    minimum_duration = minimum_duration_seconds(word_count)
    if info["duration_seconds"] < minimum_duration:
        fail(f"Audio is shorter than the {minimum_duration}-second minimum for {word_count} words "
             f"at {FAST_PLAUSIBLE_WORDS_PER_MINUTE} words per minute")
    chapters = validate_chapters(chapters_path, info["duration_seconds"], sections)
    # Create output only after every input has passed validation.
    out_dir.mkdir(mode=0o700, parents=True)
    try:
        staged_name = "recording.wav"
        shutil.copyfile(audio_path, out_dir / staged_name)
        staged_audio_hash = sha256_file(out_dir / staged_name)
        if staged_audio_hash != source_audio_hash or sha256_file(audio_path) != source_audio_hash:
            fail("Source audio changed during staging or the staged checksum does not match")
        if wav_info(out_dir / staged_name) != info:
            fail("Staged WAV measurements do not match the validated source audio")
        os.chmod(out_dir / staged_name, stat.S_IRUSR | stat.S_IWUSR)
        metadata = {"status": "technical_validation_passed_human_review_pending", "item_id": item["id"],
                    "revision_id": item["revision_id"], "script_sha256": digest,
                    "script_word_count": word_count,
                    "audio_file": staged_name, "audio_sha256": staged_audio_hash,
                    **info, "chapters": chapters,
                    "source_manifest_sha256": sha256_file(manifest_path),
                    "provider_metadata": "not supplied by this provider-independent tool",
                    "editorial_approval": "pending human listening and review; not marked recorded"}
        (out_dir / "metadata.json").write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
        os.chmod(out_dir / "metadata.json", stat.S_IRUSR | stat.S_IWUSR)
    except Exception:
        shutil.rmtree(out_dir)
        raise
    return metadata


def manifest_item_id(manifest_path):
    try:
        return json.loads(Path(manifest_path).read_text(encoding="utf-8"))["item_id"]
    except (OSError, KeyError, json.JSONDecodeError, TypeError):
        fail("Export manifest is not valid JSON with an item_id")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path, help="export_narration.py manifest.json")
    parser.add_argument("--audio", required=True, type=Path, help="externally supplied final WAV recording")
    parser.add_argument("--out-dir", required=True, type=Path, help="new private staging directory")
    parser.add_argument("--content-dir", type=Path, default=CONTENT, help=argparse.SUPPRESS)
    parser.add_argument("--chapters", type=Path, help="optional JSON array of section chapter timings")
    args = parser.parse_args()
    try:
        result = prepare(args.manifest, args.audio, args.out_dir, args.content_dir, args.chapters)
    except (ValueError, OSError, json.JSONDecodeError) as error:
        print(f"Recording preparation rejected: {error}", file=sys.stderr)
        return 2
    print(f"Technically validated {result['item_id']} {result['revision_id']}; "
          f"{result['duration_seconds']:.3f}s WAV review master staged privately at {args.out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
