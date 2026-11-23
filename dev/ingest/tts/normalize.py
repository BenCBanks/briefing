"""Deterministic cleanup of written scripts before text-to-speech synthesis."""

from __future__ import annotations

import re
from datetime import date


NORMALIZER_VERSION = 'ear-2'
_ONES = ('zero', 'one', 'two', 'three', 'four', 'five', 'six', 'seven', 'eight', 'nine',
         'ten', 'eleven', 'twelve', 'thirteen', 'fourteen', 'fifteen', 'sixteen',
         'seventeen', 'eighteen', 'nineteen')
_TENS = ('', '', 'twenty', 'thirty', 'forty', 'fifty', 'sixty', 'seventy', 'eighty', 'ninety')
_SCALES = ('', 'thousand', 'million', 'billion', 'trillion')
_MONTHS = ('January', 'February', 'March', 'April', 'May', 'June', 'July', 'August',
           'September', 'October', 'November', 'December')
_MONTH_PATTERN = r'(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)'
_CITATION_PATTERN = (
    r'\[(?:\s*\d+(?:\s*[,;–-]\s*\d+)*\s*|\s*[A-Z]\s*|'
    r'\s*(?:source|citation)\s*\d*\s*)\](?:\s*\[\s*\d+(?:\s*[,;–-]\s*\d+)*\s*\])*')
_CITATION = re.compile(_CITATION_PATTERN, re.I)
_CITATION_LINK = re.compile(_CITATION_PATTERN + r'\s*\((?:https?://|www\.)[^)]+\)', re.I)
_URL = re.compile(r'(?i)\b(?:https?://|www\.)[^\s<>]+')
_LINK_ONLY_REFERENCE_SENTENCE = re.compile(
    r'(^|[.!?]\s+)see\s+(?:https?://|www\.)[^\s<>]+'
    r'(?:\s+\[[^\]]+\])?[.!?]?(?=\s+[A-Z]|$)', re.I)
_ISO_DATE = re.compile(r'\b(?P<year>19\d{2}|20\d{2})-(?P<month>0?[1-9]|1[0-2])-(?P<day>0?[1-9]|[12]\d|3[01])\b')
_MONTH_DATE = re.compile(rf'\b(?P<month>{_MONTH_PATTERN})\.?\s+(?P<day>\d{{1,2}})(?:st|nd|rd|th)?(?:,?\s+(?P<year>19\d{{2}}|20\d{{2}}))?\b', re.I)
_TIME = re.compile(r'\b(?P<hour>\d{1,2}):(?P<minute>[0-5]\d)(?:\s*(?P<period>a\.?m\.?|p\.?m\.?))?(?=\s|$|[,;.!?])', re.I)
_SPOKEN_TIME_COLON = re.compile(r'\b(?P<hour>one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)\s*:\s*(?P<period>a\.?m\.?|p\.?m\.?)\b', re.I)
_GLUED_DATE_YEAR = re.compile(
    rf'\b(?P<month>{_MONTH_PATTERN})\s+(?P<day>one|two|three|four|five|six|seven|eight|nine|ten|'
    r'eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty(?:[- ]one|[- ]two|[- ]three|[- ]four|[- ]five|[- ]six|[- ]seven|[- ]eight|[- ]nine)?|'
    r'thirty(?:[- ]one)?)\s*(?=two thousand\b)', re.I)
