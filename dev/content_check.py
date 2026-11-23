#!/usr/bin/env python3
"""Read-only, provider-free editorial checks. Heuristics flag review, not truth."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from difflib import SequenceMatcher
import json
import os
from pathlib import Path
import re
from urllib.parse import urlsplit
from urllib.request import urlopen

from dev.ingest.repeats import canonical_url as repeat_canonical_url, edition_seen_by_reader

STOP = set('a an the and or of to in on for with from by is are was were as at this that new latest update news today how why it its has have will'.split())


def words(text):
    tokens = set(re.findall(r'[a-z0-9]+', str(text).casefold())) - STOP
    return {token[:-1] if len(token) > 4 and token.endswith('s') and not token.endswith(('ss', 'us')) else token for token in tokens}


def timestamp(value):
    try:
        parsed = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)
    except ValueError:
        return None


def canonical_url(value):
    return repeat_canonical_url(value)


def url_path(value):
    try:
        return urlsplit(str(value or '')).path
    except (TypeError, ValueError):
        return ''


def unpack(payload):
    """Accept API response, current.json/history bundle, or a bare edition."""
    if not isinstance(payload, dict):
        raise ValueError('edition must be a JSON object')
    edition = payload.get('edition', payload)
    if 'editions' in payload:
        rows = payload['editions']
        rows = rows.get('editions') if isinstance(rows, dict) else rows
        edition = next((row for row in rows if isinstance(row, dict) and row.get('kind', 'daily') == 'daily'), {}) if isinstance(rows, list) else {}
    if not isinstance(edition, dict) or not isinstance(edition.get('sections'), list):
        raise ValueError('edition sections missing or invalid')
    sources = payload.get('sources', edition.get('sources', []))
    sources = sources.get('sources', []) if isinstance(sources, dict) else sources
    if not isinstance(sources, list):
        raise ValueError('sources must be a list')
    return edition, {row['id']: row for row in sources if isinstance(row, dict) and row.get('id')}


def stories(edition):
    return [row for row in edition['sections'] if isinstance(row, dict)
            and (row.get('cluster_id') or row.get('kind', 'story') == 'story')
            and row.get('kind') not in {'intro', 'outro', 'quiet', 'coverage'}]


def heading(story):
    return str(story.get('heading') or story.get('headline') or story.get('title') or '')


def entities(story):
    explicit = story.get('entities') or []
    if isinstance(explicit, str):
        explicit = [explicit]
    found = {str(row.get('name', '') if isinstance(row, dict) else row).casefold().strip() for row in explicit}
    # Conservative fallback for proper names/acronyms when the writer omits entities.
    found.update(token.casefold() for token in re.findall(r'\b[A-Z][A-Za-z0-9]+\b', heading(story))
                 if token.casefold() not in STOP)
    return found - {''}


def linked(story, sources):
    return [sources[source_id] for source_id in story.get('source_ids', []) if source_id in sources]


def topic_vocabularies(profile):
    """Keep each selected topic's terms together so unrelated topics cannot lend relevance."""
    topics = profile.get('topics', []) if isinstance(profile, dict) else []
    topics = topics if isinstance(topics, list) else []
    vocabularies = []
    for topic in topics:
        if isinstance(topic, dict):
            text = ' '.join(str(topic.get(key, '')) for key in ('label', 'scope', 'keywords', 'entities', 'include'))
            identifier = str(topic.get('id') or topic.get('topic_id') or '').casefold()
            place_text = ' '.join(str(topic.get(key, '')) for key in ('label', 'scope', 'sourcePhrase'))
            place_terms = set()
            for match in re.finditer(r'\b[A-Z]{2,}\b|\b[A-Z][a-z]{2,}(?:\s+[A-Z][a-z]{2,})*', place_text):
                place_terms.update(words(match.group(0)))
            place_terms -= {'health', 'healthcare', 'insurance', 'coverage', 'claim', 'policy',
                            'finance', 'financial', 'medicare', 'medicaid', 'state', 'states'}
        else:
            text, identifier = str(topic), ''
            place_terms = set()
        vocabulary = words(text)
        if vocabulary:
            vocabularies.append((identifier, vocabulary, place_terms))
    # Old profiles may have only a free-text description. Use it only as a single
    # fallback scope, never as extra vocabulary shared across structured topics.
    if not vocabularies and isinstance(profile, dict):
        vocabulary = words(profile.get('words', ''))
        if vocabulary:
            vocabularies.append(('', vocabulary, set()))
    return vocabularies


