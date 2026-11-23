#!/usr/bin/env python3
"""Build a deterministic spoken script from verified edition story fields."""

from __future__ import annotations

import re


_ONES = (
    'zero', 'one', 'two', 'three', 'four', 'five', 'six', 'seven', 'eight', 'nine',
    'ten', 'eleven', 'twelve', 'thirteen', 'fourteen', 'fifteen', 'sixteen',
    'seventeen', 'eighteen', 'nineteen',
)
_TENS = ('', '', 'twenty', 'thirty', 'forty', 'fifty', 'sixty', 'seventy', 'eighty', 'ninety')
_SCALES = ('', 'thousand', 'million', 'billion', 'trillion')
_CITATION = re.compile(r'\s*\[(?:\d+(?:\s*,\s*\d+)*|[A-Z])\](?:\s*\[(?:\d+(?:\s*,\s*\d+)*|[A-Z])\])*')
_EDITORIAL_NOTE = re.compile(
    r'^\s*(?:limits?|citations?|coverage(?:\s+(?:note|status|today|reviewed|limited(?:\s+today)?))?)\s*:',
    re.I,
)
_INLINE_NOTE = re.compile(
    r'\s+(?:limits?|citations?|coverage(?:\s+(?:note|status|today|reviewed|limited(?:\s+today)?))?)\s*:.*$',
    re.I,
)
_ISO_DATE = re.compile(r'\b(?P<year>19\d{2}|20\d{2})-(?P<month>0?[1-9]|1[0-2])-(?P<day>0?[1-9]|[12]\d|3[01])\b')
_TEXT_DATE = re.compile(
    r'\b(?P<month>January|February|March|April|May|June|July|August|September|October|November|December)'
    r'\s+(?P<day>\d{1,2})(?:st|nd|rd|th)?\s*,?\s+(?P<year>19\d{2}|20\d{2})\b', re.I)
_CLOCK_TIME = re.compile(
    r'\b(?P<hour>0?[1-9]|1[0-2]):(?P<minute>[0-5]\d)\s*'
    r'(?P<period>a\.?m\.?|p\.?m\.?)(?=\s|$|[,;.!?])', re.I)
_NUMBER_RANGE = re.compile(
    r'(?<![\w.$£€+-])(?P<left>\d[\d,]*(?:\.\d+)?)\s*[-–—]\s*'
    r'(?P<right>\d[\d,]*(?:\.\d+)?)(?P<unit>\s+'
    r'(?:(?:business|calendar|working)\s+)?(?:percent(?:age points?)?|basis points?|bps|'
    r'minutes?|hours?|days?|weeks?|months?|years?|times?|dollars?|euros?|pounds?)(?![A-Za-z])|%)', re.I)
_NUMBER = re.compile(
    r'(?<![\w])(?P<sign>[+-])?(?P<money>[$£€])?\s*(?P<value>\d[\d,]*(?:\.\d+)?)'
    r'(?P<scale>\s*(?:thousand|million|billion|trillion))?'
    r'(?:\s*(?P<unit>%|percent(?:age points?)?|basis points?|bps|dollars?|euros?|pounds?))?', re.I)
_SCIENTIFIC = re.compile(r'(?<![\w])(?P<mantissa>[+-]?\d[\d,]*(?:\.\d+)?)\s*[eE]\s*(?P<exponent>[+-]?\d+)(?!\w)')