_ORDINAL_BY_CARDINAL = {
    'one': 'first', 'two': 'second', 'three': 'third', 'four': 'fourth', 'five': 'fifth',
    'six': 'sixth', 'seven': 'seventh', 'eight': 'eighth', 'nine': 'ninth', 'ten': 'tenth',
    'eleven': 'eleventh', 'twelve': 'twelfth', 'thirteen': 'thirteenth', 'fourteen': 'fourteenth',
    'fifteen': 'fifteenth', 'sixteen': 'sixteenth', 'seventeen': 'seventeenth',
    'eighteen': 'eighteenth', 'nineteen': 'nineteenth', 'twenty': 'twentieth',
    'twenty-one': 'twenty-first', 'twenty one': 'twenty-first', 'twenty-two': 'twenty-second',
    'twenty two': 'twenty-second', 'twenty-three': 'twenty-third', 'twenty three': 'twenty-third',
    'twenty-four': 'twenty-fourth', 'twenty four': 'twenty-fourth', 'twenty-five': 'twenty-fifth',
    'twenty five': 'twenty-fifth', 'twenty-six': 'twenty-sixth', 'twenty six': 'twenty-sixth',
    'twenty-seven': 'twenty-seventh', 'twenty seven': 'twenty-seventh', 'twenty-eight': 'twenty-eighth',
    'twenty eight': 'twenty-eighth', 'twenty-nine': 'twenty-ninth', 'twenty nine': 'twenty-ninth',
    'thirty': 'thirtieth', 'thirty-one': 'thirty-first', 'thirty one': 'thirty-first',
}
_MONTH_DATE_WORDS = re.compile(
    rf'\b(?P<month>{_MONTH_PATTERN})\.?\s+(?P<day>one|two|three|four|five|six|seven|eight|nine|ten|'
    r'eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty(?:[- ]one|[- ]two|[- ]three|[- ]four|[- ]five|[- ]six|[- ]seven|[- ]eight|[- ]nine)?|'
    r'thirty(?:[- ]one)?)\b', re.I)
_GLUED_CURRENCY_PREPOSITION = re.compile(
    r'\b(?P<currency>dollars?|euros?|pounds?)(?P<next>over|per|to|in|for|from|by|on|at|as|with|while|through)\b', re.I)
_NUMBER_TOKEN = r'\d[\d,]*(?:\.\d+)?'
_SCALE_TOKEN = r'(?:thousand|million|billion|trillion)\b'
_RANGE = re.compile(
    rf'(?<![\w.])(?P<lc>US\$|[$£€])?\s*(?P<left>{_NUMBER_TOKEN})(?P<ls>\s*{_SCALE_TOKEN})?'
    rf'\s*(?:–|—|\s+to\s+|-)\s*(?P<rc>US\$|[$£€])?\s*(?P<right>{_NUMBER_TOKEN})'
    rf'(?P<rs>\s*{_SCALE_TOKEN})?(?P<unit>\s*(?:%|percent(?:age points?)?|basis points?|bps|dollars?|euros?|pounds?))?', re.I)
_MONEY = re.compile(
    rf'(?<![\w.])(?P<currency>US\$|[$£€]|USD\s+|EUR\s+|GBP\s+)(?P<number>{_NUMBER_TOKEN})'
    rf'(?P<scale>\s*{_SCALE_TOKEN})?', re.I)
_NUMBER = re.compile(
    rf'(?<![\w.])(?P<sign>[+-])?(?P<number>{_NUMBER_TOKEN})(?P<ordinal>st|nd|rd|th)?'
    rf'(?P<scale>\s*{_SCALE_TOKEN})?(?P<unit>\s*(?:%|percent(?:age points?)?|basis points?|bps|'
    rf'degrees?\s*[CF]|°\s*[CF]|dollars?|euros?|pounds?|minutes?|hours?|days?|years?|times?))?', re.I)
_ACRONYMS = {
    'AI': 'A-I', 'API': 'A-P-I', 'ATM': 'A-T-M', 'CDC': 'C-D-C', 'CEO': 'C-E-O',
    'CFO': 'C-F-O', 'CPI': 'C-P-I', 'CMS': 'C-M-S', 'FDA': 'F-D-A', 'FBI': 'F-B-I',
    'GDP': 'G-D-P', 'IRS': 'I-R-S', 'NASA': 'NASA', 'ACH': 'ACH', 'NC': 'N-C', 'SEC': 'S-E-C',
    'UN': 'U-N',
    'UK': 'U-K', 'US': 'U-S', 'WHO': 'W-H-O',
}


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
    return ' '.join(words) or 'zero'


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
            groups.append(f'{_under_thousand(group)} {_SCALES[scale]}'.strip())
        value //= 1000
        scale += 1
    return ' '.join(reversed(groups))


def _ordinal_words(value: int) -> str:
    irregular = {1: 'first', 2: 'second', 3: 'third', 5: 'fifth', 8: 'eighth', 9: 'ninth',
                 11: 'eleventh', 12: 'twelfth', 13: 'thirteenth', 15: 'fifteenth',
                 18: 'eighteenth', 19: 'nineteenth'}
    if value in irregular:
        return irregular[value]
    cardinal = _integer_words(value)
    if '-' in cardinal:
        head, last = cardinal.rsplit('-', 1)
        tail = {'one': 1, 'two': 2, 'three': 3, 'five': 5, 'eight': 8,
                'nine': 9, 'twelve': 12}.get(last)
        return head + '-' + _ordinal_words(tail) if tail else head + '-' + last + 'th'
    if cardinal.endswith('y'):
        return cardinal[:-1] + 'ieth'
    return cardinal + 'th'


