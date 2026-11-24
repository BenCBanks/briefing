#!/usr/bin/env python3
"""Run the 12-point edition quality scorecard without storing article text."""

from pathlib import Path
import argparse
import hashlib
import json
import re
import sys

ROOT = Path(__file__).resolve().parents[2]
CRITICAL = {1, 2, 3, 4, 5, 6, 8, 11, 12, 13}
STOP = {'a','an','and','are','as','at','be','been','by','for','from','in','is','it','its','of','on','or','that','the','this','to','was','were','will','with','may','can','could','would','their','they','them','he','she','we','you','not','has','have','had','after','before','about','more','than','into','over','under','also','according','says','said'}
CLICKBAIT = {'shocking', 'unbelievable', 'you won’t believe', "you won't believe", 'must buy', 'guaranteed to soar', 'secret trick'}

try:
    from .flags import freshness_days
except ImportError:  # Direct script execution uses dev/ingest as sys.path[0].
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from dev.ingest.flags import freshness_days


def _tokens(text):
    return {word for word in re.findall(r'[a-z0-9]{2,}', text.lower()) if word not in STOP}


def _normalised_terms(text):
    return re.findall(r"[\w]+(?:['’][\w]+)?", text.casefold())


def _contains_term_sequence(text, term):
    haystack = _normalised_terms(text)
    needle = _normalised_terms(term)
    return bool(needle) and any(haystack[index:index + len(needle)] == needle
                                 for index in range(len(haystack) - len(needle) + 1))


def _name_supported(cited, name):
    def remove_possessive(value):
        return re.sub(r"(?i)(\b[\w]+)['’]s\b", r'\1', value)

    if (_contains_term_sequence(cited, name) or
            _contains_term_sequence(remove_possessive(cited), remove_possessive(name))):
        return True
    canonical_name = re.sub(r'\s+', ' ', remove_possessive(name).casefold().replace('&', 'and').replace('’', "'")).strip()
    expected_acronym = KNOWN_ACRONYMS.get(canonical_name)
    if expected_acronym and expected_acronym in _normalised_terms(cited):
        return True
    # News sources commonly switch between an organization's full name and its
    # conventional acronym. Accept only a mechanically exact initial sequence.
    if name.isupper() and 2 <= len(name) <= 8:
        phrases = re.findall(r"\b(?:[A-Z][a-z]+(?:\s+|(?=[.,;:!?]|$))){2,6}", cited)
        for phrase in phrases:
            words = [word for word in re.findall(r"[A-Z][a-z]+", phrase)
                     if word.casefold() not in {'the', 'a', 'an'}]
            if ''.join(word[0] for word in words).casefold() == name.casefold():
                return True
    words = _normalised_terms(name)
    if len(words) >= 2 and all(word[:1].isupper() for word in name.split()):
        # Conversely, a spelled-out entity is supported by its exact acronym.
        initials = ''.join(word[0] for word in name.split() if word.casefold() not in {'the', 'a', 'an'})
        cited_terms = _normalised_terms(cited)
        if initials and initials.casefold() in cited_terms:
            return True
    aliases = {'fed': ('federal reserve',), 'federal reserve': ('fed',)}
    return any(_contains_term_sequence(cited, alias) for alias in aliases.get(name.casefold(), ()))


KNOWN_ENTITY_WORDS = {
    'administration', 'agency', 'association', 'authority', 'bank', 'board', 'bureau',
    'center', 'centers', 'committee', 'commission', 'company', 'corporation', 'department',
    'federation', 'foundation', 'government', 'group', 'institute', 'program', 'reserve',
    'services', 'system', 'university', 'reforms'
}
KNOWN_NAMED_ENTITIES = {'medicare', 'medicaid', 'chip', 'cms', 'aca'}
KNOWN_ACRONYMS = {
    'centers for medicare and medicaid services': 'cms',
    "children's health insurance program": 'chip',
    'affordable care act': 'aca',
}
GENERIC_SENTENCE_STARTS = {
    'executing', 'while', 'furthermore', 'completing', 'payment', 'payments',
    'every', 'comparable', 'federal', 'according', 'however', 'overall',
    'the', 'a', 'an', 'this', 'that', 'these', 'those', 'it', 'they', 'we',
    'i', 'he', 'she', 'what', 'why', 'when', 'where', 'after', 'before',
    'under', 'during', 'state', 'agency', 'official', 'recently', 'currently',
    'separate', 'similar', 'newly'
}
ENTITY_CONNECTORS = {'for', 'and', 'of', 'the', '&'}


