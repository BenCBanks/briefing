"""Resolve optional reader-selected local sources outside the public repository."""

import json
import os
from pathlib import Path


def configured_path():
    explicit = os.environ.get('BRIEFING_LOCAL_SOURCES')
    if explicit:
        return Path(explicit).expanduser()
    data_dir = os.environ.get('BRIEFING_DATA_DIR')
    if data_dir:
        return Path(data_dir).expanduser() / 'local-sources.json'
    return Path.home() / '.local' / 'share' / 'briefing' / 'local-sources.json'


def load(path=None):
    """Load the optional document; its absence means no local sources are configured."""
    source_path = Path(path).expanduser() if path is not None else configured_path()
    try:
        return json.loads(source_path.read_text(encoding='utf-8'))
    except FileNotFoundError:
        return None
