"""Private selection of an audition narrator using only public sample letters."""

from __future__ import annotations

from pathlib import Path
import json
import os
import re

from .episodes_audio import DEFAULT_AUDITION_MANIFEST, DEFAULT_VOICE_FILE
from .state_io import atomic_write_json


_LABELS = ('A', 'B', 'C', 'D', 'E', 'F')
_VOICE_ID = re.compile(r'^[A-Za-z][A-Za-z0-9_-]{0,63}$')


class VoiceSettingsError(ValueError):
    """Safe voice setting or manifest error; never includes a voice name."""


def voice_map(manifest_path: Path | str = DEFAULT_AUDITION_MANIFEST) -> dict[str, str]:
    """Load exactly the six private letter-to-voice mappings from the audition manifest."""
    path = Path(manifest_path).expanduser()
    if path.is_symlink():
        raise VoiceSettingsError('voice manifest is unavailable')
    try:
        manifest = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError, TypeError):
        raise VoiceSettingsError('voice manifest is unavailable') from None
    samples = manifest.get('samples') if isinstance(manifest, dict) else None
    if not isinstance(samples, list):
        raise VoiceSettingsError('voice manifest is invalid')
    mapping = {}
    voice_ids = set()
    for row in samples:
        if not isinstance(row, dict):
            continue
        label, voice_id = row.get('label'), row.get('voice')
        if label not in _LABELS or not isinstance(voice_id, str) or not _VOICE_ID.fullmatch(voice_id):
            continue
        if label in mapping or voice_id in voice_ids:
            raise VoiceSettingsError('voice manifest is invalid')
        mapping[label] = voice_id
        voice_ids.add(voice_id)
    if tuple(sorted(mapping)) != _LABELS or len(voice_ids) != len(_LABELS):
        raise VoiceSettingsError('voice manifest is invalid')
    return mapping


def _read_voice_id(path: Path) -> str | None:
    if path.is_symlink():
        raise VoiceSettingsError('voice settings are unavailable')
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
    except FileNotFoundError:
        return None
    except (OSError, ValueError, TypeError):
        raise VoiceSettingsError('voice settings are unavailable') from None
    if not isinstance(value, dict):
        raise VoiceSettingsError('voice settings are invalid')
    # Read older local settings during upgrade; every new write uses voice_id.
    voice_id = value.get('voice_id', value.get('voice_name', value.get('voice')))
    if not isinstance(voice_id, str) or not _VOICE_ID.fullmatch(voice_id):
        raise VoiceSettingsError('voice settings are invalid')
    return voice_id


def get_selection(*, voice_file: Path | str = DEFAULT_VOICE_FILE,
                  manifest_path: Path | str = DEFAULT_AUDITION_MANIFEST) -> dict:
    mapping = voice_map(manifest_path)
    voice_id = _read_voice_id(Path(voice_file).expanduser())
    if voice_id is None:
        return {'letter': 'A'}
    for letter, candidate in mapping.items():
        if candidate == voice_id:
            return {'letter': letter}
    raise VoiceSettingsError('saved voice is not in the audition manifest')


def letter_for_voice_id(voice_id: object, *,
                        manifest_path: Path | str = DEFAULT_AUDITION_MANIFEST) -> str:
    """Resolve a manifest ID for existing first-party clients without returning it."""
    if not isinstance(voice_id, str):
        raise VoiceSettingsError('choose a sample from A through F')
    for letter, candidate in voice_map(manifest_path).items():
        if candidate == voice_id:
            return letter
    raise VoiceSettingsError('choose a sample from A through F')


def _atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path.parent, 0o700)
    if path.is_symlink():
        raise VoiceSettingsError('voice settings are unavailable')
    atomic_write_json(path, payload, indent=None, separators=(',', ':'))


def set_selection(letter: object, *, voice_file: Path | str = DEFAULT_VOICE_FILE,
                  manifest_path: Path | str = DEFAULT_AUDITION_MANIFEST) -> dict:
    if not isinstance(letter, str) or letter not in _LABELS:
        raise VoiceSettingsError('choose a sample from A through F')
    mapping = voice_map(manifest_path)
    _atomic_json(Path(voice_file).expanduser(), {'voice_id': mapping[letter]})
    # The provider voice id stays private; API clients receive only the label.
    return {'letter': letter}