def _explicit_region_terms(text):
    """Return place names attached to explicit state/region markers."""
    value = str(text or '')
    name = r'((?:[A-Z]{2,}|[A-Z][a-z]+)(?:\s+(?:[A-Z]{2,}|[A-Z][a-z]+))*)'
    patterns = (
        rf'\b(?:state|province|territory|county|city|region)\s+of\s+{name}',
        rf'\b{name}(?:\'s)?\s+(?:state|province|territory|county|city|region)\b',
    )
    excluded = {'cms', 'medicare', 'medicaid', 'health', 'insurance', 'policy', 'federal',
                'national', 'united', 'states', 'state', 'all', 'the'}
    found = set()
    for pattern in patterns:
        for match in re.finditer(pattern, value):
            found.update(words(match.group(1)) - excluded)
    return found


def _has_national_scope(text):
    """Require explicit copy that a policy applies nationally or across states."""
    value = str(text or '')
    explicit_application = (
        r'\b(?:applies?|applicable|effective|in\s+effect|available|covers?|covering)\b'
        r'.{0,60}\b(?:nationwide|nationally|countrywide|across\s+all\s+states|'
        r'in\s+every\s+state|all\s+states|every\s+state)\b')
    explicit_scope = (r'\b(?:nationwide|countrywide)\s+(?:polic(?:y|ies)|rule|proposal|program|coverage|'
                      r'requirements?|safeguards?|standards?)\b|'
                      r'\bacross\s+all\s+states\b|\bin\s+every\s+state\b|\ball\s+states\b')
    return bool(re.search(explicit_application, value, re.I | re.S) or
                re.search(explicit_scope, value, re.I))


def _is_region_limited(text):
    return bool(re.search(r'\b(?:only|solely|confined|limited|restricted)\b.{0,80}\b'
                          r'(?:state|province|territory|county|city|region)\b',
                          str(text or ''), re.I | re.S))


