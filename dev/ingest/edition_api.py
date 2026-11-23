"""Read the latest generated edition for the preview app, with a safe sample fallback."""

from pathlib import Path
import json

from . import generation_api
from .core import STATE_DIR
from .paths import (legacy_migration_allowed, legacy_profile_owner, migrate_legacy_generated_files,
                    profile_identifier, set_legacy_profile_owner)

GENERATED_DIR = generation_api.DEFAULT_EDITION_DIR
LEGACY_GENERATED_DIR = STATE_DIR / 'generated'


def _history_rows(directory, seen=()):
    try:
        history_bundles = json.loads((directory / 'editions-history.json').read_text(encoding='utf-8'))
    except (OSError, ValueError, TypeError):
        return []
    found = set(seen)
    rows = []
    for previous in history_bundles if isinstance(history_bundles, list) else []:
        if not isinstance(previous, dict):
            continue
        edition_bundle = previous.get('editions')
        source_bundle = previous.get('sources')
        previous_editions = edition_bundle.get('editions', []) if isinstance(edition_bundle, dict) else []
        previous_sources = source_bundle.get('sources', []) if isinstance(source_bundle, dict) else []
        edition = next((row for row in previous_editions if isinstance(row, dict) and row.get('generated') is True
                        and row.get('kind') == 'daily'), None) if isinstance(previous_editions, list) else None
        if not edition or edition.get('revision_id') in found or not isinstance(previous_sources, list):
            continue
        previous_source_ids = {source.get('id') for source in previous_sources if isinstance(source, dict)}
        if any(not isinstance(section, dict) or not set(section.get('source_ids', [])) <= previous_source_ids
               for section in edition.get('sections', [])):
            continue
        found.add(edition.get('revision_id'))
        rows.append({'edition': edition, 'sources': previous_sources})
    return rows


def _legacy_words_only_profile(path=None):
    """Recognize the pre-profile-id file only while it still owns legacy output."""
    try:
        value = json.loads(Path(path or generation_api.PROFILE_PATH).read_text(encoding='utf-8'))
    except (OSError, ValueError, TypeError):
        return False
    return isinstance(value, dict) and set(value) == {'words'} and isinstance(value.get('words'), str)


def edition_response(directory=None, profile='default', *, include_history=True):
    if type(include_history) is not bool:
        raise ValueError('include_history must be a boolean')
    use_state_default = directory is None and profile != 'active'
    legacy_words_only = False
    if directory is None and profile == 'active':
        try:
            current_profile = generation_api.load_profile()
            directory = generation_api.profile_generated_dir(current_profile)
            if not (directory / 'current.json').is_file() and not (directory / 'editions.json').is_file():
                if not (LEGACY_GENERATED_DIR / 'legacy-profile-owner.json').exists():
                    set_legacy_profile_owner(profile_identifier(current_profile), LEGACY_GENERATED_DIR)
                if legacy_migration_allowed(current_profile, LEGACY_GENERATED_DIR):
                    migrate_legacy_generated_files(LEGACY_GENERATED_DIR, directory)
        except (OSError, ValueError, TypeError):
            owner = legacy_profile_owner(LEGACY_GENERATED_DIR)
            legacy_words_only = _legacy_words_only_profile() and owner is None
            if legacy_words_only:
                migrate_legacy_generated_files(LEGACY_GENERATED_DIR, GENERATED_DIR)
            directory = LEGACY_GENERATED_DIR if legacy_words_only and owner is None else GENERATED_DIR
    directory = Path(directory or GENERATED_DIR)
    if use_state_default or directory == Path(GENERATED_DIR):
        migrate_legacy_generated_files(LEGACY_GENERATED_DIR, GENERATED_DIR)
    try:
        try:
            bundle = json.loads((directory / 'current.json').read_text(encoding='utf-8'))
            editions = bundle['editions']
            sources = bundle['sources']
        except FileNotFoundError:
            editions = json.loads((directory / 'editions.json').read_text(encoding='utf-8'))
            sources = json.loads((directory / 'sources.json').read_text(encoding='utf-8'))
        rows = editions.get('editions')
        source_rows = sources.get('sources')
        current = rows[0] if isinstance(rows, list) and rows else None
        if not isinstance(current, dict) or current.get('generated') is not True or current.get('kind') != 'daily':
            history = _history_rows(directory) if include_history else []
            return 200, {'generated': False, 'edition': None, 'sources': [], 'history': history}
        if profile == 'active':
            if not legacy_words_only:
                current_profile = generation_api.load_profile()
                active_id = profile_identifier(current_profile)
                edition_profile_id = current.get('profile_id')
                if edition_profile_id and edition_profile_id != active_id:
                    return 200, {'generated': False, 'edition': None, 'sources': [], 'history': []}
                owner = legacy_profile_owner(LEGACY_GENERATED_DIR)
                if not edition_profile_id and (LEGACY_GENERATED_DIR / 'legacy-profile-owner.json').exists() and owner != active_id:
                    return 200, {'generated': False, 'edition': None, 'sources': [], 'history': []}
                if not edition_profile_id and not (LEGACY_GENERATED_DIR / 'legacy-profile-owner.json').exists():
                    set_legacy_profile_owner(active_id, LEGACY_GENERATED_DIR)
        if not isinstance(source_rows, list):
            return 500, {'error': 'generated_sources_invalid'}
        source_ids = {source.get('id') for source in source_rows if isinstance(source, dict)}
        for section in current.get('sections', []):
            if not isinstance(section, dict) or not set(section.get('source_ids', [])) <= source_ids:
                return 500, {'error': 'generated_source_reference_invalid'}
        history = _history_rows(directory, {current.get('revision_id')}) if include_history else []
        return 200, {'generated': True, 'edition': current, 'sources': source_rows, 'history': history}
    except FileNotFoundError:
        history = _history_rows(directory) if include_history else []
        return 200, {'generated': False, 'edition': None, 'sources': [], 'history': history}
    except (OSError, ValueError, TypeError):
        return 500, {'error': 'generated_edition_unavailable'}
