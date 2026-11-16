#!/usr/bin/env python3
"""Build a private, review-only content proposal from an approved staged recording."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
import os
import re
import shutil
import stat
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from dev import check_content, prepare_recording


ROOT = Path(__file__).resolve().parents[1]
CONTENT = ROOT / "content"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
CONTENT_FILES = ("topics.json", "deep-dives.json", "sources.json", "editions.json")
PLACEHOLDER_VALUES = {"pending", "todo", "tbd", "unknown", "n/a", "na", "none",
                      "placeholder", "not provided", "to be determined"}


def fail(message):
    raise ValueError(message)


def read_json(path, description):
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        fail(f"Could not read {description}: {error}")
    if not isinstance(value, dict):
        fail(f"{description} must contain a JSON object")
    return value


def sha256_file(path):
    return check_content.file_sha256(Path(path))


def validate_approval(approval, item_id, revision_id, script_hash, audio_hash, chapters):
    if approval.get("decision") != "approved":
        fail("Approval record is not approved")
    if approval.get("scope") != "complete_recording":
        fail("Approval record must cover the complete recording")
    scope_checks = approval.get("scope_checks")
    required_checks = {"listening_quality", "spoken_completeness", "chapter_alignment"}
    if not isinstance(scope_checks, dict) or set(scope_checks) != required_checks \
            or any(scope_checks.get(check) is not True for check in required_checks):
        fail("Complete-recording approval must affirm listening quality, spoken completeness and chapter alignment")
    for field, expected in (("item_id", item_id), ("revision_id", revision_id),
                            ("script_sha256", script_hash), ("audio_sha256", audio_hash)):
        if approval.get(field) != expected:
            fail(f"Approval record {field} does not match the current staged candidate")
    if approval.get("chapters") != chapters:
        fail("Approval record chapter list does not match the staged chapters")

    def meaningful_string(value):
        return isinstance(value, str) and bool(value.strip()) and value.strip().casefold() not in PLACEHOLDER_VALUES

    for field in ("reviewer", "decision_reference", "provider", "model", "voice"):
        if not meaningful_string(approval.get(field)):
            fail(f"Approval record needs a concrete {field}")
    rights = approval.get("rights_decision")
    if not isinstance(rights, dict) or rights.get("approved") is not True \
            or not meaningful_string(rights.get("basis")):
        fail("Approval record needs an affirmative rights decision and basis")
    disclosure = approval.get("disclosure_decision")
    if not isinstance(disclosure, dict):
        fail("Approval record needs a disclosure decision")
    if disclosure.get("decision") == "required":
        if not meaningful_string(disclosure.get("wording")):
            fail("Required disclosure needs exact wording")
    elif disclosure.get("decision") == "not_required":
        if not meaningful_string(disclosure.get("rationale")):
            fail("A not-required disclosure decision needs a rationale")
    else:
        fail("Disclosure decision must be required or not_required")
    reviewed_at = approval.get("review_date_utc")
    if not isinstance(reviewed_at, str):
        fail("Approval record needs a UTC review_date_utc")
    try:
        parsed = datetime.fromisoformat(reviewed_at.replace("Z", "+00:00"))
    except ValueError:
        fail("Approval review_date_utc must be an ISO-8601 timestamp")
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        fail("Approval review_date_utc must include a UTC timezone")


def validate_chapters(chapters, sections, duration):
    if not isinstance(chapters, list) or len(chapters) != len(sections):
        fail("Staged report chapters must match every current section")
    expected_ids = [section["id"] for section in sections]
    if [chapter.get("id") if isinstance(chapter, dict) else None for chapter in chapters] != expected_ids:
        fail("Staged chapter IDs do not match current sections in order")
    checked = []
    previous = -1.0
    for chapter, section in zip(chapters, sections):
        title, start = chapter.get("title"), chapter.get("start_seconds")
        if not isinstance(title, str) or title.strip() != section["heading"].strip():
            fail("Staged chapter titles must match the current section headings")
        if isinstance(start, bool) or not isinstance(start, (int, float)) or not math.isfinite(start):
            fail("Staged chapter starts must be finite numbers")
        if start <= previous or start < 0 or start >= duration:
            fail("Staged chapter starts must increase strictly within measured duration")
        checked.append({"id": section["id"], "title": title.strip(), "start_seconds": float(start)})
        previous = start
    if checked[0]["start_seconds"] != 0:
        fail("The first staged chapter must start at zero")
    return checked


def current_daily(item_id, content_dir):
    item, sections, digest, word_count = prepare_recording.current_item(item_id, Path(content_dir))
    if item.get("kind") != "daily":
        fail("Only the current daily edition can receive a recording proposal")
    return item, sections, digest, word_count


def validate_staged(staged_dir, approval_path, content_dir):
    staged_dir, content_dir = Path(staged_dir), Path(content_dir)
    report = read_json(staged_dir / "metadata.json", "staged technical report")
    if report.get("status") != "technical_validation_passed_human_review_pending":
        fail("Staged report is not an awaiting-listening-review candidate")
    item_id = report.get("item_id")
    if not isinstance(item_id, str) or not item_id or not re.fullmatch(r"[A-Za-z0-9_-]+", item_id):
        fail("Staged report has an invalid daily item ID")
    item, sections, script_hash, word_count = current_daily(item_id, content_dir)
    if report.get("revision_id") != item["revision_id"]:
        fail("Staged report revision is stale")
    if report.get("script_sha256") != script_hash:
        fail("Staged report script hash does not match current text")
    report_audio = report.get("audio_file")
    if not isinstance(report_audio, str) or Path(report_audio).name != report_audio:
        fail("Staged report audio path is unsafe")
    staged_audio = (staged_dir / report_audio).resolve()
    if staged_audio.parent != staged_dir.resolve() or not staged_audio.is_file():
        fail("Staged recording bytes are missing")
    expected_audio_hash = report.get("audio_sha256")
    if not isinstance(expected_audio_hash, str) or not SHA256_RE.fullmatch(expected_audio_hash):
        fail("Staged report needs a valid audio SHA-256")
    audio_hash = sha256_file(staged_audio)
    if audio_hash != expected_audio_hash:
        fail("Staged recording bytes changed after technical validation")
    measurements = prepare_recording.wav_info(staged_audio)
    if measurements.get("signal_level") == "digital_silence":
        fail("Staged recording contains digital silence")
    for key, measured in measurements.items():
        if report.get(key) != measured:
            fail(f"Staged report {key} no longer matches the recording bytes")
    if word_count != report.get("script_word_count"):
        fail("Staged report word count does not match current text")
    if measurements["duration_seconds"] < prepare_recording.minimum_duration_seconds(word_count):
        fail("Staged recording is too short for the current daily script")
    chapters = validate_chapters(report.get("chapters"), sections, measurements["duration_seconds"])
    approval = read_json(approval_path, "approval record")
    validate_approval(approval, item_id, item["revision_id"], script_hash, audio_hash, chapters)
    return {"item": item, "sections": sections, "script_hash": script_hash,
            "word_count": word_count, "audio_hash": audio_hash, "audio_path": staged_audio,
            "measurements": measurements, "chapters": chapters, "approval": approval}


def propose(staged_dir, approval_path, proposal_dir, content_dir=CONTENT):
    staged_dir, approval_path = Path(staged_dir), Path(approval_path)
    proposal_dir, content_dir = Path(proposal_dir), Path(content_dir)
    resolved_output = proposal_dir.resolve()
    resolved_content = content_dir.resolve()
    if resolved_output.is_relative_to(ROOT):
        fail("Private proposal output must be outside the source checkout")
    if resolved_output.is_relative_to(resolved_content):
        fail("Private proposal output must be outside the supplied content directory")
    repo_root = next((parent for parent in (resolved_content, *resolved_content.parents)
                      if (parent / ".git").exists()), None)
    if repo_root is not None and resolved_output.is_relative_to(repo_root):
        fail("Private proposal output must be outside the supplied content directory's repository")
    if proposal_dir.exists():
        fail(f"Proposal output already exists; refusing to overwrite: {proposal_dir}")
    source_hashes = {}
    for filename in CONTENT_FILES:
        source = content_dir / filename
        if not source.is_file():
            fail(f"Source content pack is missing {filename}")
        source_hashes[filename] = sha256_file(source)
    validated = validate_staged(staged_dir, approval_path, content_dir)
    # Ensure the source pack is currently valid before making any proposal files.
    check_content.validate_content(content_dir)
    candidate_item = json.loads(json.dumps(validated["item"]))
    approval = validated["approval"]
    measurements = validated["measurements"]
    audio_name = f"{candidate_item['id']}.wav"
    candidate_item["audio"] = {
        "state": "recorded",
        "revision_id": candidate_item["revision_id"],
        "script_hash": validated["script_hash"],
        "url": f"/content/audio/{audio_name}",
        "audio_sha256": validated["audio_hash"],
        "duration_seconds": math.ceil(measurements["duration_seconds"]),
        "chapters": validated["chapters"],
        "rms_dbfs": measurements["rms_dbfs"],
        "sample_peak_dbfs": measurements["sample_peak_dbfs"],
    }
    proposal_dir.mkdir(mode=0o700, parents=True)
    try:
        candidate_content = proposal_dir / "content"
        candidate_content.mkdir(mode=0o700)
        for filename in CONTENT_FILES:
            source = content_dir / filename
            shutil.copyfile(source, candidate_content / filename)
            os.chmod(candidate_content / filename, stat.S_IRUSR | stat.S_IWUSR)
        if any(sha256_file(content_dir / filename) != source_hashes[filename]
               for filename in CONTENT_FILES):
            fail("Source content changed while the proposal pack was being copied")
        editions_path = candidate_content / "editions.json"
        editions_data = json.loads(editions_path.read_text(encoding="utf-8"))
        matching_indexes = [index for index, row in enumerate(editions_data["editions"])
                            if row.get("id") == candidate_item["id"]
                            and row.get("revision_id") == candidate_item["revision_id"]]
        if len(matching_indexes) != 1:
            fail("Proposal copy no longer has exactly one matching daily revision")
        editions_data["editions"][matching_indexes[0]] = candidate_item
        editions_path.write_text(json.dumps(editions_data, indent=2, ensure_ascii=False) + "\n",
                                 encoding="utf-8")
        proposed_rows = json.loads(editions_path.read_text(encoding="utf-8"))["editions"]
        proposed_item = proposed_rows[matching_indexes[0]]
        if proposed_item.get("audio", {}).get("state") != "recorded" \
                or proposed_item["audio"].get("audio_sha256") != validated["audio_hash"]:
            fail("Proposal content did not persist the approved recorded state and checksum")
        audio_dir = candidate_content / "audio"
        audio_dir.mkdir(mode=0o700)
        proposed_audio = audio_dir / audio_name
        shutil.copyfile(validated["audio_path"], proposed_audio)
        copied_hash = sha256_file(proposed_audio)
        if copied_hash != validated["audio_hash"] or sha256_file(validated["audio_path"]) != validated["audio_hash"]:
            fail("Staged audio changed while the proposal was being written")
        if prepare_recording.wav_info(proposed_audio) != measurements:
            fail("Proposed audio measurements do not match the validated staged recording")
        os.chmod(proposed_audio, stat.S_IRUSR | stat.S_IWUSR)
        approval_copy = proposal_dir / "approval_record.json"
        approval_copy.write_text(json.dumps(approval, indent=2, ensure_ascii=False) + "\n",
                                 encoding="utf-8")
        os.chmod(approval_copy, stat.S_IRUSR | stat.S_IWUSR)
        proposal = {"status": "proposed_awaiting_review", "activated": False,
                    "approval_record": "validated_attestation_not_identity_verified",
                    "item_id": candidate_item["id"], "revision_id": candidate_item["revision_id"],
                    "script_sha256": validated["script_hash"], "audio_sha256": copied_hash,
                    "proposal_content_dir": "content", "asset": f"content/audio/{audio_name}",
                    "test_fixture": approval.get("test_fixture") is True}
        proposal_path = proposal_dir / "proposal.json"
        proposal_path.write_text(json.dumps(proposal, indent=2) + "\n", encoding="utf-8")
        os.chmod(proposal_path, stat.S_IRUSR | stat.S_IWUSR)
        check_content.validate_content(candidate_content)
    except Exception:
        shutil.rmtree(proposal_dir)
        raise
    return proposal


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--staged-dir", required=True, type=Path,
                        help="private output from dev/prepare_recording.py")
    parser.add_argument("--approval", required=True, type=Path,
                        help="manually written approval record")
    parser.add_argument("--proposal-dir", required=True, type=Path,
                        help="new private proposal directory outside the source checkout")
    parser.add_argument("--content-dir", type=Path, default=CONTENT,
                        help=argparse.SUPPRESS)
    args = parser.parse_args()
    try:
        result = propose(args.staged_dir, args.approval, args.proposal_dir, args.content_dir)
    except (ValueError, OSError, KeyError, json.JSONDecodeError) as error:
        print(f"Recording proposal rejected: {error}", file=sys.stderr)
        return 2
    print(f"Approval record validated; proposal prepared at {args.proposal_dir}; not activated.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