def _scale_words(value: str | None) -> str:
    return (value or '').strip().lower()


def _year_words(value: int) -> str:
    if 2000 <= value <= 2009:
        return 'two thousand' + (f' {_integer_words(value - 2000)}' if value > 2000 else '')
    if 2010 <= value <= 2099:
        return 'twenty ' + _under_thousand(value - 2000)
    if 1900 <= value <= 1999:
        remainder = value - 1900
        return 'nineteen hundred' if remainder == 0 else 'nineteen ' + _under_thousand(remainder)
    return _integer_words(value)


def _number_words(raw: str, *, year: bool = False) -> str:
    clean = raw.replace(',', '')
    if '.' not in clean:
        value = int(clean)
        return _year_words(value) if year and len(clean) == 4 else _integer_words(value)
    whole, fraction = clean.split('.', 1)
    return f"{_integer_words(int(whole))} point {' '.join(_ONES[int(digit)] for digit in fraction)}"


def _money_value(raw: str, scale: str, currency: str) -> str:
    clean = raw.replace(',', '')
    currency_name = {'$': 'dollar', 'US$': 'dollar', 'USD': 'dollar',
                     '£': 'pound', 'GBP': 'pound', '€': 'euro', 'EUR': 'euro'}[currency]
    scale = _scale_words(scale)
    if '.' in clean and not scale:
        whole, cents = clean.split('.', 1)
        if len(cents) <= 2:
            cents_value = int((cents + '00')[:2])
            major = _integer_words(int(whole))
            major_label = currency_name if int(whole) == 1 else currency_name + 's'
            if cents_value == 0:
                return f'{major} {major_label}'
            cents_label = 'cent' if cents_value == 1 else 'cents'
            if int(whole) == 0:
                return f'{_integer_words(cents_value)} {cents_label}'
            return f'{major} {major_label} and {_integer_words(cents_value)} {cents_label}'
    if '.' in clean:
        whole, fraction = clean.split('.', 1)
        half = {'5': 'half', '50': 'half', '25': 'quarter', '75': 'three quarters'}.get(fraction.rstrip('0'))
        if half:
            whole_value = int(whole)
            fraction_words = f'a {half}' if half in {'half', 'quarter'} else half
            value = f'{_integer_words(whole_value)} and {fraction_words}' if whole_value else fraction_words
        else:
            value = _number_words(raw)
    else:
        value = _integer_words(int(clean))
    label = currency_name + 's'
    return ' '.join(part for part in (value, scale, label) if part)


def _date_parts(year: int | None, month: str, day: str) -> str:
    month_index = next(index for index, name in enumerate(_MONTHS, 1)
                       if name[:3].casefold() == month[:3].casefold())
    day_number = int(day)
    try:
        date(year or 2000, month_index, day_number)
    except ValueError:
        return f'{month} {_number_words(day)}' + (f', {_year_words(year)}' if year else '')
    result = f'{_MONTHS[month_index - 1]} {_ordinal_words(day_number)}'
    return result + (f', {_year_words(year)}' if year else '')


def _speak_acronym(match: re.Match) -> str:
    acronym = match.group(1)
    return _ACRONYMS.get(acronym, '-'.join(acronym))