def _at_sentence_start(text, index):
    prefix = text[:index]
    return not prefix or bool(re.search(r"[.!?][’”\"]?\s*$", prefix))


def _appears_mid_sentence(text, phrase, after):
    for match in re.finditer(re.escape(phrase), text, re.I):
        if match.start() > after and not _at_sentence_start(text, match.start()):
            return True
    return False


def _likely_names(text, cited=''):
    # Mid-sentence capitalized phrases stay strict. At sentence starts, ignore
    # grammatical capitalization unless it is repeated mid-sentence, matches
    # cited evidence, or has a known institutional/location shape.
    token_pattern = r"\b(?:[A-Z][a-zA-Z’'-]{2,}|[A-Z]{2,8})\b"
    raw = list(re.finditer(token_pattern, text))
    runs = []
    index = 0
    while index < len(raw):
        run = [raw[index]]
        cursor = index + 1
        while cursor < len(raw):
            previous = run[-1].group(0).casefold().rstrip(".'’")
            if previous in {'services', 'department', 'bureau', 'commission', 'agency', 'board',
                            'corporation', 'program', 'administration', 'authority', 'association', 'university'}:
                break
            gap = text[run[-1].end():raw[cursor].start()]
            if not gap:
                break
            # Include lower-case joiners inside names (e.g. Centers for Medicare
            # and Medicaid Services) while keeping title-like words separate.
            connector_match = re.fullmatch(r"\s+(?:(?:for|and|of|the)\s+)?", gap, re.I)
            if connector_match:
                run.append(raw[cursor])
                cursor += 1
                continue
            break
        runs.append(run)
        index = cursor

    names = []
    for run in runs:
        words = [token.group(0) for token in run]
        at_start = _at_sentence_start(text, run[0].start())
        first = 0
        while first < len(words) and words[first].casefold() in {'a', 'an', 'the'}:
            first += 1
        if first == len(words):
            continue
        phrase = text[run[first].start():run[-1].end()].strip()
        if at_start:
            is_known_entity = (phrase.casefold() in KNOWN_NAMED_ENTITIES or
                               (len(words[first:]) > 1 and words[-1].casefold().rstrip(".'’") in KNOWN_ENTITY_WORDS))
            common_start = len(words[first:]) == 1 and words[first].casefold() in GENERIC_SENTENCE_STARTS
            if common_start:
                continue
            if not (is_known_entity or _appears_mid_sentence(text, phrase, run[-1].end()) or
                    _name_supported(cited, phrase)):
                continue
        if phrase not in names:
            names.append(phrase)
    return names


NUMBER_PATTERN = re.compile(
    r"(?<![\w])(?P<sign>[+-])?(?P<prefix>[$€£]|USD\s*)?(?P<number>\d[\d,]*(?:\.\d+)?)"
    r"(?:\s*(?P<scale>thousand|million|billion|trillion))?"
    r"(?:\s*(?P<unit>%|percent(?:age points?)?|basis points?|bps|USD|dollars?|euros?|pounds?))?",
    re.I)


def _numeric_assertions(text):
    assertions = []
    for match in NUMBER_PATTERN.finditer(text):
        sign = match.group('sign') or ''
        prefix = (match.group('prefix') or '').strip()
        if prefix.casefold() == 'usd':
            prefix = '$'
        number = match.group('number').replace(',', '')
        scale = (match.group('scale') or '').casefold()
        unit = (match.group('unit') or '').casefold()
        if unit == 'percent':
            unit = '%'
        elif unit in {'percentage point', 'percentage points'}:
            unit = 'pp'
        elif unit in {'basis point', 'basis points'}:
            unit = 'bps'
        elif unit in {'usd', 'dollar', 'dollars'}:
            prefix = '$'
            unit = ''
        elif unit in {'euro', 'euros'}:
            prefix = '€'
            unit = ''
        elif unit in {'pound', 'pounds'}:
            prefix = '£'
            unit = ''
        assertions.append(f'{sign}{prefix}{number}{scale}{unit}')
    return assertions


def _sentence_supported(text, ids, evidence):
    if not ids or any(source_id not in evidence for source_id in ids):
        return False
    cited = ' '.join(evidence[source_id].get('text', '') for source_id in ids)
    words = _tokens(text)
    evidence_words = _tokens(cited)
    if len(words) < 2 or len(words & evidence_words) / max(1, len(words)) < 0.24:
        return False
    numbers = _numeric_assertions(text)
    cited_numbers = set(_numeric_assertions(cited))
    return all(number in cited_numbers for number in numbers)