def _under_thousand(value: int) -> str:
    words = []
    if value >= 100:
        words.extend((_ONES[value // 100], 'hundred'))
        value %= 100
    if value >= 20:
        tens, ones = divmod(value, 10)
        words.append(_TENS[tens] + (f'-{_ONES[ones]}' if ones else ''))
    elif value:
        words.append(_ONES[value])
    return ' '.join(words)


def _integer_words(value: int) -> str:
    if value < 0 or value >= 10**15:
        raise ValueError('number is outside the supported speech range')
    if value < 1000:
        return _under_thousand(value)
    groups = []
    scale = 0
    while value:
        group = value % 1000
        if group:
            suffix = _SCALES[scale]
            groups.append(f'{_under_thousand(group)} {suffix}'.strip())
        value //= 1000
        scale += 1
    return ' '.join(reversed(groups))


def _ordinal_words(value: int) -> str:
    irregular = {1: 'first', 2: 'second', 3: 'third', 5: 'fifth', 8: 'eighth',
                 9: 'ninth', 11: 'eleventh', 12: 'twelfth', 13: 'thirteenth',
                 15: 'fifteenth', 18: 'eighteenth', 19: 'nineteenth'}
    if value in irregular:
        return irregular[value]
    cardinal = _integer_words(value)
    if '-' in cardinal:
        head, last = cardinal.rsplit('-', 1)
        if last in {'one', 'two', 'three', 'five', 'eight', 'nine', 'twelve'}:
            return head + '-' + _ordinal_words({'one': 1, 'two': 2, 'three': 3, 'five': 5,
                                                 'eight': 8, 'nine': 9, 'twelve': 12}[last])
    if cardinal.endswith('y'):
        return cardinal[:-1] + 'ieth'
    return cardinal + 'th'


def _say_number(raw: str) -> str:
    normalized = raw.replace(',', '')
    if '.' in normalized:
        whole, fraction = normalized.split('.', 1)
        return f"{_integer_words(int(whole))} point {' '.join(_ONES[int(digit)] for digit in fraction)}"
    return _integer_words(int(normalized))


def _spoken_number(match: re.Match) -> str:
    raw = match.group('value')
    scale = (match.group('scale') or '').strip().lower()
    unit = (match.group('unit') or '').lower()
    money = match.group('money')
    sign = match.group('sign') or ''
    value_words = _say_number(raw)
    if money and '.' in raw and not scale:
        whole, cents = raw.replace(',', '').split('.', 1)
        if len(cents) > 2:
            currency = {'$': 'dollars', '£': 'pounds', '€': 'euros'}[money]
            return f'{sign == "-" and "minus " or sign == "+" and "plus " or ""}{value_words} {currency}'
        cents_value = int((cents + '00')[:2])
        currency = {'$': ('dollar', 'dollars'), '£': ('pound', 'pounds'),
                    '€': ('euro', 'euros')}[money]
        return (f'{sign == "-" and "minus " or sign == "+" and "plus " or ""}'
                f'{_integer_words(int(whole.replace(",", "")))} '
                f'{currency[0] if int(whole.replace(",", "")) == 1 else currency[1]} and '
                f'{_integer_words(cents_value)} '
                f'{"cent" if cents_value == 1 else "cents"}')
    if money:
        currency = {'$': 'dollars', '£': 'pounds', '€': 'euros'}[money]
        return f'{sign == "-" and "minus " or sign == "+" and "plus " or ""}{value_words} {scale} {currency}'.replace('  ', ' ').strip()
    if unit in {'%', 'percent'}:
        return f'{sign == "-" and "minus " or sign == "+" and "plus " or ""}{value_words} percent'
    if unit in {'percentage point', 'percentage points'}:
        return f'{sign == "-" and "minus " or sign == "+" and "plus " or ""}{value_words} percentage points'
    if unit in {'bps', 'basis point', 'basis points'}:
        return f'{sign == "-" and "minus " or sign == "+" and "plus " or ""}{value_words} basis points'
    if unit in {'dollar', 'dollars', 'euro', 'euros', 'pound', 'pounds'}:
        return f'{sign == "-" and "minus " or sign == "+" and "plus " or ""}{value_words} {scale} {unit}'.replace('  ', ' ').strip()
    return f'{sign == "-" and "minus " or sign == "+" and "plus " or ""}{value_words} {scale}'.strip()


def _spoken_scientific(match: re.Match) -> str:
    mantissa = _say_number(match.group('mantissa'))
    exponent = int(match.group('exponent'))
    sign = 'negative ' if exponent < 0 else ''
    return f'{mantissa} times ten to the {sign}{_integer_words(abs(exponent))}'


def _without_editorial_notes(text: str) -> str:
    """Drop labeled coverage/editorial lines before turning verified text into speech."""
    kept = []
    for line in (text or '').splitlines():
        if _EDITORIAL_NOTE.match(line):
            continue
        line = _INLINE_NOTE.sub('', line).strip()
        if line:
            kept.append(line)
    return '\n'.join(kept).strip()


def _speak_text(text: str) -> str:
    text = _without_editorial_notes(text)
    text = _CITATION.sub('', text or '')
    text = re.sub(r'https?://\S+|www\.\S+', '', text, flags=re.I)
    month_names = ('January', 'February', 'March', 'April', 'May', 'June',
                   'July', 'August', 'September', 'October', 'November', 'December')

    def speak_date(match: re.Match) -> str:
        month = int(match.group('month'))
        day = int(match.group('day'))
        year = int(match.group('year'))
        return f'{month_names[month - 1]} {_ordinal_words(day)}, {_integer_words(year)}'

    text = _ISO_DATE.sub(speak_date, text)

    def speak_text_date(match: re.Match) -> str:
        month = match.group('month').title()
        day = int(match.group('day'))
        year = int(match.group('year'))
        return f'{month} {_ordinal_words(day)}, {_integer_words(year)}'

    text = _TEXT_DATE.sub(speak_text_date, text)
    def speak_clock(match: re.Match) -> str:
        hour = _integer_words(int(match.group('hour')))
        minute_value = int(match.group('minute'))
        minute = ('' if minute_value == 0 else
                  ('oh ' if minute_value < 10 else '') + _integer_words(minute_value))
        period = 'a.m.' if match.group('period').casefold().startswith('a') else 'p.m.'
        return ' '.join(part for part in (hour, minute, period) if part)

    text = _CLOCK_TIME.sub(speak_clock, text)
    text = _NUMBER_RANGE.sub(
        lambda match: f"{_say_number(match.group('left'))} to {_say_number(match.group('right'))}"
                      f"{' percent' if match.group('unit') == '%' else match.group('unit')}", text)
    text = _SCIENTIFIC.sub(_spoken_scientific, text)
    # If a conventional initialism and its spelled-out name are both present,
    # make that relationship audible while staying within the supplied words.
    # Expand an acronym only when its full name is already written in this
    # verified story. Otherwise say its letters without guessing its meaning.
    def speak_acronym(match: re.Match) -> str:
        acronym = match.group(1)
        prefix = text[:match.start()].rstrip()
        prefix = re.split(r'[.!?]\s+', prefix)[-1]
        has_parenthetical_expansion = prefix.endswith('(')
        phrase_match = re.search(
            r'((?:(?:[A-Z][\w’&-]*|for|and|of|the)\s+){1,8}[A-Z][\w’&-]*)\s*\($',
            prefix)
        # Only a source's explicit parenthetical pairing licenses an expansion.
        expanded = has_parenthetical_expansion and phrase_match is not None
        replacement = 'or ' + '-'.join(acronym) if expanded else '-'.join(acronym)
        return replacement

    text = re.sub(r'\b([A-Z]{2,8})\b', speak_acronym, text)
    text = re.sub(r'\s*\(\s*or ([A-Z](?:-[A-Z])+?)\s*\)', r', or \1', text)

    text = _NUMBER.sub(_spoken_number, text)
    # Provider prose occasionally contains a numeric token adjacent to an
    # unusual separator that the structured patterns above do not recognize.
    # Make one final conservative pass before failing the whole edition.
    text = re.sub(r'(?<![A-Za-z])\d[\d,]*(?:\.\d+)?',
                  lambda match: _say_number(match.group(0)), text)
    text = text.replace('%', ' percent').replace('$', ' dollars ').replace('£', ' pounds ').replace('€', ' euros ')
    if re.search(r'\d|[$£€%]', text):
        raise ValueError('story contains a number format that is not supported for speech')
    text = re.sub(r'\s+', ' ', text)
    return text.strip()


def build_audio_scripts(edition: dict, sources: list[dict] | None = None) -> list[dict]:
    """Return one spoken script for each story with at least one verified claim.

    Paragraphs preserve verified claim wording apart from citation removal,
    acronym pronunciation, and numeric verbalization. Source attribution is
    assembled only from linked source records; URLs and uncited body copy are
    never included.
    """
    source_by_id = {row.get('id'): row for row in (sources or []) if isinstance(row, dict)}
    scripts = []
    for story in edition.get('sections', []):
        if not isinstance(story, dict) or not story.get('cluster_id'):
            continue
        links = [row for row in story.get('claim_links', [])
                 if isinstance(row, dict) and row.get('verified') is True
                 and str(row.get('section') or '').casefold() not in {'limits', 'coverage', 'citations', 'source', 'sources'}
                 and isinstance(row.get('text'), str) and row['text'].strip()]
        if not links:
            continue
        paragraphs = []
        for link in links:
            source_text = _without_editorial_notes(link['text'])
            spoken = _speak_text(source_text)
            if not spoken:
                continue
            paragraphs.append({'section': link.get('section', 'what_happened'),
                               'source_text': source_text, 'text': spoken})
        read_text = story.get('read_takeaway')
        if isinstance(read_text, str) and read_text.strip():
            read_source_text = _CITATION.sub('', _without_editorial_notes(read_text)).strip()
            read_spoken = _speak_text(read_source_text)
            if read_spoken:
                paragraphs.append({'section': 'read', 'source_text': read_source_text, 'text': read_spoken})
        if not paragraphs:
            continue
        source_numbers = sorted({number for link in links
                                 for number in link.get('citation_numbers', [])
                                 if isinstance(number, int) and number > 0})
        read_link = story.get('read_takeaway_link') or {}
        source_numbers = sorted(set(source_numbers) | {
            number for number in read_link.get('citation_numbers', [])
            if isinstance(number, int) and number > 0})
        source_ids = story.get('source_ids', [])
        source_names = []
        # Generated editions order source_ids by their citation number.
        for number in source_numbers:
            index = number - 1
            if index < len(source_ids):
                source = source_by_id.get(source_ids[index])
                name = (source or {}).get('publisher') or (source or {}).get('kind')
                if isinstance(name, str) and name.strip():
                    spoken_name = _speak_text(name)
                    if spoken_name and spoken_name not in source_names:
                        source_names.append(spoken_name)
        text_parts = ['First up.' if not scripts else 'Next.']
        prior_section = None
        for paragraph in paragraphs:
            section = paragraph['section']
            if section != prior_section and section in {'why_it_matters', 'things_to_consider'}:
                text_parts.append({'why_it_matters': 'Why it matters.',
                                   'things_to_consider': 'Things to consider.'}[section])
            elif section == 'read' and prior_section != 'read':
                text_parts.append('Read.')
            text_parts.append(paragraph['text'])
            prior_section = section
        if source_names:
            if len(source_names) == 1:
                joined_sources = source_names[0]
            else:
                joined_sources = ', '.join(source_names[:-1]) + ' and ' + source_names[-1]
            attribution = 'Source: ' + joined_sources + '.'
            text_parts.append(attribution)
        else:
            attribution = ''
        scripts.append({'story_id': story.get('id'), 'heading': story.get('heading', ''),
                        'orientation': 'First up.', 'paragraphs': paragraphs,
                        'source_attribution': attribution,
                        'source_names': source_names,
                        'text': '\n\n'.join(text_parts)})
    return scripts
