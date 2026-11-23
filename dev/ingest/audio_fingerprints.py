"""Pure, provider-independent episode audio identity fingerprints."""

import hashlib
import json

from .tts.normalize import NORMALIZER_VERSION, normalize_narration


def _fingerprints(episode: dict, chapters: list[dict], voice_name: str,
                  provider_name: str, model_name: str, *,
                  tts_output_token_limit: int | None = None,
                  max_segment_attempts: int | None = None,
                  normalizer=normalize_narration,
                  normalizer_version: str | None = None) -> tuple[str, str]:
    script_payload = [{'id': chapter['id'], 'text': chapter['text']} for chapter in chapters]
    script_hash = hashlib.sha256(json.dumps(script_payload, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()
    if 'transcript' in episode:
        spoken = '\n\n'.join(chapter['text'] for chapter in chapters)
        if spoken != episode['transcript']:
            raise ValueError('Frozen transcript and chapter speech differ.')
        script_hash = hashlib.sha256(spoken.encode()).hexdigest()
        if episode.get('script_hash') != script_hash:
            raise ValueError('Frozen transcript hash differs.')
    revision_id = episode.get('revision_id') or episode.get('revision') or ''
    narration_payload = [{'id': chapter['id'], 'text': normalizer(chapter['text'])}
                         for chapter in chapters]
    narration_hash = hashlib.sha256(json.dumps(narration_payload, ensure_ascii=False,
                                               separators=(',', ':')).encode()).hexdigest()
    identity_fields = {'episode_id': episode['id'], 'revision_id': str(revision_id),
                       'script_hash': script_hash, 'voice_name': voice_name,
                       'provider': provider_name, 'model': model_name,
                       'narration_hash': narration_hash,
                       'normalizer_version': (NORMALIZER_VERSION if normalizer_version is None
                                              else normalizer_version)}
    if tts_output_token_limit is not None:
        identity_fields['tts_output_token_limit'] = tts_output_token_limit
        identity_fields['max_segment_attempts'] = max_segment_attempts
    identity = json.dumps(identity_fields,
                          ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()
    asset_key = hashlib.sha256(identity).hexdigest()
    return script_hash, asset_key
