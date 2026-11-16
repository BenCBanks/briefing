#!/usr/bin/env python3
"""Generate the name-independent 1024px app icon using only Python's standard library."""

from pathlib import Path
import struct
import zlib

SIZE = 1024
INK = (17, 17, 19)
IVORY = (244, 244, 245)
OUTPUT = Path(__file__).resolve().parents[1] / "BriefingApp/Assets.xcassets/AppIcon.appiconset/AppIcon.png"


def is_inside_round_rect(x, y, left, top, width, height, radius):
    right, bottom = left + width, top + height
    if not (left <= x < right and top <= y < bottom):
        return False
    cx = min(max(x, left + radius), right - radius - 1)
    cy = min(max(y, top + radius), bottom - radius - 1)
    return (x - cx) ** 2 + (y - cy) ** 2 <= radius ** 2


def chunk(tag, data):
    return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)


def main():
    bars = ((257, 331, 510, 70), (257, 465, 430, 70), (257, 599, 330, 70))
    rows = bytearray()
    for y in range(SIZE):
        rows.append(0)
        for x in range(SIZE):
            color = IVORY if any(is_inside_round_rect(x, y, *bar, 18) for bar in bars) else INK
            rows.extend(color)
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    payload = bytearray(b"\x89PNG\r\n\x1a\n")
    payload.extend(chunk(b"IHDR", struct.pack(">IIBBBBB", SIZE, SIZE, 8, 2, 0, 0, 0)))
    payload.extend(chunk(b"IDAT", zlib.compress(bytes(rows), 9)))
    payload.extend(chunk(b"IEND", b""))
    OUTPUT.write_bytes(payload)
    print(OUTPUT)


if __name__ == "__main__":
    main()