def _normalize_number(match: re.Match) -> str:
    sign = match.group('sign') or ''
    value = _number_words(match.group('number'), year=not match.group('unit') and not match.group('scale'))
    ordinal = match.group('ordinal')
    if ordinal:
        value = _ordinal_words(int(match.group('number').replace(',', '')))
    scale = _scale_words(match.group('scale'))
    unit = (match.group('unit') or '').strip().lower()
    value = ' '.join(part for part in (value, scale) if part)
    if unit.rstrip('s') in {'dollar', 'euro', 'pound'}:
        value = _money_value(match.group('number'), match.group('scale') or '',
                             money_symbol(unit.rstrip('s')))
        return ('minus ' if sign == '-' else 'plus ' if sign == '+' else '') + value
    unit = {'%': 'percent', 'bps': 'basis points', 'basis point': 'basis points',
            'degree c': 'degrees Celsius', 'degrees c': 'degrees Celsius',
            '° c': 'degrees Celsius', '°c': 'degrees Celsius', 'degree f': 'degrees Fahrenheit',
            'degrees f': 'degrees Fahrenheit', '° f': 'degrees Fahrenheit', '°f': 'degrees Fahrenheit'}.get(unit, unit)
    if unit in {'dollar', 'dollars', 'euro', 'euros', 'pound', 'pounds'}:
        unit = unit.rstrip('s') + 's'
    prefix = 'minus ' if sign == '-' else 'plus ' if sign == '+' else ''
    return prefix + ' '.join(part for part in (value, unit) if part)


def _normalize_range(match: re.Match) -> str:
    left, right = match.group('left'), match.group('right')
    left_currency, right_currency = match.group('lc'), match.group('rc')
    scale = _scale_words(match.group('ls') or match.group('rs'))
    unit = (match.group('unit') or '').strip().lower()
    currency = left_currency or right_currency
    is_money = bool(currency) or unit.rstrip('s') in {'dollar', 'euro', 'pound'}
    if is_money:
        currency_name = {'$': 'dollar', 'US$': 'dollar', '£': 'pound', '€': 'euro',
                         'USD': 'dollar', 'EUR': 'euro', 'GBP': 'pound'}
        money = currency_name.get((currency or '').upper(), unit.rstrip('s') or 'dollar')
        symbol = money_symbol(money)
        left_scale = _scale_words(match.group('ls') or match.group('rs'))
        right_scale = _scale_words(match.group('rs') or match.group('ls'))
        return f'{_money_value(left, left_scale, symbol)} to {_money_value(right, right_scale, symbol)}'
    if unit:
        unit = {'%': 'percent', 'bps': 'basis points', 'basis point': 'basis points'}.get(unit, unit)
    left_year = len(left.replace(',', '')) == 4 and left.isdigit()
    right_year = len(right.replace(',', '')) == 4 and right.isdigit()
    spoken = f'{_number_words(left, year=left_year)} to {_number_words(right, year=right_year)}'
    return ' '.join(part for part in (spoken, scale, unit) if part)


def money_symbol(currency: str) -> str:
    return {'dollar': '$', 'pound': '£', 'euro': '€'}.get(currency, '$')