def _topic_relevant(vocabulary, text, place_terms=None):
    """Apply narrow banking and public-payer synonyms after literal overlap."""
    story_words = words(text)
    # Do not let "bank" alone (for example, a river bank) satisfy Banking.
    # Accept the bank/banking word-form difference only with financial or
    # supervisory context in the actual story.
    has_banking_scope = bool(vocabulary & {'bank', 'banking'})
    has_bank_term = bool(story_words & {'bank', 'banking'})
    banking_context = bool(story_words & {
        'federal', 'reserve', 'enforcement', 'regulator', 'regulatory', 'supervision',
        'agreement', 'deposit', 'loan', 'lending', 'capital', 'financial', 'stability',
    })
    # A bank's transaction-processing incident is in scope even when the copy
    # does not repeat "financial" or "payments".  Keep the context requirement
    # so a literal river bank still cannot satisfy a banking topic.
    transaction_terms = story_words & {'transaction', 'payment', 'settlement', 'transfer'}
    transaction_signal = story_words & {
        'processing', 'delay', 'disruption', 'error', 'errors', 'surge', 'issue', 'issues',
    }
    transaction_context = bool(transaction_terms and transaction_signal)
    insurance_scope = bool(vocabulary & {'insurance', 'coverage', 'claim', 'policy'})
    generic_health_terms = {'health', 'healthcare', 'policy', 'state', 'states'}
    direct_vocabulary = vocabulary - {'bank', 'banking'}
    scope_place_terms = place_terms or set()
    if insurance_scope:
        # "Health" and "policy" alone can describe hospitals, visitor rules,
        # or many unrelated subjects. Require more specific overlap or the
        # public-payer path below.
        direct_vocabulary -= generic_health_terms
        # Exclude named places from ordinary overlap. A public-payer match must
        # carry the selected place, where the profile names one.
        direct_vocabulary -= scope_place_terms
    direct = direct_vocabulary & story_words
    named_scope_match = bool(story_words & scope_place_terms)
    explicit_regions = _explicit_region_terms(text)
    other_region = bool(explicit_regions - scope_place_terms) if scope_place_terms else False
    national_scope = _has_national_scope(text)
    if (insurance_scope and scope_place_terms and other_region and not named_scope_match and
            (not national_scope or _is_region_limited(text))):
        return False
    if direct or (has_banking_scope and has_bank_term and (banking_context or transaction_context)):
        return True

    # Coverage reforms can be material to a health-insurance finance topic
    # through hospital uncompensated-care exposure even when the article does
    # not repeat an insurer or public-program name. Keep every part of this
    # signal so an ordinary hospital operations story cannot qualify.
    coverage_finance_context = (
        insurance_scope and 'coverage' in story_words and
        {'uncompensated', 'care'} <= story_words and
        bool(story_words & {'margin', 'margins', 'finance', 'financial', 'cost', 'costs'}) and
        bool(story_words & {'reform', 'reforms', 'policy', 'policies', 'rule', 'rules'}))
    if coverage_finance_context:
        return True

    # A selected AI-in-finance topic can describe the same technology as
    # "machine learning" without repeating "AI" or "finance" in the story.
    # Require both a finance-scoped profile and explicit financial-institution
    # context in the story; unrelated machine-learning items stay out.
    ai_scope = bool(vocabulary & {'ai', 'artificial', 'intelligence'})
    finance_scope = bool(vocabulary & {'finance', 'financial', 'bank', 'banking', 'payments',
                                       'payment', 'fintech', 'credit', 'lending', 'insurance'})
    machine_learning = {'machine', 'learning'} <= story_words
    financial_context = bool(story_words & {
        'bank', 'banks', 'banking', 'lender', 'lenders', 'lending', 'credit', 'loan', 'loans',
        'underwriting', 'payment', 'payments', 'fintech', 'insurance', 'insurer', 'insurers',
        'capital', 'investment', 'trading',
    })
    if ai_scope and finance_scope and machine_learning and financial_context:
        return True

    # Medicare and Medicaid are health-insurance programs, but "healthcare" or
    # the CMS acronym alone is too broad. Require a public-payer issue, an
    # integrity/policy signal, and any named geographic scope from the selected
    # topic when one is present.
    public_payer = bool(story_words & {'medicare', 'medicaid'})
    integrity_signal = bool(story_words & {
        'fraud', 'integrity', 'claim', 'claims', 'eligibility', 'coverage', 'enforcement',
        'proposal', 'proposed', 'rule', 'requirement', 'requirements',
    })
    if not (insurance_scope and public_payer and integrity_signal):
        return False
    if named_scope_match:
        return True
    if other_region:
        return national_scope and not _is_region_limited(text)
    if scope_place_terms:
        # A regional topic may also accept an explicitly national policy when
        # the story itself states that it applies across the country/states.
        # Federal CMS rules for state-administered Medicaid/CHIP are an
        # equivalent explicit applicability signal: they govern each state's
        # program without claiming a named state-specific policy.
        federal_state_program = (
            'federal' in story_words and
            bool(story_words & {'cms', 'medicare', 'medicaid', 'chip'}) and
            bool(story_words & {'rule', 'rules', 'regulation', 'regulations', 'proposal', 'proposed'}) and
            bool(story_words & {'state-administered', 'state', 'states'}) and
            bool(story_words & {'medicaid', 'chip'}))
        return national_scope or federal_state_program
    if vocabulary & {'state', 'states'}:
        return national_scope or bool(story_words & {'state', 'states', 'statewide'})
    return True


