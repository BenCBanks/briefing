"""Render a correction-safe, five-minute daily edition as chaptered audio."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import logging
import math
from pathlib import Path
import re
import os

from .episodes_audio import (DEFAULT_VOICE_FILE, EpisodeAudioError, current_audio_asset,
                             has_bounded_audio_identity, load_voice, persisted_audio_matches_manifest,
                             persisted_audio_selector, render_episode_audio)
from .paths import STATE_DIR, checked_state_path, profile_identifier
from .tts.normalize import normalize_narration
from .tts.gemini import DEFAULT_MODEL
from .usage import UsageLedger, process_ledger, reset_usage_caller, usage_caller


def _spoken_section(section: dict) -> str:
    value = section.get('audio_script')
    if isinstance(value, dict):
        value = value.get('spoken_text', value.get('script', value.get('text')))
    if not isinstance(value, str) or not value.strip():
        value = section.get('body', '')
    if not isinstance(value, str):
        return ''
    value = re.sub(r'\[\s*\d+\s*\]', '', value)
    value = re.sub(r'(?im)^\s*(?:citations?|limits|sources?)\s*:?\s*.*$', '', value)
    value = re.sub(r'(?i)\b(?:citations?|limits|sources?)\s*:\s*.*$', '', value)
    value = ' '.join(value.split()).strip()
    return re.sub(r'\s+([,.!?;:])', r'\1', value)


def daily_episode_from_edition(edition: dict) -> dict:
    """Build the stable spoken chapter payload shared with the daily audio route."""
    if not isinstance(edition, dict):
        raise ValueError('edition is required')
    sections = edition.get('sections')
    if not isinstance(sections, list):
        raise ValueError('edition sections are unavailable')
    daily = edition.get('daily_briefing') if isinstance(edition.get('daily_briefing'), dict) else {}
    text_only = daily.get('text_only') is True
    section_by_cluster = {
        section.get('cluster_id'): section for section in sections
        if isinstance(section, dict) and isinstance(section.get('cluster_id'), str)
    }
    spoken_order = []
    for row in ([] if text_only else daily.get('briefing_script', [])):
        section_id = row.get('section_id') if isinstance(row, dict) else None
        if (isinstance(row, dict) and row.get('kind') == 'story_intro' and
                section_id in section_by_cluster and
                section_id not in spoken_order):
            spoken_order.append(section_id)
    ordered_sections = [section_by_cluster[section_id] for section_id in spoken_order]
    if not text_only:
        ordered_sections.extend(section for section in sections if section not in ordered_sections)
    chapters = []
    used_ids = set()
    for index, section in enumerate(ordered_sections, 1):
        if not isinstance(section, dict) or not section.get('cluster_id'):
            continue
        title = section.get('heading') or section.get('headline') or f'Story {index}'
        if not isinstance(title, str) or not title.strip():
            title = f'Story {index}'
        chapter_id = re.sub(r'[^A-Za-z0-9_-]+', '-', str(section.get('id') or f'story-{index}')).strip('-')
        if not chapter_id or not chapter_id[0].isalnum():
            chapter_id = f'story-{index}'
        chapter_id = chapter_id[:64]
        if chapter_id in used_ids:
            chapter_id = f'{chapter_id[:54]}-{index}'
        used_ids.add(chapter_id)
        text = _spoken_section(section)
        if text:
            audio_script = section.get('audio_script')
            includes_title = isinstance(audio_script, dict) and audio_script.get('includes_title') is True
            chapters.append({'id': chapter_id, 'title': title[:180],
                             'spoken_text': text if includes_title else f'{title.strip()}. {text}'})
    for index, segment in enumerate([] if text_only else daily.get('background_segments', []), 1):
        if not isinstance(segment, dict):
            continue
        title = 'Worth knowing: ' + str(segment.get('topic_id') or 'background').replace('-', ' ')
        script = segment.get('audio_script')
        if isinstance(script, dict):
            text = str(script.get('spoken_text') or '').strip()
            includes_title = script.get('includes_title') is True
        else:
            text = ' '.join(str(row.get('text') or '') for row in segment.get('sentences', [])
                            if isinstance(row, dict))
            includes_title = False
        chapter_id = re.sub(r'[^A-Za-z0-9_-]+', '-',
                            str(segment.get('segment_id') or f'background-{index}')).strip('-')[:64]
        if not chapter_id or chapter_id in used_ids:
            chapter_id = f'background-{index}'
        used_ids.add(chapter_id)
        if text:
            chapters.append({'id': chapter_id, 'title': title[:180],
                             'spoken_text': text if includes_title else f'{title}. {text}'})
    target_duration = edition.get('duration_minutes') or daily.get('duration_minutes') or 5
    edition_id = edition.get('id') or f"daily-{edition.get('local_date', 'current')}"
    return {'id': str(edition_id), 'revision_id': str(edition.get('revision_id') or edition.get('revision') or ''),
            'title': edition.get('title') or 'Today’s briefing', 'duration_minutes': target_duration,
            'made_date': edition.get('local_date'), 'chapters': chapters,
            'sources': edition.get('sources') if isinstance(edition.get('sources'), list) else []}


def _late_audio_response(current_audio, current_episode, *, audio_changed: bool,
                         verified_asset=None) -> dict:
    status = current_audio.get('audio_status') if isinstance(current_audio, dict) else None
    if status == 'failed':
        reason = current_audio.get('reason')
        if not isinstance(reason, str) or not re.fullmatch(r'(?:synthesis_failed|render_failed|[A-Z][A-Za-z0-9]{0,39})', reason):
            reason = 'render_failed'
        return {'audio_status': 'failed', 'audio_url': None, 'duration_seconds': None,
                'file_size_bytes': None, 'ready_at': None, 'resumed': False,
                'chapter_offsets': [], 'reason': reason}

    if status == 'ready' and audio_changed and isinstance(current_episode, dict):
        asset_state, manifest, audio_path = verified_asset or (None, None, None)
        url = current_audio.get('audio_url')
        voice_name = current_audio.get('voice_name')
        chapters = current_episode.get('chapters')
        expected_ids = [chapter.get('id') for chapter in chapters] if isinstance(chapters, list) else []
        offsets = manifest.get('chapters') if isinstance(manifest, dict) else None
        valid_manifest = (asset_state == 'ready' and isinstance(manifest, dict) and
                          isinstance(audio_path, Path) and isinstance(offsets, list) and
                          len(offsets) == len(expected_ids) and
                          manifest.get('episode_id') == current_episode.get('id') and
                          manifest.get('revision_id') == current_episode.get('revision_id') and
                          manifest.get('voice_name') == voice_name and
                          current_audio.get('revision_id', manifest.get('revision_id')) == manifest.get('revision_id') and
                          current_audio.get('script_hash', manifest.get('script_hash')) == manifest.get('script_hash') and
                          url == '/api/daily/audio')
        if valid_manifest and has_bounded_audio_identity(current_audio):
            valid_manifest = persisted_audio_matches_manifest(current_audio, manifest)
        try:
            audio_size = audio_path.stat().st_size if isinstance(audio_path, Path) else None
        except OSError:
            audio_size = None
        if valid_manifest:
            valid_manifest = all(
                isinstance(row, dict) and row.get('chapter_id') == chapter_id and
                type(row.get('start_seconds')) in (int, float) and math.isfinite(row['start_seconds']) and
                type(row.get('duration_seconds')) in (int, float) and math.isfinite(row['duration_seconds'])
                for row, chapter_id in zip(offsets, expected_ids, strict=True)
            )
        duration = manifest.get('duration_seconds') if isinstance(manifest, dict) else None
        if (valid_manifest and audio_size is not None and type(duration) in (int, float) and
                math.isfinite(duration) and duration > 0):
            return {'audio_status': 'ready', 'audio_url': url, 'duration_seconds': duration,
                    'file_size_bytes': audio_size,
                    'ready_at': current_audio.get('ready_at'),
                    'resumed': current_audio.get('resumed') is True,
                    'chapter_offsets': [{'chapter_id': row['chapter_id'],
                                         'start_seconds': row['start_seconds'],
                                         'duration_seconds': row['duration_seconds']}
                                        for row in offsets],
                    'voice_name': voice_name,
                    **({key: current_audio.get(key) for key in ('provider', 'model')}
                       if has_bounded_audio_identity(current_audio) else {}),
                    **{key: current_audio[key] for key in (
                        'asset_key', 'episode_id', 'batch_id', 'tts_output_token_limit', 'max_segment_attempts',
                        'coverage_status', 'human_listening_approval') if key in current_audio}}

    current_reason = current_audio.get('reason') if isinstance(current_audio, dict) else None
    if current_reason not in {'daily_budget', 'tts_disabled', 'no_spoken_stories', 'synthesis_failed'}:
        current_reason = 'superseded'
    return {'audio_status': 'pending', 'audio_url': None, 'duration_seconds': None,
            'file_size_bytes': None, 'ready_at': None, 'resumed': False,
            'chapter_offsets': [], 'reason': current_reason}


def _late_audio_result(edition: dict, episode: dict, initial_audio, *, profile_id,
                       state_dir, provider_name, model_name):
    """Return the state already attached to the edition if it changed mid-render."""
    try:
        current_episode = daily_episode_from_edition(edition)
    except (TypeError, ValueError):
        current_episode = None
    current_audio = edition.get('audio') if isinstance(edition.get('audio'), dict) else None
    audio_changed = current_audio != initial_audio
    if current_episode != episode or audio_changed:
        verified_asset = None
        voice_name = current_audio.get('voice_name') if isinstance(current_audio, dict) else None
        if (current_audio and current_audio.get('audio_status') == 'ready' and
                isinstance(current_episode, dict) and isinstance(profile_id, str) and
                isinstance(voice_name, str)):
            try:
                selector = persisted_audio_selector(current_episode, current_audio)
                verified_asset = current_audio_asset(
                    current_episode, profile_id, voice_name=voice_name, state_dir=state_dir,
                    provider_name=provider_name, model_name=model_name, **selector)
                if selector and verified_asset[1] and not persisted_audio_matches_manifest(
                        current_audio, verified_asset[1]):
                    verified_asset = ('stale', None, None)
            except (OSError, TypeError, ValueError):
                verified_asset = None
        return _late_audio_response(current_audio, current_episode, audio_changed=audio_changed,
                                    verified_asset=verified_asset)
    return None


def render_daily_audio(edition: dict, *, provider=None, voice_file: Path | str = DEFAULT_VOICE_FILE,
                       state_dir: Path | str = STATE_DIR, profile=None, ledger=None,
                       renderer=render_episode_audio) -> dict:
    """Render one WAV chapter per story using only the daily run's reserved cap.

    A budget refusal or disabled TTS leaves narration pending while preserving
    the text edition. A render error is reported as failed without exposing
    provider details. Successful daily audio is ``ready`` (the underlying
    chaptered audio manifest keeps its own ``recorded`` state).
    """
    state_dir = checked_state_path(state_dir)
    episode = daily_episode_from_edition(edition)
    initial_audio = deepcopy(edition.get('audio')) if isinstance(edition.get('audio'), dict) else None
    profile_id = None
    provider_name = getattr(provider, 'provider_name', 'gemini')
    model_name = getattr(provider, 'model', DEFAULT_MODEL)
    if not episode['chapters']:
        return {'audio_status': 'pending', 'audio_url': None, 'duration_seconds': None,
                'chapter_offsets': [], 'reason': 'no_spoken_stories'}
    if os.environ.get('BRIEFING_TTS', 'enabled').strip().lower() in {'0', 'false', 'off', 'disabled'}:
        return {'audio_status': 'pending', 'audio_url': None, 'duration_seconds': None,
                'chapter_offsets': [], 'reason': 'tts_disabled'}
    try:
        if profile is None:
            from .generation_api import load_profile
            profile = load_profile()
        profile_id = profile_identifier(profile)
        ledger = ledger or process_ledger(UsageLedger)
        voice = load_voice(voice_file)
        char_count = sum(len(normalize_narration(chapter['spoken_text'])) for chapter in episode['chapters'])
        expected_duration = max(float(episode['duration_minutes']) * 60, char_count / 12)
        tts_cost = ledger.estimate_tts_cost(char_count, expected_duration)
        if tts_cost > ledger.daily_audio_budget_remaining():
            return {'audio_status': 'pending', 'audio_url': None, 'duration_seconds': None,
                    'chapter_offsets': [], 'reason': 'daily_budget'}
        token = usage_caller('daily')
        try:
            try:
                result = renderer(episode, profile_id, provider=provider, voice_name=voice,
                                  voice_file=voice_file, state_dir=state_dir)
            except EpisodeAudioError:
                logging.getLogger(__name__).warning(
                    'daily_audio_failure {"count":1,"reason":"render_failed"}')
                late_result = _late_audio_result(
                    edition, episode, initial_audio, profile_id=profile_id,
                    state_dir=state_dir, provider_name=provider_name, model_name=model_name)
                if late_result is not None:
                    return late_result
                return {'audio_status': 'pending', 'audio_url': None,
                        'duration_seconds': None, 'file_size_bytes': None,
                        'ready_at': None, 'resumed': False, 'chapter_offsets': [],
                        'reason': 'synthesis_failed'}
        finally:
            reset_usage_caller(token)
        late_result = _late_audio_result(
            edition, episode, initial_audio, profile_id=profile_id,
            state_dir=state_dir, provider_name=provider_name, model_name=model_name)
        if late_result is not None:
            return late_result
        if result.get('status') == 'pending_human_listening':
            return {'audio_status': 'pending', 'audio_url': None,
                    'duration_seconds': None, 'file_size_bytes': None,
                    'ready_at': None, 'resumed': False, 'chapter_offsets': [],
                    'reason': 'pending_human_listening'}
        manifest = result['manifest']
        rendered_episode = result.get('episode')
        rendered_audio = rendered_episode.get('audio') if isinstance(rendered_episode, dict) else None
        selector = persisted_audio_selector(rendered_episode or {}, rendered_audio)
        asset_state, current_manifest, current_path = current_audio_asset(
            episode, profile_id, voice_name=voice, state_dir=state_dir,
            provider_name=provider_name, model_name=model_name, **selector)
        if (asset_state != 'ready' or current_manifest is None or current_path is None or
                not isinstance(manifest, dict) or
                manifest.get('episode_id') != episode['id'] or
                manifest.get('revision_id') != episode['revision_id'] or
                manifest.get('script_hash') != current_manifest.get('script_hash') or
                manifest.get('asset_key') != current_manifest.get('asset_key') or
                (selector and not persisted_audio_matches_manifest(rendered_audio, current_manifest)) or
                result.get('directory') != current_path.parent or
                not isinstance(rendered_episode, dict) or
                rendered_episode.get('id') != episode['id'] or
                rendered_episode.get('revision_id') != episode['revision_id'] or
                not isinstance(rendered_audio, dict) or
                rendered_audio.get('revision_id') != episode['revision_id'] or
                rendered_audio.get('script_hash') != current_manifest.get('script_hash')):
            raise ValueError('daily audio does not match the current edition')
        audio_size = None
        directory, stitched_name = result.get('directory'), manifest.get('stitched_file')
        if isinstance(directory, Path) and isinstance(stitched_name, str):
            audio_path = directory / stitched_name
            if audio_path.is_file() and not audio_path.is_symlink():
                audio_size = audio_path.stat().st_size
        return {'audio_status': 'ready', 'audio_url': '/api/daily/audio',
                'duration_seconds': manifest['duration_seconds'],
                'file_size_bytes': audio_size,
                'ready_at': datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
                'resumed': result.get('cached') is True,
                'chapter_offsets': [{'chapter_id': row['chapter_id'],
                                     'start_seconds': row['start_seconds'],
                                     'duration_seconds': row['duration_seconds']}
                                    for row in manifest['chapters']],
                'voice_name': manifest['voice_name'], 'episode': result['episode'],
                **({key: rendered_audio.get(key) for key in ('provider', 'model')}
                   if selector else {}),
                **{key: result['episode']['audio'][key] for key in (
                    'asset_key', 'episode_id', 'batch_id', 'tts_output_token_limit', 'max_segment_attempts',
                    'coverage_status', 'human_listening_approval')
                   if key in result.get('episode', {}).get('audio', {})}}
    except Exception as error:
        late_result = _late_audio_result(
            edition, episode, initial_audio, profile_id=profile_id,
            state_dir=state_dir, provider_name=provider_name, model_name=model_name)
        if late_result is not None:
            return late_result
        # Audio is optional for this run; provider details never enter the daily report.
        logging.getLogger(__name__).warning(
            'daily_audio_failure {"count":1,"reason":"render_failed"}')
        return {'audio_status': 'failed', 'audio_url': None, 'duration_seconds': None,
                'file_size_bytes': None, 'ready_at': None, 'resumed': False,
                'chapter_offsets': [], 'reason': type(error).__name__}
