#!/usr/bin/env python3
"""Export exact, revision-checked narration text without calling a voice provider."""

import argparse
import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONTENT = ROOT / "content"


def find_item(item_id):
    files = (("editions.json", "editions"), ("deep-dives.json", "deep_dives"))
    matches = [item for filename, key in files
               for item in json.loads((CONTENT / filename).read_text())[key]
               if item["id"] == item_id]
    if not matches:
        raise ValueError(f"No narration item named {item_id!r}")
    revisions = [item.get("revision_id") for item in matches]
    if any(not revision for revision in revisions) or len(set(revisions)) != len(revisions):
        raise ValueError(f"Narration item {item_id!r} has invalid or duplicate revisions")
    superseded = {
        correction.get("previous_revision_id")
        for item in matches for correction in item.get("corrections", [])
        if correction.get("previous_revision_id")
    }
    current = [item for item in matches
               if item["revision_id"] not in superseded
               and item.get("status") not in {"superseded", "withdrawn"}
               and item.get("superseded") is not True
               and item.get("withdrawn") is not True
               and not item.get("superseded_by")
               and not item.get("withdrawn_at")]
    if len(current) != 1:
        raise ValueError(f"Narration item {item_id!r} needs exactly one current revision; found {len(current)}")
    return current[0]


def export(item, max_chars, output_dir):
    if max_chars < 100:
        raise ValueError("--max-chars must be at least 100")
    if not item.get("revision_id") or not item.get("script_hash"):
        raise ValueError("Item needs a revision and script hash")
    sections = item.get("sections") or []
    if not sections:
        raise ValueError("Item has no script sections")
    units = [(section["id"], f"{section['heading']}\n{section['body']}") for section in sections]
    script = "\n\n".join(text for _, text in units)
    actual_hash = hashlib.sha256(script.encode("utf-8")).hexdigest()
    if actual_hash != item["script_hash"]:
        raise ValueError("Script changed since its recorded SHA-256; review and revise before export")
    audio = item.get("audio") or {}
    if audio.get("revision_id") != item["revision_id"] or audio.get("script_hash") != actual_hash:
        raise ValueError("Audio metadata does not match this script revision")

    groups = []
    current = []
    for section_id, text in units:
        if len(text) > max_chars:
            raise ValueError(f"Section {section_id!r} exceeds {max_chars} characters")
        proposed = "\n\n".join(part for _, part in current + [(section_id, text)])
        if current and len(proposed) > max_chars:
            groups.append(current)
            current = []
        current.append((section_id, text))
    if current:
        groups.append(current)

    segments = ["\n\n".join(text for _, text in group) for group in groups]
    if "\n\n".join(segments) != script:
        raise AssertionError("Exported segments no longer match the approved script")
    if output_dir.exists() and any(output_dir.iterdir()):
        raise ValueError(f"Output directory is not empty: {output_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)

    manifest = {"item_id": item["id"], "revision_id": item["revision_id"],
                "script_sha256": actual_hash, "characters": len(script), "segments": []}
    for index, (group, text) in enumerate(zip(groups, segments), start=1):
        filename = f"segment-{index:02d}.txt"
        (output_dir / filename).write_text(text)
        manifest["segments"].append({"file": filename, "characters": len(text),
                                     "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                                     "section_ids": [section_id for section_id, _ in group]})
    (output_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--item", required=True, help="Edition or explanation ID")
    parser.add_argument("--max-chars", type=int, default=10000,
                        help="Maximum characters in one section-aligned segment")
    parser.add_argument("--out-dir", required=True, type=Path,
                        help="Empty private directory for the text and manifest")
    args = parser.parse_args()
    result = export(find_item(args.item), args.max_chars, args.out_dir)
    print(f"Exported {result['item_id']} revision {result['revision_id']}: "
          f"{result['characters']} characters in {len(result['segments'])} segment(s)")


if __name__ == "__main__":
    main()