def _update_facts(text):
    value = str(text or '')
    dates = re.findall(r'\b(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|'
                       r'Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\s+\d{1,2}'
                       r'(?:,?\s+\d{4})?|\b\d{4}-\d{2}-\d{2}\b', value, re.I)
    numbers = re.findall(r'(?<!\w)(?:\$\s*)?\d+(?:[,.]\d+)*(?:\s?%|\s?(?:million|billion|thousand|day|days|month|months|year|years))?(?!\w)',
                         value, re.I)
    return ({'date:' + re.sub(r'\s+', ' ', item.casefold()).strip() for item in dates} |
            {'number:' + re.sub(r'\s+', '', item.casefold()) for item in numbers})


def _is_supported_followup(story, current_sources, previous_edition, previous_story, previous_sources):
    if str(story.get('kicker') or '').casefold() != 'update:' or story.get('new_fact_supported') is not True:
        return False
    covered_at = timestamp(previous_edition.get('generated_at') or previous_edition.get('local_date'))
    dates = [timestamp(source.get('published_at') or source.get('published')) for source in current_sources]
    dates = [value for value in dates if value]
    if not covered_at or not dates or max(dates) <= covered_at:
        return False
    current_text = ' '.join([heading(story), story.get('body', ''),
                             *(source.get('title', '') for source in current_sources)])
    previous_text = ' '.join([heading(previous_story), previous_story.get('body', ''),
                              *(source.get('title', '') for source in previous_sources)])
    new_date_or_number = _update_facts(current_text) - _update_facts(previous_text)
    new_entities = entities(story) - entities(previous_story)
    return bool(new_date_or_number or new_entities)


def similarity(first, second):
    a, b = words(first), words(second)
    dice = 2 * len(a & b) / (len(a) + len(b)) if a and b else 0
    normalize = lambda text: ' '.join(re.findall(r'\w+', text.casefold()))
    return max(dice, SequenceMatcher(None, normalize(first), normalize(second)).ratio()) if first and second else 0


def prior_editions(history, current):
    if isinstance(history, dict):
        history = history.get('history')
    if not isinstance(history, list):
        raise ValueError('edition history missing or invalid (use [] for a known empty history)')
    candidates = []
    current_time = timestamp(current.get('generated_at') or current.get('local_date'))
    seen = set()
    for payload in history:
        edition, sources = unpack(payload)
        if not edition_seen_by_reader(payload, edition):
            continue
        # Revisions of the same day remain eligible; the current revision does not.
        key = edition.get('revision_id') or edition.get('id')
        current_key = current.get('revision_id') or current.get('id')
        if key and (key == current_key or key in seen):
            continue
        when = timestamp(edition.get('generated_at') or edition.get('local_date'))
        if current_time and when and when > current_time:
            continue
        if key:
            seen.add(key)
        candidates.append((when or datetime.min.replace(tzinfo=timezone.utc), edition, sources))
    candidates.sort(key=lambda row: row[0], reverse=True)
    return [(edition, sources) for _, edition, sources in candidates[:5]]


