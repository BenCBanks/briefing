"""Gemini API text-to-speech provider using a local key file."""

from __future__ import annotations

import base64
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from io import BytesIO
import json
from pathlib import Path
import re
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request
import wave

from .. import core
from ..usage import UsageLedger, process_ledger
from .base import CostCounter, TtsResult


DEFAULT_MODEL = 'gemini-3.8-flash-lite-tts'
API_ROOT = 'https://generativelanguage.googleapis.com/v1beta'
MODEL_INPUT_TOKEN_LIMIT = 8192
MODEL_OUTPUT_TOKEN_LIMIT = 16384
DEFAULT_BATCH_OUTPUT_TOKEN_LIMIT = 4096
MAX_TTS_BATCH_COST_USD = Decimal('0.06144')
AUDIO_TOKENS_PER_SECOND = 25
_VOICE_NAME = re.compile(r'^[A-Za-z][A-Za-z0-9_-]{0,63}$')
_SAMPLE_RATE = re.compile(r'(?:rate|sample_rate)=(\d+)', re.I)
MAX_PROVIDER_RESPONSE_BYTES = 8_000_000


class TtsProviderError(RuntimeError):
    """Sanitized provider failure; never includes credentials or request headers."""


class TtsOutputLimitError(TtsProviderError):
    """The provider ended a bounded response at its output-token limit."""

    def __init__(self, message, *, output_tokens=None):
        super().__init__(message)
        self.output_tokens = output_tokens


@dataclass(frozen=True)
class ModelInfo:
    name: str
    display_name: str
    methods: tuple[str, ...]


def _read_key(path: Path) -> str:
    try:
        key = path.expanduser().read_text(encoding='utf-8').strip()
    except OSError as error:
        raise TtsProviderError('Gemini API key file is unavailable.') from None
    if not key:
        raise TtsProviderError('Gemini API key file is empty.')
    return key


def _safe_transport(request: Request, timeout: float):
    parts = urlsplit(request.full_url)
    if (parts.scheme != 'https' or parts.hostname != 'generativelanguage.googleapis.com' or
            parts.username or parts.password or (parts.port is not None and parts.port != 443)):
        raise TtsProviderError('Gemini API request URL was rejected.')
    return core._safe_https_open(request, {'generativelanguage.googleapis.com'}, timeout,
                                 max_redirects=0)


def _response_json(request: Request, timeout: float, transport: Callable | None = None) -> dict:
    try:
        with (transport or _safe_transport)(request, timeout=timeout) as response:
            raw = response.read(MAX_PROVIDER_RESPONSE_BYTES + 1)
            if len(raw) > MAX_PROVIDER_RESPONSE_BYTES:
                raise TtsProviderError('Gemini API response was too large.')
    except HTTPError as error:
        raise TtsProviderError(f'Gemini API returned HTTP {error.code}.') from None
    except (URLError, TimeoutError, OSError) as error:
        raise TtsProviderError(f'Gemini API request failed ({type(error).__name__}).') from None
    except ValueError:
        raise TtsProviderError('Gemini API response was rejected.') from None
    try:
        value = json.loads(raw.decode('utf-8'))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise TtsProviderError('Gemini API returned invalid JSON.') from None
    if not isinstance(value, dict):
        raise TtsProviderError('Gemini API returned an invalid response.')
    return value


def list_tts_models(key_path: Path | str = Path('~/.briefing_gemini_key'), *,
                    timeout: float = 15, transport: Callable | None = None) -> list[ModelInfo]:
    key = _read_key(Path(key_path))
    request = Request(f'{API_ROOT}/models?pageSize=1000', headers={
        'Accept': 'application/json', 'User-Agent': 'BriefingTtsAudition/1.0', 'x-goog-api-key': key,
    })
    payload = _response_json(request, timeout, transport)
    result = []
    for row in payload.get('models', []):
        if not isinstance(row, dict):
            continue
        full_name = str(row.get('name') or '')
        display = str(row.get('displayName') or '')
        if 'tts' not in (full_name + ' ' + display).lower():
            continue
        methods = tuple(str(method) for method in row.get('supportedGenerationMethods', [])
                        if isinstance(method, str))
        if 'generateContent' in methods:
            result.append(ModelInfo(full_name.removeprefix('models/'), display, methods))
    return result


def _wav_from_pcm(audio: bytes, sample_rate: int, channels: int = 1) -> bytes:
    if sample_rate <= 0 or channels <= 0 or len(audio) % (2 * channels):
        raise TtsProviderError('Gemini API returned malformed PCM audio.')
    output = BytesIO()
    with wave.open(output, 'wb') as wav:
        wav.setnchannels(channels)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(audio)
    return output.getvalue()