def _normalize_paragraph(text: str) -> str:
    text = _LINK_ONLY_REFERENCE_SENTENCE.sub(r'\1', text)
    text = _CITATION_LINK.sub('', text)
    text = _CITATION.sub('', text)
    text = re.sub(r'\[([^\]]+)\]\((?:https?://|www\.)[^)]+\)', r'\1', text, flags=re.I)
    text = _URL.sub('', text)
    text = re.sub(r'\b(?:at|here|via)\s*(?=[,.;!?]?\s*$)', '', text, flags=re.I)

    def iso_date(match: re.Match) -> str:
        year, month, day = map(int, (match.group('year'), match.group('month'), match.group('day')))
        try:
            date(year, month, day)
        except ValueError:
            return f'{_year_words(year)} {month} {day}'
        return f'{_MONTHS[month - 1]} {_ordinal_words(day)}, {_year_words(year)}'

    text = _ISO_DATE.sub(iso_date, text)

    def glued_date_year(match: re.Match) -> str:
        day = re.sub(r'\s+', ' ', match.group('day').casefold())
        return f"{match.group('month')} {_ORDINAL_BY_CARDINAL[day]} "

    text = _GLUED_DATE_YEAR.sub(glued_date_year, text)

    def spoken_month_date(match: re.Match) -> str:
        day = re.sub(r'\s+', ' ', match.group('day').casefold())
        month = next((name for name in _MONTHS if name.casefold().startswith(match.group('month')[:3].casefold())),
                     match.group('month'))
        return f'{month} {_ORDINAL_BY_CARDINAL[day]}'

    text = _MONTH_DATE_WORDS.sub(spoken_month_date, text)

    def month_date(match: re.Match) -> str:
        year = int(match.group('year')) if match.group('year') else None
        return _date_parts(year, match.group('month'), match.group('day'))

    text = _MONTH_DATE.sub(month_date, text)

    def time_words(match: re.Match) -> str:
        hour, minute = int(match.group('hour')), int(match.group('minute'))
        spoken_hour = _number_words(str(hour))
        spoken_minute = 'o five' if minute == 5 else 'o ' + _number_words(str(minute)) if 0 < minute < 10 else _number_words(str(minute)) if minute else ''
        period = match.group('period')
        period_words = ''
        if period:
            period_words = 'a m' if period.casefold().startswith('a') else 'p m'
        return ' '.join(part for part in (spoken_hour, spoken_minute, period_words) if part)

    text = _SPOKEN_TIME_COLON.sub(lambda match: f"{match.group('hour')} {match.group('period')}", text)
    text = _TIME.sub(time_words, text)
    text = re.sub(r'\b([ap])\.?\s*m\.\s+(?=(?:eastern|central|mountain|pacific|et|ct|mt|pt)\b)',
                  lambda match: match.group(1) + ' m ', text, flags=re.I)
    text = _RANGE.sub(_normalize_range, text)

    def money(match: re.Match) -> str:
        currency = match.group('currency').strip().upper()
        return _money_value(match.group('number'), match.group('scale') or '', currency)

    text = _MONEY.sub(money, text)
    text = _NUMBER.sub(_normalize_number, text)

    # A spelled-out name followed by its parenthetical acronym is already an
    # explicit expansion. Keep it audible without inventing an expansion.
    def paired_acronym(match: re.Match) -> str:
        acronym = match.group(1)
        prefix = text[:match.start()].rstrip()
        prefix = re.split(r'[.!?]\s+', prefix)[-1]
        phrase_match = re.search(r'((?:(?:[A-Z][\w’&-]*|for|and|of|the)\s+){1,8}[A-Z][\w’&-]*)\s*\($', prefix)
        if phrase_match:
            return 'or ' + '-'.join(acronym)
        return _speak_acronym(match)

    text = re.sub(r'\b([A-Z]{2,8})\b', paired_acronym, text)
    text = re.sub(r'\s*\(\s*or ([A-Z](?:-[A-Z])+?)\s*\)', r', or \1', text)
    text = re.sub(r'(?i)\bU\.S\.(?=\W|$)', 'U-S', text)
    text = re.sub(r'(?i)\bU\.K\.(?=\W|$)', 'U-K', text)
    text = re.sub(r'\b(?:[A-Z]\.){2,8}\b', lambda match: '-'.join(match.group(0).replace('.', '')), text)
    text = _GLUED_CURRENCY_PREPOSITION.sub(lambda match: f"{match.group('currency')} {match.group('next')}", text)
    text = re.sub(r'\s*&\s*', ' and ', text)
    text = re.sub(r'\s*/\s*', ' per ', text)
    text = re.sub(r'\s*\^\s*', ' to the power of ', text)
    text = re.sub(r'~\s*', 'about ', text)
    text = re.sub(r'\s*>\s*', ' greater than ', text)
    text = re.sub(r'\s*<\s*', ' less than ', text)
    text = text.replace('—', ', ').replace('–', ', ').replace(';', '.')
    text = re.sub(r'\(\s*\)', '', text)
    text = re.sub(r'\(([^()]*)\)', r', \1', text)
    text = re.sub(r'\s*#\s*(?=\w)', 'number ', text)
    text = re.sub(r'\s*\+\s*', ' plus ', text)
    text = re.sub(r'\s*=\s*', ' equals ', text)
    text = re.sub(r'\s*:\s*', ', ', text)
    text = re.sub(r'\s+([,.!?])', r'\1', text)
    text = re.sub(r',\s*,+', ',', text)
    text = re.sub(r'\s+', ' ', text).strip(' ,')
    if re.search(r'\d|[$£€%]', text):
        raise ValueError('narration contains a number or symbol that is not supported for speech')
    if text:
        text = text[0].upper() + text[1:]
    if text and not re.search(r'[.!?]$', text):
        text += '.'
    return text


def normalize_narration(text: str) -> str:
    """Return deterministic ear-ready text while preserving paragraph pauses."""
    if not isinstance(text, str):
        raise TypeError('narration text must be a string')
    paragraphs = [paragraph for paragraph in re.split(r'\n\s*\n', text.strip()) if paragraph.strip()]
    return '\n\n'.join(filter(None, (_normalize_paragraph(row) for row in paragraphs)))
