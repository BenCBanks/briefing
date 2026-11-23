"""Small provider interface and count-based cost estimator."""

from dataclasses import dataclass
from decimal import Decimal, ROUND_CEILING
from typing import Protocol


@dataclass(frozen=True)
class TtsResult:
    audio_bytes: bytes
    mime_type: str
    duration_seconds: float
    character_count: int
    estimated_cost_usd: Decimal
    model_name: str
    voice_name: str
    prompt_tokens: int | None = None
    candidates_tokens: int | None = None
    finish_reason: str | None = None
    coverage_status: str | None = None


class TtsProvider(Protocol):
    def synthesize(self, text: str, voice_name: str, *,
                   max_output_tokens: int | None = None, batch_id: str | None = None,
                   batch_output_token_limit: int = 4096) -> TtsResult:
        """Return audio bytes and measured or estimated TTS usage metadata."""


class CostCounter:
    """Estimate Gemini Flash-Lite TTS spend from characters and rendered time.

    Input tokenization is estimated at four characters per token. Output audio
    uses Google's documented 25 audio tokens per second. Rates are the paid
    Gemini 3.8 Flash-Lite TTS rates through 2026-12-31.
    """

    INPUT_USD_PER_MILLION_TOKENS = Decimal('0.50')
    AUDIO_USD_PER_MILLION_TOKENS = Decimal('6.00')
    AUDIO_TOKENS_PER_SECOND = Decimal('25')
    INPUT_CHARACTERS_PER_TOKEN = Decimal('4')
    MILLION = Decimal('1000000')

    def __init__(self):
        self.character_count = 0
        self.input_token_estimate = 0
        self.audio_seconds = Decimal('0')
        self._estimated_cost_usd = Decimal('0')

    @classmethod
    def estimate(cls, character_count: int, duration_seconds: float) -> Decimal:
        characters = max(0, int(character_count))
        seconds = max(0, Decimal(str(duration_seconds)))
        input_tokens = (Decimal(characters) / cls.INPUT_CHARACTERS_PER_TOKEN).to_integral_value(
            rounding=ROUND_CEILING)
        audio_tokens = seconds * cls.AUDIO_TOKENS_PER_SECOND
        input_cost = input_tokens * cls.INPUT_USD_PER_MILLION_TOKENS / cls.MILLION
        audio_cost = audio_tokens * cls.AUDIO_USD_PER_MILLION_TOKENS / cls.MILLION
        return input_cost + audio_cost

    def record(self, character_count: int, duration_seconds: float) -> Decimal:
        characters = max(0, int(character_count))
        seconds = max(0, Decimal(str(duration_seconds)))
        input_tokens = (Decimal(characters) / self.INPUT_CHARACTERS_PER_TOKEN).to_integral_value(
            rounding=ROUND_CEILING)
        cost = self.estimate(characters, float(seconds))
        self.character_count += characters
        self.input_token_estimate += int(input_tokens)
        self.audio_seconds += seconds
        self._estimated_cost_usd += cost
        return cost

    @property
    def estimated_cost_usd(self) -> Decimal:
        return self._estimated_cost_usd
