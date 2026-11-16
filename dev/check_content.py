#!/usr/bin/env python3
"""Validate briefing content and verify recorded audio against its stored checksum."""

import argparse
import hashlib
import json
from pathlib import Path


CONTENT = Path(__file__).resolve().parents[1] / "content"
SHA256_LENGTH = 64


def require(condition, message):
    if not condition:
        raise ValueError(message)


def rows(name, key, revisioned=False, content_dir=None):
    root = Path(content_dir) if content_dir is not None else CONTENT
    path = root / f"{name}.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    result = data.get(key)
    require(isinstance(result, list) and result, f"{name}: empty or invalid list")
    ids = [row["id"] for row in result]
    if revisioned:
        revisions = [(row["id"], row.get("revision_id")) for row in result]
        require(all(item_id and revision for item_id, revision in revisions),
                f"{name}: missing identity or revision")
        require(len(revisions) == len(set(revisions)), f"{name}: duplicate item revision")
        for item_id in set(ids):
            candidates = [row for row in result if row["id"] == item_id]
            superseded = {correction.get("previous_revision_id") for row in candidates
                          for correction in row.get("corrections", []) if correction.get("previous_revision_id")}
            current = [row for row in candidates if row["revision_id"] not in superseded
                       and row.get("status") not in {"superseded", "withdrawn"}
                       and row.get("superseded") is not True and row.get("withdrawn") is not True
                       and not row.get("superseded_by") and not row.get("withdrawn_at")]
            require(len(current) <= 1, f"{name}: {item_id} has ambiguous current revisions")
    else:
        require(len(ids) == len(set(ids)), f"{name}: duplicate IDs")
    return result


def file_sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_content(content_dir=CONTENT):
    content_dir = Path(content_dir)
    topics = rows("topics", "topics", content_dir=content_dir)
    dives = rows("deep-dives", "deep_dives", revisioned=True, content_dir=content_dir)
    sources = rows("sources", "sources", content_dir=content_dir)
    editions = rows("editions", "editions", revisioned=True, content_dir=content_dir)
    topic_ids = {row["id"] for row in topics}
    source_ids = {row["id"] for row in sources}

    require({row["topic_id"] for row in dives} == topic_ids, "every topic needs one explanation")
    require({row["kind"] for row in editions} == {"daily", "weekly"},
            "daily and weekly samples required")

    content_root = content_dir.resolve()
    for item in editions + dives:
        audio = item["audio"]
        require(audio["revision_id"] == item["revision_id"],
                f"{item['id']}: audio revision differs")
        require(audio["script_hash"] == item["script_hash"],
                f"{item['id']}: audio script differs")
        script = "\n\n".join(f"{section['heading']}\n{section['body']}"
                               for section in item["sections"])
        expected_hash = hashlib.sha256(script.encode("utf-8")).hexdigest()
        require(item["script_hash"] == expected_hash, f"{item['id']}: script hash is stale")
        is_daily = item in editions and item.get("kind") == "daily"
        if audio["state"] == "recorded":
            require(is_daily, f"{item['id']}: only daily editions may have recorded audio")
            asset_hash = audio.get("audio_sha256", "")
            require(isinstance(asset_hash, str) and len(asset_hash) == SHA256_LENGTH
                    and all(char in "0123456789abcdef" for char in asset_hash),
                    f"{item['id']}: recorded audio needs a valid SHA-256")
            asset = audio.get("url", "")
            require(isinstance(asset, str) and asset.startswith("/content/audio/")
                    and "\\" not in asset and ".." not in asset.split("/"),
                    f"{item['id']}: audio path invalid")
            asset_path = (content_root / asset.removeprefix("/content/")).resolve()
            audio_root = (content_root / "audio").resolve()
            require(asset_path.is_relative_to(audio_root) and asset_path.is_file(),
                    f"{item['id']}: recording missing")
            require(file_sha256(asset_path) == asset_hash,
                    f"{item['id']}: recording SHA-256 does not match its bytes")
        else:
            require(audio["state"] == "pending" and not audio.get("url"),
                    f"{item['id']}: invalid pending audio")
        if not is_daily:
            require(audio["state"] == "pending" and not audio.get("url"),
                    f"{item['id']}: weekly editions and explanations must remain pending")

        sections = item["sections"]
        require(sections and len({row["id"] for row in sections}) == len(sections),
                f"{item['id']}: section IDs invalid")
        for section in sections:
            require(section["body"].strip(), f"{item['id']}: empty section")
            require(set(section["source_ids"]) <= source_ids,
                    f"{item['id']}: unknown source")
            require(set(section.get("topic_ids", [])) <= topic_ids,
                    f"{item['id']}: unknown topic")

    for edition in editions:
        coverage = edition["coverage"]
        require(len(coverage) == len(topics), f"{edition['id']}: duplicate or missing coverage")
        require({row["topic_id"] for row in coverage} == topic_ids,
                f"{edition['id']}: missing topic coverage")

    return {"topics": len(topics), "deep_dives": len(dives),
            "editions": len(editions), "sources": len(sources)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--content-dir", type=Path, default=CONTENT,
                        help="content directory to validate (defaults to app content)")
    args = parser.parse_args()
    counts = validate_content(args.content_dir)
    print(f"Content valid: {counts['topics']} topics, {counts['deep_dives']} explanations, "
          f"{counts['editions']} editions, {counts['sources']} references.")


if __name__ == "__main__":
    main()
