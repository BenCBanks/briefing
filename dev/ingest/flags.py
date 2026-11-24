"""Validated runtime flags with environment, file and default provenance."""

import json
import os
from pathlib import Path
from .paths import checked_state_path


DEFAULTS = {
    'retrieval_fill': False,
    'search_first': False,
    'freshness_days': 90,
    'writer': True,
    'relevance': False,
    'evidence_packs': False,
    'extraction': False,
    # Full spoken rewrite is opt-in until fixture and live acceptance pass.
    'daily_narration': False,
    'episode_streaming': False,
    'episodes': 'real',
    # Additional bounded generation controls are reported with the main flags.
    'relevance_tune': False,
    'candidate_attempt_limit': 30,
    # The private preview runs with TasksMax=16. Two writers leave room for the
    # extraction pool and one HTTPS timeout helper per in-flight model call.
    'story_writer_workers': 2,
    'gemini_calls_per_run': 40,
}
_INTEGER_RANGES = {'freshness_days': (1, 365), 'candidate_attempt_limit': (1, 30),
                   'story_writer_workers': (1, 4), 'gemini_calls_per_run': (1, 40)}
_CHOICES = {'episodes': frozenset({'real', 'fake'})}
FLAG_NAMES = tuple(sorted(DEFAULTS))


def flags_path():
    """Return the one documented runtime flags file location."""
    explicit = os.environ.get('BRIEFING_FLAGS_FILE')
    if explicit:
        return Path(explicit).expanduser()
    state_dir = os.environ.get('BRIEFING_STATE_DIR')
    if os.environ.get('BRIEFING_TESTING') == '1' and not state_dir:
        return None
    return checked_state_path(state_dir or (Path.home() / '.local/state/briefing')) / 'flags.json'


def _coerce(name, raw, *, environment=False):
    if name in _INTEGER_RANGES:
        if environment:
            try:
                value = int(str(raw).strip())
            except (TypeError, ValueError):
                return None
        else:
            value = raw
        low, high = _INTEGER_RANGES[name]
        return value if type(value) is int and low <= value <= high else None
    if name in _CHOICES:
        value = str(raw).strip().lower()
        return value if value in _CHOICES[name] else None
    if environment:
        value = str(raw).strip().lower()
        return {'on': True, 'off': False}.get(value)
    if type(raw) is bool:
        return raw
    if isinstance(raw, str) and raw.strip().lower() in {'on', 'off'}:
        return raw.strip().lower() == 'on'
    return None


def setting_details(name):
    """Return an effective value with its winning source and documented default."""
    if name not in DEFAULTS:
        raise ValueError('unknown feature flag')
    default = DEFAULTS[name]
    env_key = 'BRIEFING_' + name.upper()
    if env_key in os.environ:
        value = _coerce(name, os.environ[env_key], environment=True)
        if value is not None:
            return {'value': value, 'default': default, 'source': 'env'}

    path = flags_path()
    if path is not None:
        try:
            if path.stat().st_size <= 16384:
                data = json.loads(path.read_text(encoding='utf-8'))
                if isinstance(data, dict) and name in data:
                    value = _coerce(name, data[name])
                    if value is not None:
                        return {'value': value, 'default': default, 'source': 'file'}
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, TypeError):
            pass
    return {'value': default, 'default': default, 'source': 'default'}


def setting(name):
    """Read one validated boolean, integer, or enum flag."""
    return setting_details(name)['value']


def effective_flags():
    """Return known flags only, with values, defaults and provenance."""
    return {name: setting_details(name) for name in FLAG_NAMES}


def non_default_flags():
    """Return effective flag details that differ from their documented defaults."""
    return {name: value for name, value in effective_flags().items()
            if value['value'] != value['default']}


def flag_enabled(name):
    if name not in DEFAULTS or name in _INTEGER_RANGES or name in _CHOICES:
        raise ValueError('unknown feature flag')
    return setting(name) is True


def freshness_days():
    return setting('freshness_days')
