"""Provider-neutral text-to-speech primitives for private auditions."""

from .base import CostCounter, TtsProvider, TtsResult

__all__ = ['CostCounter', 'GeminiTtsProvider', 'TtsProvider', 'TtsProviderError', 'TtsResult']


def __getattr__(name):
    if name in {'GeminiTtsProvider', 'TtsProviderError'}:
        from .gemini import GeminiTtsProvider, TtsProviderError
        return {'GeminiTtsProvider': GeminiTtsProvider,
                'TtsProviderError': TtsProviderError}[name]
    raise AttributeError(f'module {__name__!r} has no attribute {name!r}')


def __dir__():
    return sorted(set(globals()) | set(__all__))