def _wav_duration(audio: bytes) -> float:
    try:
        with wave.open(BytesIO(audio), 'rb') as wav:
            if wav.getcomptype() != 'NONE':
                raise wave.Error('compressed WAV not supported')
            rate = wav.getframerate()
            frames = wav.getnframes()
            channels = wav.getnchannels()
            width = wav.getsampwidth()
            if rate <= 0 or frames <= 0 or channels <= 0 or width not in {1, 2, 3, 4}:
                raise wave.Error('invalid WAV parameters')
            pcm = wav.readframes(frames)
            if len(pcm) != frames * channels * width:
                raise wave.Error('truncated WAV frames')
            return frames / rate
    except (wave.Error, EOFError, OSError):
        raise TtsProviderError('Gemini API returned malformed WAV audio.') from None


class GeminiTtsProvider:
    def __init__(self, *, model: str = DEFAULT_MODEL,
                 key_path: Path | str = Path('~/.briefing_gemini_key'), timeout: float = 60,
                 transport: Callable | None = None, cost_counter: CostCounter | None = None,
                 usage_ledger: UsageLedger | None = None):
        if not model or '/' in model:
            raise ValueError('model must be a Gemini model name without a models/ prefix')
        self.model = model
        self.key_path = Path(key_path)
        self.timeout = timeout
        self._transport = transport
        self.cost_counter = cost_counter or CostCounter()
        self.usage_ledger = usage_ledger or process_ledger(UsageLedger)

    def bounded_batch_cost(self, batch_id: str) -> dict:
        """Read this render's ledger costs; pending reservations are not actual spend."""
        if not isinstance(batch_id, str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,120}', batch_id):
            raise TtsProviderError('Bounded TTS cost identity is invalid.')

        def read(state):
            pool = state.get('token_pools', {}).get(batch_id)
            if not isinstance(pool, dict):
                raise TtsProviderError('Bounded TTS cost accounting is unavailable.')
            try:
                settled = Decimal(pool['settled_usd'])
                reserved = Decimal(pool['reserved_usd'])
            except (KeyError, InvalidOperation, TypeError, ValueError):
                raise TtsProviderError('Bounded TTS cost accounting is invalid.') from None
            if any(not value.is_finite() or value < 0 for value in (settled, reserved)):
                raise TtsProviderError('Bounded TTS cost accounting is invalid.')
            return {'estimated_cost_usd': f'{settled + reserved:.8f}',
                    'settled_cost_usd': f'{settled:.8f}',
                    'pending_reservation_usd': f'{reserved:.8f}',
                    'cost_basis': 'settled token cost plus pending reservations; not an invoice'}, False

        return self.usage_ledger._transaction(read)

    def synthesize(self, text: str, voice_name: str, *,
                   max_output_tokens: int | None = None, batch_id: str | None = None,
                   batch_output_token_limit: int = DEFAULT_BATCH_OUTPUT_TOKEN_LIMIT,
                   batch_limit_usd: Decimal | str | None = None) -> TtsResult:
        if not isinstance(text, str) or not text.strip():
            raise ValueError('text must be a non-empty string')
        if len(text) > 6000:
            raise ValueError('text exceeds the audition request limit')
        if (max_output_tokens is not None and
                (type(max_output_tokens) is not int or
                 not 1 <= max_output_tokens <= MODEL_OUTPUT_TOKEN_LIMIT)):
            raise ValueError('max_output_tokens is outside the supported TTS limit')
        bounded = max_output_tokens is not None
        if bounded:
            if self.model != DEFAULT_MODEL:
                raise ValueError('bounded TTS supports only the verified Gemini default Lite model.')
            if (not isinstance(batch_id, str) or
                    not re.fullmatch(r'[A-Za-z0-9_.:-]{1,120}', batch_id) or
                    type(batch_output_token_limit) is not int or
                    not 1 <= batch_output_token_limit <= DEFAULT_BATCH_OUTPUT_TOKEN_LIMIT or
                    max_output_tokens > batch_output_token_limit or batch_limit_usd is None):
                raise ValueError('bounded TTS needs a batch identity and complete batch limits')
            try:
                batch_limit_usd = Decimal(str(batch_limit_usd))
            except (InvalidOperation, ValueError, TypeError):
                raise ValueError('batch_limit_usd must be a positive amount') from None
            if (not batch_limit_usd.is_finite() or batch_limit_usd <= 0 or
                    batch_limit_usd > MAX_TTS_BATCH_COST_USD):
                raise ValueError('batch_limit_usd exceeds the bounded TTS batch maximum')
        elif batch_id is not None or batch_limit_usd is not None:
            raise ValueError('TTS batch limits require max_output_tokens')
        if not _VOICE_NAME.fullmatch(voice_name or ''):
            raise ValueError('voice_name contains unsupported characters')
        key = _read_key(self.key_path)
        if max_output_tokens is None:
            reservation = self.usage_ledger.reserve_tts_call(len(text), model=self.model)
        else:
            reserve = getattr(self.usage_ledger, 'reserve_tts_token_call', None)
            settle = getattr(self.usage_ledger, 'settle_tts_token_call', None)
            if not callable(reserve) or not callable(settle):
                raise TtsProviderError('Bounded TTS reservation is unavailable.')
            reservation = reserve(MODEL_INPUT_TOKEN_LIMIT, max_output_tokens, self.model,
                                  batch_id=batch_id,
                                  batch_output_token_limit=batch_output_token_limit,
                                  batch_limit_usd=batch_limit_usd)
        request_body = {
            'contents': [{'parts': [{'text': text}]}],
            'generationConfig': {
                'responseModalities': ['AUDIO'],
                'speechConfig': {
                    'voiceConfig': {'prebuiltVoiceConfig': {'voiceName': voice_name}},
                },
            },
        }
        if max_output_tokens is not None:
            request_body['generationConfig']['maxOutputTokens'] = max_output_tokens
            request_body['generationConfig']['candidateCount'] = 1
        request = Request(f'{API_ROOT}/models/{self.model}:generateContent',
                          data=json.dumps(request_body, separators=(',', ':')).encode('utf-8'),
                          headers={'Accept': 'application/json', 'Content-Type': 'application/json',
                                   'User-Agent': 'BriefingTtsAudition/1.0', 'x-goog-api-key': key},
                          method='POST')
        payload = _response_json(request, self.timeout, self._transport)
        finish_reason = None
        prompt_tokens = None
        candidates_tokens = None
        if max_output_tokens is not None:
            candidates = payload.get('candidates')
            usage = payload.get('usageMetadata')
            if not isinstance(candidates, list) or len(candidates) != 1 or not isinstance(usage, dict):
                raise TtsProviderError('Gemini API returned ambiguous bounded TTS usage.')
            candidate = candidates[0]
            finish_reason = candidate.get('finishReason') if isinstance(candidate, dict) else None
            prompt_tokens = usage.get('promptTokenCount')
            candidates_tokens = usage.get('candidatesTokenCount')
            if (finish_reason not in {'STOP', 'MAX_TOKENS'} or
                    type(prompt_tokens) is not int or not 0 <= prompt_tokens <= MODEL_INPUT_TOKEN_LIMIT or
                    type(candidates_tokens) is not int or not 0 <= candidates_tokens <= max_output_tokens):
                raise TtsProviderError('Gemini API returned ambiguous bounded TTS usage.')
            settle(reservation, prompt_tokens, candidates_tokens, self.model)
            if finish_reason == 'MAX_TOKENS':
                raise TtsOutputLimitError('Gemini TTS reached its output-token limit.',
                                          output_tokens=candidates_tokens)
        try:
            candidates = payload['candidates']
            candidate = next(candidate for candidate in candidates
                             if isinstance(candidate, dict) and
                             any('inlineData' in part
                                 for part in candidate['content']['parts']))
            if candidate.get('finishReason') != 'STOP':
                raise TtsProviderError('Gemini API did not finish TTS generation normally.')
            part = next(part for part in candidate['content']['parts'] if 'inlineData' in part)
            inline = part['inlineData']
            audio = base64.b64decode(inline['data'], validate=True)
            source_type = str(inline.get('mimeType') or '').lower()
        except TtsProviderError:
            raise
        except (KeyError, StopIteration, TypeError, ValueError):
            raise TtsProviderError('Gemini API response did not contain valid inline audio.') from None

        if source_type.startswith('audio/l16') or source_type.startswith('audio/pcm'):
            match = _SAMPLE_RATE.search(source_type)
            sample_rate = int(match.group(1)) if match else 24000
            wav_bytes = _wav_from_pcm(audio, sample_rate)
        elif source_type in {'audio/wav', 'audio/x-wav', 'audio/wave'}:
            wav_bytes = audio
        else:
            raise TtsProviderError('Gemini API returned an unsupported audio format.')
        duration = _wav_duration(wav_bytes)
        characters = len(text)
        cost = self.cost_counter.record(characters, duration)
        if max_output_tokens is None:
            self.usage_ledger.settle_tts_call(reservation, characters, duration)
        return TtsResult(wav_bytes, 'audio/wav', duration, characters, cost, self.model, voice_name,
                         prompt_tokens, candidates_tokens, finish_reason,
                         'pending_human_listening' if max_output_tokens is not None else None)