def _official(source):
    host = source.get('url', '').split('/')[2].lower() if '//' in source.get('url', '') else ''
    return source.get('source_class') == 'official' or host.endswith('.gov') or host.endswith('.int')


def _check(number, name, passed, detail):
    return {'number': number, 'name': name, 'status': 'PASS' if passed else 'FAIL',
            'critical': number in CRITICAL, 'detail': str(detail)[:260]}


def _not_applicable(number, name, detail):
    return {'number': number, 'name': name, 'status': 'N/A',
            'critical': number in CRITICAL, 'detail': str(detail)[:260]}


def score_edition(edition, sources, registry, evidence=None, rights_path=None):
    evidence = evidence or {}
    rights_path = Path(rights_path or ROOT / 'docs/source-rights.md')
    rights_doc = rights_path.read_text(encoding='utf-8') if rights_path.is_file() else ''
    source_by_id = {source.get('id'): source for source in sources if isinstance(source, dict)}
    registry_by_id = {source.get('id'): source for source in registry if isinstance(source, dict)}
    sections = [section for section in edition.get('sections', []) if section.get('cluster_id')]
    links = [(section, link) for section in sections for link in section.get('claim_links', [])]

    # 1 Claim coverage: every generated factual sentence must be represented in claim_links.
    factual_sections = [section for section in sections if section.get('section_blocks') or any(label in section.get('body', '') for label in
                        ('What happened:', 'Why it matters:', 'Things to consider:'))]
    check1 = all(section.get('claim_links') and all(
        link.get('text') and link.get('text') in section.get('body', '')
        for link in section.get('claim_links', [])) for section in factual_sections) and \
        len(factual_sections) == len(sections)
    checks = [_check(1, 'Claim coverage', check1, f'{len(links)} linked claim sentence(s) across {len(factual_sections)} story/stories')]

    # 2 Every cited sentence ID exists in the ephemeral extraction map and maps to a listed source.
    references_valid = True
    for section, link in links:
        ids = link.get('source_sentence_ids', [])
        if not ids or any(source_id not in evidence for source_id in ids):
            references_valid = False
            continue
        source_numbers = {evidence[source_id].get('source_number') for source_id in ids}
        if any(not isinstance(number, int) or number < 1 or number > len(section.get('source_ids', [])) for number in source_numbers):
            references_valid = False
    checks.append(_check(2, 'Citation validity', references_valid, 'Every sentence ID resolves to extracted evidence and a numbered source.' if references_valid else 'At least one sentence ID or numbered source is unresolved.'))

    # 3 Deterministic entailment screen repeats the writer's conservative lexical/number checks.
    entailment = all(link.get('verified') is True and _sentence_supported(link.get('text', ''),
                     link.get('source_sentence_ids', []), evidence) for _, link in links)
    checks.append(_check(3, 'Sentence support', entailment, f'{sum(1 for _, link in links if link.get("verified") is True)}/{len(links)} claim(s) passed the source-sentence check.'))

    # 4 Numeric values, dates, and capitalized names are checked against cited sentences.
    data_term_issues = []
    for _, link in links:
        cited = ' '.join(evidence.get(source_id, {}).get('text', '') for source_id in link.get('source_sentence_ids', []))
        text = link.get('text', '')
        terms = _numeric_assertions(text)
        cited_terms = set(_numeric_assertions(cited))
        names = _likely_names(text, cited)
        data_term_issues.extend(term for term in terms if term not in cited_terms)
        data_term_issues.extend(name for name in names if not _name_supported(cited, name))
    data_terms_ok = not data_term_issues
    detail = ('Numbers and likely names resolve in cited passages.' if data_terms_ok else
              'Unresolved cited numbers or names: ' + ', '.join(dict.fromkeys(data_term_issues))[:170])
    checks.append(_check(4, 'Numbers, dates, names', data_terms_ok, detail))

    # 5 Quotes must be short, exact, attributed, and linked to the passage.
    quote_ok = True
    quote_count = 0
    for section in sections:
        for quote in section.get('quotes', []):
            quote_count += 1
            ids = quote.get('evidence_ids', []) if isinstance(quote, dict) else []
            cited = ' '.join(evidence.get(source_id, {}).get('text', '') for source_id in ids)
            text = quote.get('text', '') if isinstance(quote, dict) else ''
            words = re.findall(r"\b[\w’'-]+\b", text)
            if len(words) > 15 or not quote.get('attribution') or not ids or text not in cited:
                quote_ok = False
            if len(text) > 0 and any(len(re.findall(r"\b[\w’'-]+\b", text[i:i + 600])) > 30 for i in range(0, len(text), 600)):
                quote_ok = False
    checks.append(_check(5, 'Quote hygiene', quote_ok, f'{quote_count} quote(s); maximum 15 words, exact source match and named attribution required.'))

    # 6 Use the same bounded freshness window as daily ingestion. Undated primary
    # sources can qualify: many official notices and reference documents publish
    # no date, and novelty is distinct from recency.
    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone.utc)
    freshness_window = timedelta(days=freshness_days())
    freshness_ok = True
    for source_id in {source_id for section in sections for source_id in section.get('source_ids', [])}:
        source = source_by_id.get(source_id, {})
        date = source.get('published_at') or source.get('updated_at')
        if not date:
            undated_primary = (source.get('source_class') in {'primary', 'official', 'reference'} or
                               source.get('kind') in {'primary', 'reference'})
            if not undated_primary:
                freshness_ok = False
            continue
        try:
            parsed = datetime.fromisoformat(date.replace('Z', '+00:00'))
            parsed = parsed.astimezone(timezone.utc) if parsed.tzinfo is not None else None
            freshness_ok &= parsed is not None and now - freshness_window <= parsed <= now + timedelta(days=1)
        except (AttributeError, TypeError, ValueError):
            freshness_ok = False
    checks.append(_check(6, 'Freshness', freshness_ok,
                         f'Cited sources are within {freshness_window.days} days, or are undated primary/reference sources.'))

    # 7 Prefer diverse outlets and an official source for finance/health where available.
    owners = []
    for source in sources:
        if source.get('id') in {sid for section in sections for sid in section.get('source_ids', [])}:
            owners.append(source.get('publisher') or source.get('registry_id'))
    diversity_ok = bool(owners) and max(owners.count(owner) for owner in set(owners)) / len(owners) <= 0.4
    official_needed = any(topic in {'payments', 'banking', 'insurance'} for section in sections for topic in section.get('topic_ids', []))
    official_available = any(_official(source) and source.get('enabled') is True for source in registry)
    selected_official = any(_official(source) for source in sources if source.get('id') in
                            {sid for section in sections for sid in section.get('source_ids', [])})
    if official_needed and official_available and not selected_official:
        diversity_ok = False
    if sections:
        checks.append(_check(7, 'Source diversity', diversity_ok, 'No publisher exceeds 40%; an official source is used when available for finance topics.'))
    else:
        checks.append(_not_applicable(7, 'Source diversity', 'No stories to assess.'))

    # 8 Cluster IDs are unique so a story is not repeated in the same edition.
    cluster_ids = [section.get('cluster_id') for section in sections]
    dedupe_ok = len(cluster_ids) == len(set(cluster_ids))
    checks.append(_check(8, 'Story deduplication', dedupe_ok, 'No cluster appears more than once.' if dedupe_ok else 'A story cluster appears more than once.'))

    # 9 Rule-based tone scan catches common clickbait and personalized advice phrasing.
    story_text = ' '.join(section.get('body', '').lower() for section in sections)
    tone_ok = not any(term in story_text for term in CLICKBAIT) and not re.search(r'\byou should (?:buy|sell|invest|take|stop)\b', story_text)
    checks.append(_check(9, 'Tone and advice', tone_ok, 'No configured clickbait phrases or personal buy/sell/health directives found.'))

    # 10 Length and script parity; short local editions can fail without blocking generation.
    script = '\n\n'.join(f"{section['heading']}\n{section['body']}" for section in edition.get('sections', []))
    words = len(re.findall(r"\b[\w’'-]+\b", script))
    parity_ok = edition.get('script_hash') == hashlib.sha256(script.encode('utf-8')).hexdigest() and \
                edition.get('audio', {}).get('script_hash') == edition.get('script_hash') and \
                edition.get('audio', {}).get('revision_id') == edition.get('revision_id')
    length_ok = 1500 <= words <= 2300
    if sections:
        checks.append(_check(10, 'Length and text/audio parity', parity_ok and length_ok,
                             f'{words} words; script/audio revision hashes ' + ('match.' if parity_ok else 'do not match.')))
    else:
        checks.append(_not_applicable(10, 'Length and text/audio parity', 'No stories to assess.'))

    # 11 Rights are assessed per cited source. Unused enabled feeds must not
    # invalidate this edition; every source the story actually cites remains strict.
    rights_failures = []
    cited_source_ids = set()
    for section in sections:
        source_ids = section.get('source_ids', [])
        if not isinstance(source_ids, list):
            continue
        markers = re.findall(r'\[(\d+(?:\s*,\s*\d+)*)\]', str(section.get('body') or ''))
        numbers = {int(number.strip()) for marker in markers for number in marker.split(',')}
        cited_source_ids.update(source_ids[number - 1] for number in numbers
                                if 1 <= number <= len(source_ids) and isinstance(source_ids[number - 1], str))
    for source_id in cited_source_ids:
        source = source_by_id.get(source_id, {})
        registry_id = source.get('registry_id')
        registered = registry_by_id.get(registry_id)
        reuse_terms = registered.get('reuse_terms') if registered else None
        if (not isinstance(reuse_terms, str) or not reuse_terms.strip() or
                f'`{registry_id}`' not in rights_doc or
                source.get('reuse_terms') != registered.get('reuse_terms')):
            rights_failures.append(f"cited:{registry_id or source_id}")
        if isinstance(reuse_terms, str) and 'link-only' in reuse_terms.lower():
            rights_failures.append(f"link-only:{registry_id}")
    rights_ok = not rights_failures
    rights_detail = 'Every cited source has reuse metadata and a source-rights entry.' if rights_ok else 'Rights metadata/documentation issues: ' + ', '.join(sorted(set(rights_failures))[:8])
    checks.append(_check(11, 'Source rights', rights_ok, rights_detail))

    # 12 A visible evidence status and limitations statement is present per story.
    limits_ok = all(section.get('source_status') in {'single-source', 'corroborated', 'official'} and
                    section.get('limits') and 'Limits:' in section.get('body', '') for section in sections)
    checks.append(_check(12, 'Limits and confidence label', limits_ok, 'Every story has a source-status label and visible Limits line.' if limits_ok else 'A story is missing a source-status label or Limits line.'))
    background_ok = True
    for section in sections:
        block = section.get('background_block')
        if block is None:
            continue
        if (not isinstance(block, dict) or block.get('label') != 'Background, not from today’s sources' or
                block.get('section') not in {'why_it_matters', 'things_to_consider'} or
                not isinstance(block.get('text'), str) or not block['text'].strip()):
            background_ok = False
            continue
        text = block['text']
        if re.search(r'\d|\b(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday|january|february|march|april|may|june|july|august|september|october|november|december)\b', text, re.I):
            background_ok = False
        for match in re.finditer(r'\b[A-Z][a-z]{2,}\b|\b[A-Z]{2,}\b', text):
            prefix = text[:match.start()].rstrip()
            if prefix and not re.search(r'[.!?][”\"\']?$', prefix):
                background_ok = False
    checks.append(_check(13, 'Uncited background separation', background_ok,
                         'Any general-knowledge background is a separate labelled context block with no names, dates, or numerals.' if background_ok else 'Uncited background is misplaced, unlabelled, or contains specific names, dates, or numerals.'))
    failed_critical = [check['number'] for check in checks if check['critical'] and check['status'] != 'PASS']
    return {'edition_id': edition.get('id'), 'revision_id': edition.get('revision_id'), 'checks': checks,
            'critical_failed': bool(failed_critical), 'failed_critical': failed_critical}


