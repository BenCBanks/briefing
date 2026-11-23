#!/usr/bin/env python3
"""Render one fictional briefing passage in six Gemini stock voices."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dev.ingest.tts import CostCounter, GeminiTtsProvider, TtsProviderError
from dev.ingest.tts.gemini import DEFAULT_MODEL, list_tts_models


PASSAGE = """Good morning. Here’s your briefing for today.

We’ll start with a small change in a fictional town called Alder Bay. The council has approved a pilot for shared delivery lockers near its library and train station. The idea is simple: residents can collect packages on one trip, and drivers make fewer stops across town. The pilot will run through the spring. The council says it’ll review missed deliveries, operating costs, and resident feedback before deciding what comes next.

Meanwhile, the local credit union is testing clearer notices for transfers that take more than a day. Members will see when a payment is received, when it’s being checked, and when it’s ready to use. That won’t make every transfer faster, but it should make the wait easier to understand.

That’s the briefing. We’ll keep following what changes, and what the evidence says."""

VOICE_SAMPLES = (
    ('A', 'Sulafat'),
    ('B', 'Charon'),
    ('C', 'Kore'),
    ('D', 'Orus'),
    ('E', 'Achernar'),
    ('F', 'Rasalgethi'),
)
DEFAULT_OUTPUT_DIR = Path(os.environ.get('BRIEFING_DATA_DIR') or Path.home() / '.local/share/briefing') / 'audio-auditions'


def _to_mp3(wav_bytes: bytes, executable: str) -> bytes:
    result = subprocess.run(
        [executable, '-hide_banner', '-loglevel', 'error', '-f', 'wav', '-i', 'pipe:0',
         '-codec:a', 'libmp3lame', '-b:a', '64k', '-ar', '24000', '-ac', '1',
         '-f', 'mp3', 'pipe:1'],
        input=wav_bytes, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
    )
    if result.returncode or not result.stdout:
        raise RuntimeError('ffmpeg could not convert the audition WAV.')
    return result.stdout


def render_auditions(output_dir: Path = DEFAULT_OUTPUT_DIR, *, model: str = DEFAULT_MODEL,
                     provider: GeminiTtsProvider | None = None, ffmpeg: str | None = None) -> dict:
    output_dir = Path(output_dir).expanduser()
    output_dir.mkdir(parents=True, exist_ok=True)
    provider = provider or GeminiTtsProvider(model=model)
    available = {row.name for row in list_tts_models(provider.key_path, timeout=provider.timeout,
                                                       transport=provider._transport)}
    if model not in available:
        raise RuntimeError(f'TTS model is not available to the configured key: {model}')

    ffmpeg = ffmpeg if ffmpeg is not None else shutil.which('ffmpeg')
    extension = 'mp3' if ffmpeg else 'wav'
    manifest = {'format_version': 1, 'model': model, 'passage': PASSAGE,
                'samples': [], 'character_count_per_sample': len(PASSAGE)}
    counter = CostCounter()
    for label, voice_name in VOICE_SAMPLES:
        result = provider.synthesize(PASSAGE, voice_name)
        cost = counter.record(result.character_count, result.duration_seconds)
        audio_bytes = _to_mp3(result.audio_bytes, ffmpeg) if ffmpeg else result.audio_bytes
        filename = f'{voice_name.lower()}.{extension}'
        target = output_dir / filename
        target.write_bytes(audio_bytes)
        manifest['samples'].append({
            'label': label,
            'voice': voice_name,
            'file': filename,
            'duration_seconds': round(result.duration_seconds, 3),
            'character_count': result.character_count,
            'estimated_cost_usd': f'{cost:.8f}',
        })
        print(f"Sample {label}: {result.character_count} characters, "
              f"{result.duration_seconds:.1f} seconds, estimated ${cost:.6f}")

    manifest['total_character_count'] = counter.character_count
    manifest['total_duration_seconds'] = round(float(counter.audio_seconds), 3)
    manifest['estimated_cost_usd'] = f'{counter.estimated_cost_usd:.8f}'
    manifest_path = output_dir / 'manifest.json'
    manifest_path.write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
    print(f"Total: {counter.character_count} characters across six samples; "
          f"{float(counter.audio_seconds):.1f} seconds; estimated ${counter.estimated_cost_usd:.6f}")
    print(f'Audio format: {extension.upper()}')
    print(f'Output: {output_dir}')
    return manifest


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument('--model', default=DEFAULT_MODEL)
    args = parser.parse_args(argv)
    try:
        render_auditions(args.output_dir, model=args.model)
    except (OSError, RuntimeError, TtsProviderError) as error:
        print(f'TTS audition failed: {error}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