def check_content(payload, history, profile, *, now=None):
    edition, sources = unpack(payload)
    rows = stories(edition)
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    reasons = {name: [] for name in ('repeats', 'freshness', 'relevance', 'citations', 'boilerplate', 'quality',
                                     'story count')}
    reviews = {}
    quality_failures = []
    try:
        previous = prior_editions(history, edition)
    except ValueError as error:
        previous = []
        reasons['repeats'].append(str(error))
    if isinstance(profile, dict) and isinstance(profile.get('profile'), dict):
        profile = profile['profile']
    vocabularies = topic_vocabularies(profile)
    if not vocabularies:
        reasons['relevance'].append('profile topics/keywords unavailable')
    is_daily_edition = isinstance(edition.get('daily_briefing'), dict)
    seen_takeaways = {}
    for number, story in enumerate(rows, 1):
        label = f'story {number} ({heading(story) or "untitled"})'
        cited = linked(story, sources)
        urls = {canonical_url(source.get('url')) for source in cited} - {''}
        if any(re.search(r'/(?:[^/]+/)*(?:spons|sponsored|partner-content|advertorial)(?:/|$)',
                         url_path(source.get('url')), re.I)
               for source in cited):
            quality_failures.append(f'{label}: cited source uses a sponsored content path')
        supported_update = False
        for old, old_sources in previous:
            for old_story in stories(old):
                old_cited = linked(old_story, old_sources)
                if _is_supported_followup(story, cited, old, old_story, old_cited):
                    supported_update = True
                    break
                matches = []
                old_urls = {canonical_url(source.get('url')) for source in old_cited} - {''}
                if urls & old_urls:
                    matches.append('same source URL')
                ratio = similarity(heading(story), heading(old_story))
                if ratio >= .6:
                    matches.append(f'headline similarity {ratio:.2f} >= 0.60')
                overlap = entities(story) & entities(old_story)
                # A company, regulator, team, or place can legitimately recur
                # in distinct developments. Entity overlap strengthens a URL
                # or headline match, but is not enough to call a story a repeat.
                if overlap and matches:
                    matches.append('same entities: ' + ', '.join(sorted(overlap)))
                if matches:
                    reasons['repeats'].append(f'{label} vs {old.get("local_date") or old.get("id", "prior edition")}: ' + '; '.join(matches))
            if supported_update:
                break
        lead_ids = story.get('lead_source_ids') or ([story['lead_source_id']] if story.get('lead_source_id') else story.get('source_ids', [])[:1])
        if not lead_ids:
            reasons['freshness'].append(f'{label}: lead source missing')
        for source_id in lead_ids:
            source = sources.get(source_id, {})
            published = timestamp(source.get('published_at') or source.get('published'))
            if published is None:
                source_class = str(source.get('source_class') or source.get('kind') or '').casefold()
                reference = source_class in {'primary', 'official', 'reference', 'explainer'}
                if not reference or str(story.get('kicker') or '').casefold() == 'update:':
                    reasons['freshness'].append(f'{label}: lead {source_id} publication date missing/invalid')
            elif published > now:
                reasons['freshness'].append(f'{label}: lead {source_id} publication date is in the future')
        body = str(story.get('body') or '')
        title = heading(story).strip()
        if is_daily_edition:
            cited_sentences = re.findall(
                r'[^.!?]+[.!?]\s*\[(?:\d+(?:\s*,\s*\d+)*)\]', body)
            distinct_cited_sentences = {
                ' '.join(re.findall(r'\w+', re.sub(r'\[(?:\d+(?:\s*,\s*\d+)*)\]', '', sentence).casefold()))
                for sentence in cited_sentences
            }
            distinct_cited_sentences.discard('')
            if len(distinct_cited_sentences) < 2:
                quality_failures.append(
                    f'{label}: insufficient cited substance ({len(distinct_cited_sentences)} distinct cited sentences; '
                    'need 2 distinct cited sentences)')
            takeaway = str(story.get('read_takeaway') or story.get('takeaway') or '').strip()
            normalized_takeaway = ' '.join(re.findall(r'\w+', re.sub(r'\[[^]]*\]', '', takeaway).casefold()))
            if normalized_takeaway:
                prior_story = seen_takeaways.get(normalized_takeaway)
                if prior_story:
                    quality_failures.append(
                        f'{label}: takeaway duplicates story {prior_story}')
                else:
                    seen_takeaways[normalized_takeaway] = number
        if (re.match(r'^(?:\d+\s+(?:things|ways|reasons)\b|best\s+.+\bcards?\b|review\b|how\s+to\s+get\b|deals?\b)', title, re.I) or
                re.search(r'\b(?:affiliate|sponsored|promo code|advertorial|paid partnership)\b',
                          title + ' ' + body, re.I)):
            quality_failures.append(f'{label}: listicle or promotion pattern')
        raw_topic_ids = story.get('topic_ids', [])
        raw_topic_ids = [raw_topic_ids] if isinstance(raw_topic_ids, str) else raw_topic_ids
        story_topic_ids = {str(value).casefold() for value in raw_topic_ids if value} if isinstance(raw_topic_ids, list) else set()
        candidates = ([(vocabulary, place_terms) for identifier, vocabulary, place_terms in vocabularies
                       if identifier and identifier in story_topic_ids]
                      if story_topic_ids else [(vocabulary, place_terms)
                                               for _, vocabulary, place_terms in vocabularies])
        story_text = heading(story) + ' ' + body + ' ' + ' '.join(entities(story))
        if not any(_topic_relevant(vocabulary, story_text, place_terms)
                   for vocabulary, place_terms in candidates):
            reasons['relevance'].append(f'{label}: no topic keyword/entity overlap (topic IDs alone do not count)')
        markers = re.findall(r'\[(\d+(?:\s*,\s*\d+)*)\]', body)
        ids = story.get('source_ids', [])
        numbers = [int(value.strip()) for marker in markers for value in marker.split(',')]
        if not numbers or any(n < 1 or n > len(ids) or not canonical_url(sources.get(ids[n-1], {}).get('url')) for n in numbers):
            reasons['citations'].append(f'{label}: missing or unresolved inline source citation')
        text = ' '.join(str(story.get(key, '')) for key in ('body', 'summary', 'read', 'read_takeaway', 'takeaway', 'lead_paragraphs', 'why_it_matters'))
        if re.search(r'\blimits\s*:', text, re.I):
            reasons['boilerplate'].append(f'{label}: contains Limits: text')
        read_parts = re.findall(r'\bRead:\s*([^\n]+)', body, re.I)
        for field in ('read', 'read_takeaway', 'takeaway'):
            if story.get(field):
                read_parts.append(str(story[field]))
        remaining = re.sub(r'\bRead:\s*[^\n]+', '', body, flags=re.I)
        sentences = re.split(r'(?<=[.!?])\s+|\n+', remaining)
        normalize = lambda value: ' '.join(re.findall(r'\w+', re.sub(r'\[[^]]*\]', '', value).casefold()))
        for read in read_parts:
            read = normalize(read)
            normalized_sentences = [normalize(sentence) for sentence in sentences]
            if read and any(sentence and (read == sentence or
                                          (len(sentence.split()) >= 6 and sentence in read))
                            for sentence in normalized_sentences):
                reasons['boilerplate'].append(f'{label}: Read: repeats a body sentence')
    reasons['quality'].extend(quality_failures)
    daily = edition.get('daily_briefing')
    if isinstance(daily, dict):
        reasons['daily length'] = []
        spoken_words = daily.get('spoken_words', edition.get('spoken_word_count', 0))
        spoken_words = spoken_words if type(spoken_words) is int and spoken_words >= 0 else 0
        # Duration is a review signal, not a content gate. Story count,
        # substantive cited facts, relevance and quality are checked separately.
        target_words = daily.get('target_spoken_words')
        narration_rate = daily.get('narrator_words_per_minute')
        narration_rate = narration_rate if type(narration_rate) is int and narration_rate > 0 else 150
        maximum_words = daily.get('maximum_spoken_words')
        script = daily.get('briefing_script')
        if isinstance(script, list):
            script_text = ' '.join(str(row.get('text') or '') for row in script if isinstance(row, dict))
            from dev.ingest.audio_script import _speak_text
            actual_words = len(re.findall(r"\b[\w’'-]+\b", _speak_text(script_text)))
            # Script text is authoritative when available; a stale counter
            # cannot make a short edition appear complete.
            spoken_words = min(spoken_words, actual_words) if spoken_words else actual_words
        ladder = daily.get('supply_ladder')
        required_rungs = {'feed', 'open_web', 'followups', 'background'}
        exhausted = (isinstance(ladder, list) and len(ladder) == len(required_rungs) and
                     {row.get('rung') for row in ladder if isinstance(row, dict)} == required_rungs and
                     all(isinstance(row, dict) and row.get('status') == 'exhausted' for row in ladder))
        searched = isinstance(daily.get('search_summary'), str) and bool(daily['search_summary'].strip())
        if type(target_words) is int and target_words > 0:
            minutes = spoken_words / narration_rate
            reviews['daily length'] = (
                f'duration review: {minutes:.1f} minutes at the configured narration rate; '
                f'{spoken_words} spoken words toward the {target_words}-word aim')
        if type(maximum_words) is int and maximum_words > 0 and spoken_words > maximum_words:
            reasons['daily length'].append(
                f'{spoken_words} spoken words exceeds the {maximum_words}-word edition ceiling')
        if edition.get('thin_day') is True and not (exhausted and searched):
            reasons['daily length'].append('thin_day requires every rung exhausted and a search summary')
        if isinstance(script, list):
            intros = [row for row in script if isinstance(row, dict) and row.get('kind') == 'story_intro']
            if len(intros) < len(rows):
                reasons['freshness'].append('daily script is missing a dated introduction for one or more stories')
            ai_narration = (isinstance(daily.get('greeting'), str) and
                            (daily['greeting'].startswith('Here is your briefing for') or
                             daily['greeting'].startswith('Good morning.')) and
                            all(row.get('source_sentence_ids') and row.get('citation_numbers')
                                for row in intros[:len(rows)]))
            for index, intro in enumerate(intros[:len(rows)], 1):
                text = str(intro.get('text') or '')
                if (not ai_narration and
                        not re.search(r'\bPublished\s+\d{1,2}\s+[A-Z][a-z]{2}\b', text) and not re.search(
                            r'\bDate not listed in the primary source\b', text, re.I)):
                    reasons['freshness'].append(f'story {index}: daily script has no honest publication date')
    if len(rows) == 0:
        reasons['story count'].append('no substantive stories in the edition')
    if not isinstance(daily, dict):
        reasons['relevance'].extend(quality_failures)
    return [{'name': name, 'passed': not failures,
             'reasons': failures or [
                 f'{len(rows)} substantive {"story" if len(rows) == 1 else "stories"}'
                 if name == 'story count' else 'no issue found'],
             'review': reviews.get(name)}
            for name, failures in reasons.items()]


