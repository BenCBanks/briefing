"""Offline editorial checks for the spoken shape of an on-demand episode."""
from __future__ import annotations

import math
import re

from .policy import WORDS_PER_MINUTE
from .script_checks import word_count


_WORD = re.compile(r"[\w’'-]+", re.UNICODE)
_OPENING_TEMPLATE = re.compile(
    r"^(?:here(?:'s| is) (?:your|the) .{0,50}briefing|welcome to|today we(?:'re| are) going to)\b",
    re.I,
)
_DISCLAIMER_ENDING = re.compile(
    r"^(?:disclaimer\b|this (?:episode|information|content) is not\b|"
    r"this is not (?:financial|legal|medical|investment|professional) advice\b|"
    r"this isn't (?:financial|legal|medical|investment|professional) advice\b|"
    r"i(?:'m| am) not (?:a |your )?(?:doctor|lawyer|financial adviser|financial advisor)\b|"
    r"consult (?:a |your )?(?:doctor|lawyer|financial adviser|financial advisor)\b)", re.I,
)
_HEADLINE_STOP = frozenset({
    'a', 'an', 'and', 'are', 'as', 'at', 'by', 'for', 'from', 'how', 'in',
    'into', 'is', 'of', 'on', 'or', 'the', 'to', 'what', 'when', 'where',
    'which', 'who', 'why', 'with', 'work', 'works',
})


def _sentence_rows(episode: dict) -> list[list[dict]]:
    chapters = episode.get('chapters') if isinstance(episode, dict) else None
    result = []
    for chapter in chapters if isinstance(chapters, list) else []:
        if not isinstance(chapter, dict):
            continue
        rows = chapter.get('sentences')
        if not isinstance(rows, list):
            continue
        result.append([row for row in rows if isinstance(row, dict) and
                       isinstance(row.get('text'), str) and row['text'].strip()])
    return result


def _headline_repeated(title: str, sentence: str) -> bool:
    title_words = [token.casefold() for token in _WORD.findall(title)
                   if token.casefold() not in _HEADLINE_STOP]
    sentence_words = [token.casefold() for token in _WORD.findall(sentence)
                      if token.casefold() not in _HEADLINE_STOP]
    if not title_words or not sentence_words:
        return False
    title_norm = ' '.join(title_words)
    sentence_norm = ' '.join(sentence_words)
    if title_norm == sentence_norm or sentence_norm.startswith(title_norm + ' '):
        return True
    if len(title_words) < 4:
        return False
    overlap = len(set(title_words) & set(sentence_words)) / len(set(title_words))
    return overlap >= .9


def episode_voice_quality(episode: dict, *, minimum_fraction: float = .60) -> dict:
    """Report directness, spoken shape and a conservative duration floor.

    These are editorial diagnostics only. They do not alter generation or
    evidence acceptance.
    """
    groups = _sentence_rows(episode)
    sentences = [row['text'].strip() for group in groups for row in group]
    first_row = next((row for group in groups for row in group), None)
    first = first_row.get('text', '').strip() if first_row else ''
    final = sentences[-1] if sentences else ''
    requested = episode.get('requested_minutes') if isinstance(episode, dict) else None
    target = episode.get('target_words') if isinstance(episode, dict) else None
    if type(target) is not int or target < 1:
        target = requested * WORDS_PER_MINUTE if type(requested) is int and requested > 0 else None
    transcript = episode.get('transcript', '') if isinstance(episode, dict) else ''
    spoken_words = word_count(transcript) if isinstance(transcript, str) and transcript.strip() else sum(
        word_count(sentence) for sentence in sentences)
    minimum_words = math.ceil(target * minimum_fraction) if target else None

    opening_clean = bool(first) and '?' not in first and not _OPENING_TEMPLATE.search(first)
    opening_direct = opening_clean and 6 <= word_count(first) <= 25
    no_question = bool(first) and '?' not in first
    no_disclaimer = bool(final) and not _DISCLAIMER_ENDING.search(final.strip())
    repeated = False
    if groups:
        chapters = episode.get('chapters') if isinstance(episode, dict) else []
        for index, group in enumerate(groups):
            title = chapters[index].get('title', '') if index < len(chapters) and isinstance(chapters[index], dict) else ''
            first_claim = next((row.get('text', '') for row in group
                                if row.get('kind') not in {'opening', 'navigation'}), '')
            if title and first_claim and _headline_repeated(title, first_claim):
                repeated = True
                break

    checks = [
        {'name': 'direct_opening', 'passed': opening_direct},
        {'name': 'no_rhetorical_question_opener', 'passed': no_question},
        {'name': 'no_disclaimer_ending', 'passed': no_disclaimer},
        {'name': 'no_repeated_headline', 'passed': not repeated},
        {'name': 'minimum_words', 'passed': minimum_words is not None and spoken_words >= minimum_words},
    ]
    return {'passed': all(row['passed'] for row in checks), 'checks': checks,
            'spoken_words': spoken_words, 'target_words': target,
            'minimum_words': minimum_words,
            'failed': [row['name'] for row in checks if not row['passed']]}