def print_scorecard(scorecard):
    for check in scorecard['checks']:
        severity = 'critical' if check['critical'] else 'noncritical'
        print(f"{check['status']} {check['number']:02d} {check['name']} ({severity}): {check['detail']}")
    print(f"Scorecard: {len(scorecard['checks'])} checks; critical failures: {scorecard['failed_critical']}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--edition', required=True)
    parser.add_argument('--sources', required=True)
    parser.add_argument('--evidence', help='Optional ephemeral JSON map of source sentence IDs to text; do not persist article text.')
    parser.add_argument('--registry', default=str(ROOT / 'content/sources-registry.json'))
    args = parser.parse_args()
    edition_data = json.loads(Path(args.edition).read_text(encoding='utf-8'))
    source_data = json.loads(Path(args.sources).read_text(encoding='utf-8'))
    registry = json.loads(Path(args.registry).read_text(encoding='utf-8'))['sources']
    evidence = json.loads(Path(args.evidence).read_text(encoding='utf-8')) if args.evidence else {}
    edition = edition_data.get('editions', [None])[0]
    result = score_edition(edition or {}, source_data.get('sources', []), registry, evidence)
    print_scorecard(result)
    return 1 if result['critical_failed'] else 0


if __name__ == '__main__':
    raise SystemExit(main())