def format_checks(checks):
    return [f'{"PASS" if row["passed"] else "FAIL"} {row["name"]}: ' + '; '.join(row['reasons']) +
            (f" | {row['review']}" if row.get('review') else '') for row in checks]


def load_json(location):
    if str(location).startswith(('http://', 'https://')):
        with urlopen(location, timeout=10) as response:
            if response.status != 200:
                raise ValueError(f'HTTP {response.status}')
            return json.load(response)
    return json.loads(Path(location).expanduser().read_text())


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('edition', nargs='?', help='edition JSON path or URL; defaults to active live edition')
    parser.add_argument('--history', help='history JSON path or URL; defaults to embedded history or active API history')
    parser.add_argument('--profile', help='profile JSON path or URL (required for offline files)')
    parser.add_argument('--base-url', default=os.environ.get('BRIEFING_LIVE_URL', 'http://127.0.0.1:8898'))
    parser.add_argument('--now', help='ISO timestamp; use a fixed time for historical fixtures')
    args = parser.parse_args(argv)
    try:
        base = args.base_url.rstrip('/')
        payload = load_json(args.edition or base + '/api/edition?profile=active')
        if not isinstance(payload, dict):
            raise ValueError('edition must be a JSON object')
        history = load_json(args.history) if args.history else payload.get('history')
        offline = args.edition and not args.edition.startswith(('http://', 'https://'))
        if history is None and not offline:
            history = load_json(base + '/api/edition?profile=active&history=1').get('history')
        profile = load_json(args.profile) if args.profile else ({} if offline else load_json(base + '/api/profile'))
        now = timestamp(args.now) if args.now else None
        if args.now and not now:
            raise ValueError('--now must be an ISO timestamp')
        checks = check_content(payload, history, profile, now=now)
        print('\n'.join(format_checks(checks)))
        return int(any(not row['passed'] for row in checks))
    except (OSError, ValueError, TypeError, KeyError) as error:
        print(f'FAIL content inputs: {error}')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
