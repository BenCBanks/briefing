"""Prototype defaults. Switching the primary requires only provider_order/key setup."""
from dataclasses import dataclass, field
from pathlib import Path
from ..paths import STATE_DIR, checked_state_path


@dataclass(frozen=True)
class RetrievalConfig:
    provider_order: tuple[str, ...] = ('gemini', 'gdelt', 'feeds')
    gemini_model: str = 'gemini-3-flash-preview'
    gemini_key_file: Path = field(default_factory=lambda: Path.home() / '.briefing_gemini_search_key')
    perplexity_key_file: Path = field(default_factory=lambda: Path.home() / '.briefing_search_key')
    state_dir: Path = STATE_DIR
    gemini_daily_cap: int = 24
    perplexity_daily_cap: int = 24
    perplexity_search_type: str = 'web'
    result_cap: int = 10
    # Editorial ranking hints only; they convey no reuse rights.
    domain_tiers: dict = field(default_factory=lambda: {
        'reuters.com': 3, 'apnews.com': 3, 'nature.com': 3,
        'science.org': 3, 'bbc.com': 2, 'bbc.co.uk': 2,
    })

    def __post_init__(self):
        object.__setattr__(self, 'state_dir', checked_state_path(self.state_dir))
