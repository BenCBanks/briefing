#!/usr/bin/env python3
"""Generate a short deterministic PCM WAV used only by native simulator tests."""

import math
import struct
import sys
import wave
from pathlib import Path


def main() -> None:
    destination = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("mobile/ios/dev/generated/playback-fixture.wav")
    destination.parent.mkdir(parents=True, exist_ok=True)
    sample_rate = 22_050
    duration_seconds = 20
    amplitude = 3_200
    with wave.open(str(destination), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(sample_rate)
        samples = (
            struct.pack("<h", int(amplitude * math.sin(2 * math.pi * 440 * i / sample_rate)))
            for i in range(sample_rate * duration_seconds)
        )
        output.writeframes(b"".join(samples))


if __name__ == "__main__":
    main()
