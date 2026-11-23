"""In-process capabilities for exact bounded-audio listening approval.

The fixture issuer creates explicitly fictional attestations for isolated tests;
it is not wired to an application route or production approval flow.
"""

from dataclasses import dataclass, field
import re
from types import MappingProxyType
from typing import Mapping

from dev.test_guard import isolated_state_error


_CAPABILITY_SEAL = object()
_PROFILE_ID = re.compile(r'^[a-f0-9]{24}$')
_IDENTITY_FIELDS = frozenset({
    'profile_id', 'episode_id', 'revision_id', 'script_sha256', 'candidate_batch_id',
    'asset_key', 'voice_id', 'provider_id', 'model_id', 'normalizer_id',
    'tts_output_token_limit', 'max_segment_attempts',
})


@dataclass(frozen=True, slots=True)
class TrustedListeningApproval:
    """Opaque, exact-identity approval token for an in-process audio promoter."""

    identity: Mapping[str, object]
    _seal: object = field(repr=False, compare=False)


def issue_fictional_listening_approval_for_test(
        expected_identity: Mapping[str, object]) -> TrustedListeningApproval:
    """Mint a sealed fictional capability for temporary-state tests only.

    This helper records no real listening and must not be used by application
    callers. Its name and return type make the fixture-only status explicit.
    """
    if isolated_state_error() is not None:
        raise RuntimeError('fictional listening approval issuer requires an isolated test environment.')
    if not isinstance(expected_identity, Mapping) or set(expected_identity) != _IDENTITY_FIELDS:
        raise ValueError('fictional listening approval identity is incomplete.')
    identity = dict(expected_identity)
    if not isinstance(identity['profile_id'], str) or not _PROFILE_ID.fullmatch(identity['profile_id']):
        raise ValueError('fictional listening approval profile identity is invalid.')
    for name in ('episode_id', 'revision_id', 'script_sha256', 'candidate_batch_id',
                 'asset_key', 'voice_id', 'provider_id', 'model_id', 'normalizer_id'):
        if not isinstance(identity[name], str) or not identity[name]:
            raise ValueError('fictional listening approval identity is invalid.')
    for name in ('tts_output_token_limit', 'max_segment_attempts'):
        if type(identity[name]) is not int or identity[name] < 1:
            raise ValueError('fictional listening approval limits are invalid.')
    return TrustedListeningApproval(MappingProxyType(identity), _CAPABILITY_SEAL)


def validate_trusted_listening_approval(
        value: object, expected_identity: Mapping[str, object]) -> bool:
    """Accept only this module's sealed capability with the exact identity."""
    if (type(value) is not TrustedListeningApproval or
            value._seal is not _CAPABILITY_SEAL or
            not isinstance(expected_identity, Mapping) or
            dict(value.identity) != dict(expected_identity)):
        raise ValueError('trusted listening approval is invalid or does not match.')
    return True
