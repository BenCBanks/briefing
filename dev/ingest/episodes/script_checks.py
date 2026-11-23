"""Deterministic, offline checks for spoken episode scripts.

These checks are editorial shape checks, not a substitute for evidence or
semantic verification. They never call a model or a provider.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import date
import re
from typing import Callable, Any
from .policy import WORDS_PER_MINUTE

MIN_SENTENCE_WORDS = 4
MAX_AVERAGE_SENTENCE_WORDS = 20
MAX_SENTENCE_WORDS = 28
MAX_NUMBER_MENTIONS_PER_CHAPTER = 3
MAX_REPAIRS = 1
BANNED_PHRASES = (
    "in today's rapidly evolving",
    "marks a significant milestone",
    "underscores the importance",
    "it remains to be seen",
    "function interoperably",
    "this move brings",
    "one publisher only",
)

_WORD_RE = re.compile(r"\b[\w’'-]+\b", re.UNICODE)
_URL_RE = re.compile(r"(?:https?://|www\.)\S+", re.IGNORECASE)
_PARENTHETICAL_RE = re.compile(r"\([^)]*\)|[()]")
_DIGIT_RE = re.compile(r"\d")
_NUMBER_SYMBOL_RE = re.compile(r"[$€£¥%]|\b\d[\d,.:/-]*\b")
_CITATION_MARKER_RE = re.compile(
    r"\[\s*\d+(?:\s*,\s*\d+)*\s*\]|\b(?:source|citation|reference|footnote)\s+"
    r"(?:number\s+)?(?:\d+|one|two|three|four|five|six|seven|eight|nine|ten)\b", re.I)
_READER_REFERENCE_RE = re.compile(
    r"\b(?:see\s+(?:above|below|figure|table|source|link)|as\s+shown|"
    r"figure\s+\d+|table\s+\d+|click(?:\s+here)?|link|(?:the\s+)?link\s+(?:above|below|here))\b", re.I)
_SPOKEN_HEADING_RE = re.compile(
    r"^\s*(?:[-*•]|(?:chapter|section|heading|summary|read)(?:\s+\w+)?\s*:)\s*", re.I)
_ACRONYM_RE = re.compile(r"(?<![\w])(?:[A-Z](?:\.[A-Z]){1,}\.?|[A-Z]{2,})(?![\w])")
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+(?=[A-ZÀ-ÖØ-Þ‘’\"'])")
_QUALITY_STOP = {"a", "an", "and", "are", "as", "at", "be", "been", "before", "but",
                 "by", "for", "from", "has", "have", "how", "in", "into", "is", "it",
                 "of", "on", "or", "that", "the", "their", "then", "there", "these",
                 "this", "to", "was", "were", "what", "when", "where", "which", "who",
                 "why", "will", "with", "does", "did", "do"}
_QUALITY_ENUMERATION = re.compile(r"\b(?:first|second|third|fourth|fifth|finally|number one|number two)\b", re.I)
_QUALITY_ABSOLUTES = re.compile(
    r"\b(?:(?P<proof>proves?)|(?P<guarantee>guarantees?)|"
    r"(?P<certainty>certainly|undoubtedly|definitely)|(?P<always>always)|"
    r"(?P<never>never))\b", re.I)
_EPISODE_QUALITY_MANDATORY = {"no_cross_chapter_repetition", "no_list_reading",
                              "requested_duration", "sensitive_topic_framing",
                              "honest_uncertainty"}
_DIGITS = re.compile(r"(?<!\w)(?:\$\s*)?\d[\d,.]*(?:\s?(?:%|percent|million|billion|thousand))?(?!\w)", re.I)
_NUMBER_WORDS = {"zero", "one", "two", "three", "four", "five", "six", "seven", "eight",
                 "nine", "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen",
                 "sixteen", "seventeen", "eighteen", "nineteen", "twenty", "thirty",
                 "forty", "fifty", "sixty", "seventy", "eighty", "ninety", "hundred",
                 "thousand", "million", "billion", "dozen", "half", "halves", "first",
                 "second", "third", "fourth", "fifth", "sixth", "seventh", "eighth",
                 "ninth", "tenth", "eleventh", "twelfth", "thirteenth", "fourteenth",
                 "fifteenth", "sixteenth", "seventeenth", "eighteenth", "nineteenth",
                 "twentieth", "thirtieth", "fortieth", "fiftieth", "sixtieth", "seventieth",
                 "eightieth", "ninetieth", "once", "twice"}
_SENSITIVE_TOPIC = re.compile(
    r"\b(?:health|medicaid|medicare|hospital|patient|coverage|bank(?:ing)?|payment|payments|"
    r"deposit(?:s)?|lender|credit union|crypto(?:currency)?|bitcoin|ethereum|stablecoin|"
    r"politic(?:s|al)?|election|congress|senator|minister|president|campaign|lawmaker)\b", re.I)
_SENSATIONAL = re.compile(
    r"\b(?:shocking|bombshell|explosive|devastating|chaos|meltdown|bloodbath|disaster|"
    r"slams|furious|sparks outrage|under fire|financial apocalypse)\b", re.I)
_CONTESTED_ASSERTION = re.compile(
    r"\b(?:fraud|fraudulent|scam|corrupt|lied|cover[- ]?up|reckless|deceptive|illegal|"
    r"unconstitutional|criminally negligent|unsafe|rigged|deliberately hid)\b", re.I)
_ATTRIBUTION = re.compile(
    r"\b(?:according to|said|says|reported|found|concluded|alleges|alleged|claims|claimed|"
    r"argues|argued|described|characterized|called)\b", re.I)
_GENERIC_OPENING_RE = re.compile(
    r"^(?:this|the)\s+(?:trial|episode|story|topic|question)\s+"
    r"(?:has several moving parts|covers many issues|involves many issues|"
    r"looks at several topics|focuses on several issues|is about several topics)\b", re.I)


def word_count(text: str) -> int:
    return len(_WORD_RE.findall(text))


def _canonical(text: str) -> str:
    return " ".join(_WORD_RE.findall(text.casefold()))


_CLOSING_LEAD_IN_RE = re.compile(
    r'^(?:the main takeaway is(?: that)?|the key point is(?: that)?|'
    r'the main point is(?: that)?|so the main point is(?: that)?|'
    r'in short|taken together|overall)[,:]?\s*', re.I)


def _claim_key(text: str, kind: str) -> str:
    """Normalize a closing lead-in before checking repeated spoken claims."""
    value = str(text or '').strip()
    if kind == 'closing':
        value = _CLOSING_LEAD_IN_RE.sub('', value)
    return _canonical(value)


def _sentences(chapter: dict[str, Any]) -> list[str]:
    rows = chapter.get("sentences")
    if isinstance(rows, list):
        return [row if isinstance(row, str) else row.get("text", "")
                for row in rows if isinstance(row, (str, dict))]
    spoken = chapter.get("spoken_text", chapter.get("text", ""))
    if isinstance(spoken, str) and spoken.strip():
        return [part.strip() for part in _SENTENCE_SPLIT_RE.split(spoken.strip())]
    return []


def _sentence_rows(chapter: dict[str, Any]) -> list[tuple[str, str]]:
    rows = chapter.get("sentences")
    if isinstance(rows, list):
        return [(row, "claim") if isinstance(row, str) else
                (row.get("text", ""), row.get("kind", "claim"))
                for row in rows if isinstance(row, (str, dict))]
    return [(text, "claim") for text in _sentences(chapter)]


def _is_number_word(token: str) -> bool:
    pieces = token.casefold().replace("’", "'").split("-")
    return bool(pieces) and all(piece in _NUMBER_WORDS for piece in pieces)


def _number_mentions(text: str) -> int:
    tokens = _WORD_RE.findall(text.casefold())
    count = 0
    in_number = False
    for index, token in enumerate(tokens):
        is_number = _is_number_word(token)
        if is_number:
            if not in_number:
                count += 1
            in_number = True
        elif (token == "and" and in_number and index + 1 < len(tokens)
              and _is_number_word(tokens[index + 1])):
            continue
        else:
            in_number = False
    return count


def _initials_match(words: list[str], acronym: str) -> bool:
    ignored = {"a", "an", "and", "for", "in", "of", "the", "to"}
    return "".join(word[0].upper() for word in words if word.casefold() not in ignored) == acronym


def _has_acronym_expansion_before(text: str, start: int, acronym: str) -> bool:
    prefix = text[:start]
    connector = re.search(r"(?:,\s*|;\s*)?(?:or|also known as|known as|short for)\s+$", prefix, re.I)
    if not connector:
        return False
    words = re.findall(r"\b[A-Za-z][A-Za-z'-]*\b", prefix[:connector.start()])
    for width in range(2, min(10, len(words)) + 1):
        if _initials_match(words[-width:], acronym):
            return True
    return False


def _unexpanded_acronyms(text: str) -> list[str]:
    result, seen = [], set()
    for match in _ACRONYM_RE.finditer(text):
        acronym = re.sub(r"[^A-Z]", "", match.group(0))
        if len(acronym) < 2 or acronym in seen:
            continue
        seen.add(acronym)
        if not _has_acronym_expansion_before(text, match.start(), acronym):
            result.append(acronym)
    return result


def _has_opening_signpost(text: str, requested_minutes: int | None) -> bool:
    folded = text.casefold().replace("’", "'")
    if not re.search(r"\b(?:briefing|episode)\b", folded) or not re.search(r"\b(?:on|about)\b", folded):
        return False
    minute_words = {3: "three", 5: "five", 10: "ten", 15: "fifteen"}
    length_word = minute_words.get(requested_minutes)
    duration = (re.search(r"\b(?:three|five|ten|fifteen)[ -]minute\b", folded)
                if length_word is None else re.search(rf"\b{length_word}[ -]minute\b", folded))
    return bool(duration and re.match(r"^(?:here is|here's|this is)\b", folded.strip()))


_SOURCE_ABSENCE_RE = re.compile(
    r"\b(?:the\s+)?(?:source|report|article|evidence)\b.{0,45}\b(?:doesn't|does not|didn't|did not|never)\s+"
    r"(?:say|state|mention|explain|specify|confirm|tell us)\b|"
    r"\b(?:we|you)\s+(?:can't|cannot|couldn't|could not)\s+(?:tell|say|know)\b", re.I)
_VAGUE_ENDING_RE = re.compile(
    r"\b(?:time will tell|we(?:'|’)ll have to wait and see|it remains to be seen|"
    r"we will have to see|only time will tell|progress will be important|"
    r"the situation will continue to evolve|watch this space|more will become clear)\b", re.I)
_HOST_TEMPLATE_PATTERNS = {
    "context lead-in": re.compile(r"\bfor context\b", re.I),
    "so-what question": re.compile(r"\bso what (?:does|do) that mean\b", re.I),
    "takeaway lead-in": re.compile(r"\b(?:the|a) main takeaway is\b", re.I),
}


def _valid_transition(text: str) -> bool:
    return bool(re.fullmatch(
        r"(?:Next,\s+[A-Za-z][A-Za-z' -]{0,42}|Turning to\s+[A-Za-z][A-Za-z' -]{0,42}|"
        r"Let(?:'|’)s turn to\s+[A-Za-z][A-Za-z' -]{0,42}|Now, let(?:'|’)s look at\s+[A-Za-z][A-Za-z' -]{0,42}|"
        r"From there, we can consider\s+[A-Za-z][A-Za-z' -]{0,42}|"
        r"The next question concerns\s+[A-Za-z][A-Za-z' -]{0,42})\."
        , text.strip(), re.I))


def _read_takeaways(episode: dict[str, Any]) -> list[str]:
    result: list[str] = []
    for key in ("read_takeaway", "read"):
        value = episode.get(key)
        if isinstance(value, str) and value.strip():
            result.append(value.strip())
    for chapter in episode.get("chapters", []):
        if isinstance(chapter, dict):
            value = chapter.get("read_takeaway", chapter.get("read"))
            if isinstance(value, str) and value.strip():
                result.append(value.strip())
    return result


def _check(name: str, passed: bool, detail: str = "") -> dict[str, Any]:
    row = {"name": name, "passed": bool(passed)}
    if detail:
        row["detail"] = detail
    return row


def _sensitive_framing(episode: dict[str, Any], plan: list[dict[str, Any]],
                       sentence_rows: list[tuple[int, str, dict[str, Any]]]) -> tuple[bool, str]:
    topic = episode.get("topic_label", episode.get("topic", ""))
    if isinstance(topic, dict):
        topic = " ".join(str(topic.get(key) or "") for key in ("label", "scope", "name"))
    context = " ".join([str(topic), *(str(row.get("question") or row.get("sub_question") or "")
                                        for row in plan),
                        *(str(chapter.get("title") or "") for chapter in episode.get("chapters", [])
                          if isinstance(chapter, dict))])
    if not _SENSITIVE_TOPIC.search(context):
        return True, ""
    issues = []
    for _index, sentence, _chapter in sentence_rows:
        sensational = _SENSATIONAL.search(sentence)
        if sensational:
            issues.append(f"sensational wording: {sensational.group(0).casefold()}")
        contested = _CONTESTED_ASSERTION.search(sentence)
        if contested and not _ATTRIBUTION.search(sentence):
            issues.append(f"unattributed contested claim: {contested.group(0).casefold()}")
    return not issues, "; ".join(dict.fromkeys(issues))


def score_episode_script(episode: dict[str, Any]) -> dict[str, Any]:
    """Return deterministic checks, score, chapter timing and honest shortfall.

    Short scripts are reported as short rather than padded or failed: the
    episode generator's writing contract explicitly allows thin evidence.
    Scripts longer than the requested allowance plus 10 percent fail.
    """
    checks: list[dict[str, Any]] = []
    errors: list[str] = []
    if not isinstance(episode, dict):
        return {"passed": False, "score": 0, "checks": [],
                "errors": ["episode must be an object"], "warnings": [],
                "word_count": 0, "estimated_minutes": 0,
                "requested_minutes": None, "chapters": []}

    chapters = episode.get("chapters")
    if not isinstance(chapters, list) or not chapters:
        chapters = []
        errors.append("episode needs at least one chapter")
    requested = episode.get("requested_minutes")
    try:
        requested = float(requested) if requested is not None else None
        if requested is not None and (requested <= 0 or requested != requested):
            requested = None
    except (TypeError, ValueError):
        requested = None
    extracted: list[tuple[int, str, str]] = []
    chapter_rows: list[list[tuple[str, str]]] = []
    chapter_reports = []
    for index, chapter in enumerate(chapters, 1):
        if not isinstance(chapter, dict):
            errors.append(f"chapter {index} must be an object")
            continue
        rows = _sentence_rows(chapter)
        chapter_rows.append(rows)
        if not rows or any(not isinstance(text, str) or not text.strip() for text, _kind in rows):
            errors.append(f"chapter {index} needs non-empty spoken sentences")
        words = sum(word_count(text) for text, _kind in rows if isinstance(text, str))
        extracted.extend((index, text.strip(), kind) for text, kind in rows
                         if isinstance(text, str) and text.strip())
        chapter_reports.append({
            "chapter": index,
            "title": str(chapter.get("title", "")),
            "word_count": words,
            "estimated_minutes": round(words / WORDS_PER_MINUTE, 2),
        })

    sentence_units = [(unit.strip(), kind) for _chapter, text, kind in extracted
                      for unit in _SENTENCE_SPLIT_RE.split(text) if unit.strip()]
    sentence_lengths = [word_count(text) for text, _kind in sentence_units]
    length_ok = bool(sentence_lengths) and all(
        (count >= MIN_SENTENCE_WORDS or kind == "navigation") and count <= MAX_SENTENCE_WORDS
        for count, (_text, kind) in zip(sentence_lengths, sentence_units)
    )
    too_short = sum(count < MIN_SENTENCE_WORDS and sentence_units[index][1] != "navigation"
                    for index, count in enumerate(sentence_lengths))
    too_long = sum(count > MAX_SENTENCE_WORDS for count in sentence_lengths)
    average_length = sum(sentence_lengths) / len(sentence_lengths) if sentence_lengths else 0
    maximum_length = max(sentence_lengths, default=0)
    checks.append(_check("sentence_length", length_ok,
                         f"{too_short} below {MIN_SENTENCE_WORDS}; {too_long} above {MAX_SENTENCE_WORDS} words"))
    checks.append(_check("average_sentence_length", bool(sentence_lengths) and average_length <= MAX_AVERAGE_SENTENCE_WORDS,
                         f"{average_length:.1f} average words; maximum {MAX_AVERAGE_SENTENCE_WORDS}"))
    checks.append(_check("max_sentence_length", bool(sentence_lengths) and maximum_length <= MAX_SENTENCE_WORDS,
                         f"{maximum_length} words; maximum {MAX_SENTENCE_WORDS}"))

    spoken_text = "\n".join(text for _, text, _kind in extracted)
    literal_digits = len(_DIGIT_RE.findall(spoken_text))
    digit_density = literal_digits * 100 / max(1, sum(sentence_lengths))
    checks.append(_check("digit_density", literal_digits == 0,
                         f"{digit_density:.2f} numeral digits per 100 spoken words; use spoken number words"))
    spoken_by_chapter = [[text for text, kind in rows if kind not in {"opening", "navigation"}]
                         for rows in chapter_rows]
    number_counts = [_number_mentions(" ".join(rows)) for rows in spoken_by_chapter]
    dense_chapters = [index for index, count in enumerate(number_counts, 1)
                      if count > MAX_NUMBER_MENTIONS_PER_CHAPTER]
    checks.append(_check("number_density", not dense_chapters,
                         ", ".join(f"chapter {index}: {number_counts[index - 1]}" for index in dense_chapters)
                         or f"at most {MAX_NUMBER_MENTIONS_PER_CHAPTER} number mentions per chapter"))

    has_digits_or_symbols = bool(_NUMBER_SYMBOL_RE.search(spoken_text))
    checks.append(_check("spoken_numbers", not has_digits_or_symbols,
                         "spell numbers in spoken words; do not read numeric or currency symbols" if has_digits_or_symbols else ""))

    has_url = bool(_URL_RE.search(spoken_text))
    checks.append(_check("no_urls", not has_url, "spoken text contains a URL" if has_url else ""))

    has_citation_marker = bool(_CITATION_MARKER_RE.search(spoken_text))
    checks.append(_check("no_citation_markers", not has_citation_marker,
                         "spoken text contains a numeric citation marker" if has_citation_marker else ""))
    references = list(dict.fromkeys(match.group(0) for match in _READER_REFERENCE_RE.finditer(spoken_text)))
    checks.append(_check("no_reader_references", not references,
                         ", ".join(references) if references else ""))
    has_heading = any(_SPOKEN_HEADING_RE.search(text) for _chapter, text, _kind in extracted)
    checks.append(_check("no_spoken_headings", not has_heading,
                         "spoken text contains a heading or bullet label" if has_heading else ""))
    has_parentheses = bool(_PARENTHETICAL_RE.search(spoken_text))
    checks.append(_check("no_parentheses", not has_parentheses,
                         "spoken text contains parenthetical material" if has_parentheses else ""))
    unexpanded = _unexpanded_acronyms(spoken_text)
    checks.append(_check("acronym_expansion", not unexpanded,
                         ", ".join(unexpanded) if unexpanded else ""))

    first_row = next(((text, kind) for rows in chapter_rows for text, kind in rows
                      if isinstance(text, str) and text.strip()), ("", ""))
    opening_ok = (first_row[1] == "opening" and
                  _has_opening_signpost(first_row[0], int(requested) if isinstance(requested, (int, float)) else None))
    checks.append(_check("opening_signpost", opening_ok,
                         "Start with the requested topic and spoken length" if not opening_ok else ""))
    transitions = [rows[0][0].strip() for rows in chapter_rows[1:]
                   if rows and rows[0][1] == "navigation" and isinstance(rows[0][0], str)]
    transitions_ok = bool(chapter_rows) and len(transitions) == max(0, len(chapter_rows) - 1) and all(
        bool(rows) and rows[0][1] == "navigation" and isinstance(rows[0][0], str)
        and _valid_transition(rows[0][0])
        for rows in chapter_rows[1:]
    )
    checks.append(_check("story_transitions", transitions_ok,
                         "Start each later chapter with a short spoken transition" if not transitions_ok else ""))
    transition_families = [re.match(
        r"^(Next|Turning to|Let(?:'|’)s turn to|Now, let(?:'|’)s look at|From there|The next question)", text, re.I
    ).group(1).casefold() for text in transitions if re.match(
        r"^(Next|Turning to|Let(?:'|’)s turn to|Now, let(?:'|’)s look at|From there|The next question)", text, re.I)]
    formula_counts = {name: len(pattern.findall(spoken_text)) for name, pattern in _HOST_TEMPLATE_PATTERNS.items()}
    repeated_formulas = sorted(name for name, count in formula_counts.items() if count > 1)
    repeated_formulas.extend(f"{family} transition" for family in sorted(set(transition_families))
                             if transition_families.count(family) > 1)
    checks.append(_check("host_formula_variety", not repeated_formulas,
                         ", ".join(repeated_formulas) if repeated_formulas else ""))
    final_row = next(((text, kind) for rows in reversed(chapter_rows) for text, kind in reversed(rows)
                      if isinstance(text, str) and text.strip()), ("", ""))
    closing_ok = final_row[1] == "closing" and word_count(final_row[0]) >= 5
    checks.append(_check("closing_recap", closing_ok,
                         "End with a complete spoken closing that carries the main takeaway" if not closing_ok else ""))
    vague_ending = bool(_VAGUE_ENDING_RE.search(final_row[0]))
    checks.append(_check("concrete_ending", closing_ok and not vague_ending,
                         "End on a concrete supported detail or specific development to watch" if vague_ending or not closing_ok else ""))

    segment_endings = [next(((text, kind) for text, kind in reversed(rows)
                             if isinstance(text, str) and text.strip()), ("", ""))
                       for rows in chapter_rows]
    vague_segments = [text for text, _kind in segment_endings if _VAGUE_ENDING_RE.search(text)]
    checks.append(_check("concrete_segment_endings", not vague_segments,
                         "A segment ends with a vague promise or filler" if vague_segments else ""))

    folded = spoken_text.casefold().replace("’", "'")
    banned = [phrase for phrase in BANNED_PHRASES if phrase.replace("’", "'") in folded]
    checks.append(_check("no_banned_filler", not banned,
                         ", ".join(banned) if banned else ""))
    source_absence = bool(_SOURCE_ABSENCE_RE.search(spoken_text))
    checks.append(_check("no_source_absence_filler", not source_absence,
                         "Explain the supported facts; do not narrate what a source did not say" if source_absence else ""))

    factual_sentences = [_claim_key(text, kind) for _, text, kind in extracted
                         if kind not in {"opening", "navigation"} and _canonical(text)]
    repeated_claims = len(factual_sentences) != len(set(factual_sentences))
    checks.append(_check("no_repeated_claims", not repeated_claims,
                         "A spoken sentence repeats an earlier claim" if repeated_claims else ""))

    body_sentences = {_canonical(text) for _, text, _kind in extracted if _canonical(text)}
    repeated_read = [text for text in _read_takeaways(episode)
                     if _canonical(text) in body_sentences]
    checks.append(_check("read_takeaway_is_fresh", not repeated_read,
                         "Read takeaway repeats a body sentence" if repeated_read else ""))

    total_words = sum(sentence_lengths)
    estimated_minutes = round(total_words / WORDS_PER_MINUTE, 2)
    overlong = requested is not None and total_words > requested * WORDS_PER_MINUTE * 1.10
    checks.append(_check("requested_duration", not overlong,
                         "script exceeds requested duration by more than 10%" if overlong else ""))
    warnings = []
    if requested is not None and total_words < requested * WORDS_PER_MINUTE * 0.90:
        warnings.append("shorter_than_requested; preserve the evidence-supported script without padding")
    if requested is None:
        warnings.append("requested duration is unavailable; only chapter estimates are reported")

    score = sum(check["passed"] for check in checks)
    return {
        "passed": not errors and score == len(checks),
        "score": score,
        "out_of": len(checks),
        "checks": checks,
        "errors": errors,
        "warnings": warnings,
        "word_count": total_words,
        "estimated_minutes": estimated_minutes,
        "requested_minutes": requested,
        "words_per_minute": WORDS_PER_MINUTE,
        "chapters": chapter_reports,
    }


WEEKLY_RECAP_TARGET_WORDS = 1350
WEEKLY_RECAP_MIN_WORDS = round(WEEKLY_RECAP_TARGET_WORDS * .9)
WEEKLY_RECAP_MAX_WORDS = 10 * WORDS_PER_MINUTE
_WEEKLY_SCOPE_RE = re.compile(r"\b(?:weekly|this week's|this week’s|week of|week beginning)\b", re.I)
_WEEKLY_CURRENT_CLAIM_RE = re.compile(r"\b(?:this week|this week's|this week’s|current week)\b", re.I)
_WEEKLY_CLOSING_STOP = _QUALITY_STOP | {
    "across", "example", "examples", "together", "shared", "common", "overall",
    "week", "weekly", "recap", "people", "help", "helps", "when", "where",
}


def _weekly_closing_is_grounded(closing: str, earlier_text: str) -> bool:
    closing_terms = {word.casefold() for word in _WORD_RE.findall(closing)
                     if word.casefold() not in _WEEKLY_CLOSING_STOP}
    earlier_terms = {word.casefold() for word in _WORD_RE.findall(earlier_text)
                     if word.casefold() not in _WEEKLY_CLOSING_STOP}
    if not closing_terms:
        return False
    shared = closing_terms & earlier_terms
    return len(shared) >= 3 and len(shared) / len(closing_terms) >= .3


def _weekly_source_dates_are_truthful(edition: dict[str, Any], sections: list[dict[str, Any]]) -> bool:
    dated_sections = [section for section in sections if section.get("source_published_at")]
    if not dated_sections:
        return True
    try:
        week_start = date.fromisoformat(str(edition.get("week_start") or "")[:10])
    except ValueError:
        return False
    from ..audio_script import _ordinal_words
    for section in dated_sections:
        try:
            published = date.fromisoformat(str(section["source_published_at"]).strip()[:10])
        except ValueError:
            return False
        if published >= week_start:
            continue
        spoken = section.get("spoken_text", section.get("body", ""))
        if isinstance(spoken, list):
            spoken = " ".join(str(row.get("text") or "") if isinstance(row, dict) else str(row)
                              for row in spoken)
        expected_date = f"{published.strftime('%B')} {_ordinal_words(published.day)}"
        if (_WEEKLY_CURRENT_CLAIM_RE.search(str(spoken)) or
                expected_date.casefold() not in str(spoken).casefold()):
            return False
    return True


def score_weekly_recap_listening(edition: dict[str, Any]) -> dict[str, Any]:
    """Check the spoken prose and duration of every weekly recap section.

    Weekly editions are stored as sections rather than episode chapters. Their
    on-screen prose is also the current playback text, so check each section as
    one spoken segment without assuming an episode's opening/navigation schema.
    """
    sections = edition.get("sections", edition.get("chapters")) if isinstance(edition, dict) else None
    sections = [row for row in sections if isinstance(row, dict)] if isinstance(sections, list) else []
    chapters = []
    for index, section in enumerate(sections):
        spoken = section.get("spoken_text", section.get("body"))
        if isinstance(spoken, list):
            sentences = [str(row.get("text") or "") if isinstance(row, dict) else str(row)
                         for row in spoken]
        elif isinstance(spoken, str):
            sentences = [part.strip() for part in _SENTENCE_SPLIT_RE.split(spoken.strip()) if part.strip()]
        else:
            sentences = []
        rows = []
        for sentence_index, sentence in enumerate(sentences):
            kind = "claim"
            if index == 0 and sentence_index == 0:
                kind = "opening"
            elif index > 0 and sentence_index == 0:
                kind = "navigation"
            elif index == len(sections) - 1 and sentence_index == len(sentences) - 1:
                kind = "closing"
            rows.append({"text": sentence, "kind": kind})
        chapters.append({"title": section.get("heading", section.get("title", "")),
                         "sentences": rows})
    report = score_episode_script({"requested_minutes": 10, "chapters": chapters})
    words = report["word_count"]
    opening = chapters[0]["sentences"][0]["text"] if chapters and chapters[0]["sentences"] else ""
    scope_ok = bool(_WEEKLY_SCOPE_RE.search(opening))
    checks = [*report["checks"], _check("weekly_scope", scope_ok,
                                       "Opening must name the recap's weekly scope" if not scope_ok else "")]
    all_sentences = [row["text"] for chapter in chapters for row in chapter["sentences"]]
    closing = all_sentences[-1] if all_sentences else ""
    earlier = " ".join(all_sentences[:-1])
    closing_grounded = (_canonical(closing) not in {_canonical(text) for text in all_sentences[:-1]}
                        and _weekly_closing_is_grounded(closing, earlier))
    checks.append(_check("weekly_closing_grounded", closing_grounded,
                         "Closing must recap covered material without repeating a claim or introducing a new development"
                         if not closing_grounded else ""))
    source_dates_ok = _weekly_source_dates_are_truthful(edition, sections)
    checks.append(_check("weekly_source_date_truth", source_dates_ok,
                         "Earlier-week sources must be spoken with their publication date, not described as current-week news"
                         if not source_dates_ok else ""))
    limit_note = edition.get("evidence_limit_note") if isinstance(edition, dict) else None
    evidence_limited = (isinstance(edition, dict) and edition.get("evidence_limited") is True
                        and isinstance(limit_note, str) and bool(limit_note.strip()))
    duration_ok = words <= WEEKLY_RECAP_MAX_WORDS and (words >= WEEKLY_RECAP_MIN_WORDS or evidence_limited)
    duration_detail = (f"{words} spoken words; target {WEEKLY_RECAP_TARGET_WORDS} "
                       f"(accepted {WEEKLY_RECAP_MIN_WORDS}–{WEEKLY_RECAP_MAX_WORDS})")
    if evidence_limited and words < WEEKLY_RECAP_MIN_WORDS:
        duration_detail += "; evidence limitation allows a shorter recap without padding"
    checks.append(_check("weekly_duration", duration_ok, duration_detail))
    score = sum(row["passed"] for row in checks)
    warnings = list(report["warnings"])
    if evidence_limited and words < WEEKLY_RECAP_MIN_WORDS:
        warnings.append("evidence_limited_shorter_recap; preserve the supported script without padding")
    return {"passed": bool(chapters) and score == len(checks), "score": score,
            "out_of": len(checks), "checks": checks, "word_count": words,
            "estimated_minutes": report["estimated_minutes"],
            "target_words": WEEKLY_RECAP_TARGET_WORDS, "warnings": warnings}


def episode_honest_uncertainty_check(
    episode: dict[str, Any], evidence: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    """Check certainty against cited facts and unresolved answers against spoken disclosure."""
    chapters = episode.get("chapters") if isinstance(episode, dict) else None
    chapters = [row for row in chapters if isinstance(row, dict)] if isinstance(chapters, list) else []
    facts = evidence if isinstance(evidence, dict) else {}
    sentence_rows = [(text, chapter) for chapter in chapters
                     for text, _kind in _sentence_rows(chapter)]
    certainty_ok = True
    for text, chapter in sentence_rows:
        row = next((entry for entry in chapter.get("sentences", [])
                    if isinstance(entry, dict) and entry.get("text") == text), {})
        ids = row.get("evidence_ids") if isinstance(row.get("evidence_ids"), list) else []
        cited = " ".join(str(facts.get(eid, {}).get("text") or "") for eid in ids)
        # Ordinary "will" remains allowed; only the existing certainty
        # categories are compared, and semantic verification remains separate.
        claim_strengths = {match.lastgroup for match in _QUALITY_ABSOLUTES.finditer(text)}
        cited_strengths = {match.lastgroup for match in _QUALITY_ABSOLUTES.finditer(cited)}
        if claim_strengths - cited_strengths:
            certainty_ok = False

    missing = episode.get("could_not_confirm") if isinstance(episode, dict) else []
    missing = [str(value) for value in missing if isinstance(value, str) and value.strip()] if isinstance(missing, list) else []
    transcript_text = " ".join(text for text, _chapter in sentence_rows).casefold()
    gap_acknowledged = (not missing or (
        all(question.casefold() in transcript_text for question in missing) and
        ("couldn't confirm" in transcript_text or "could not confirm" in transcript_text or
         "not established" in transcript_text)))
    passed = certainty_ok and gap_acknowledged
    return {"name": "honest_uncertainty", "passed": passed,
            "detail": "Keep strong certainty tied to evidence and acknowledge unresolved questions."}


def refresh_episode_quality_check(report: dict[str, Any], replacement: dict[str, Any]) -> bool:
    """Replace one post-assembly check while retaining the rubric's canonical bar."""
    if (not isinstance(report, dict) or report.get("unavailable_reason") or
            not isinstance(report.get("checks"), list) or not isinstance(replacement, dict)):
        return False
    for row in report["checks"]:
        if isinstance(row, dict) and row.get("name") == replacement.get("name"):
            row.update(replacement)
            failed = [check["name"] for check in report["checks"]
                      if isinstance(check, dict) and not check.get("passed")]
            score = sum(bool(check.get("passed")) for check in report["checks"]
                        if isinstance(check, dict))
            bar = report.get("bar", 6)
            passed = score >= bar and not (_EPISODE_QUALITY_MANDATORY & set(failed))
            report.update(score=score, out_of=len(report["checks"]), issues=failed,
                          passed=passed, partial=not passed)
            return True
    return False


def score_episode_quality(episode: dict[str, Any], research_plan: list[dict[str, Any]],
                          evidence: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Score listening quality after factual verification; deterministic and offline."""
    chapters = episode.get("chapters") if isinstance(episode, dict) else None
    chapters = [row for row in chapters if isinstance(row, dict)] if isinstance(chapters, list) else []
    facts = evidence if isinstance(evidence, dict) else {}
    plan = [row for row in research_plan if isinstance(row, dict)] if isinstance(research_plan, list) else []
    sentence_rows = []
    for index, chapter in enumerate(chapters):
        for sentence, _kind in _sentence_rows(chapter):
            sentence_rows.append((index, sentence, chapter))
    requested_minutes = episode.get("requested_minutes") if isinstance(episode, dict) else None
    if type(requested_minutes) is not int or requested_minutes not in {3, 5, 10, 15}:
        requested_minutes = None
    first = next((text.strip() for _index, text, chapter in sentence_rows if text.strip()
                  and next((row.get("kind", "claim") for row in chapter.get("sentences", [])
                            if isinstance(row, dict) and row.get("text") == text), "claim")
                  not in {"opening", "navigation"}), "")
    opening_ok = (bool(first) and 6 <= word_count(first) <= 25
                  and not re.match(r"^(?:today|in this (?:episode|report)|let(?:'|’)s (?:begin|start)|welcome)\b", first, re.I)
                  and not _GENERIC_OPENING_RE.search(first))

    chapter_answers = []
    remaining_questions = [str(row.get("question") or row.get("sub_question") or "") for row in plan]
    for chapter in chapters:
        chapter_text = " ".join([str(chapter.get("title") or ""), *_sentences(chapter)])
        chapter_terms = {word.casefold() for word in _WORD_RE.findall(chapter_text)
                         if len(word) > 2 and word.casefold() not in _QUALITY_STOP}
        question_matches = []
        for index, question in enumerate(remaining_questions):
            question_terms = {word.casefold() for word in _WORD_RE.findall(question)
                              if len(word) > 2 and word.casefold() not in _QUALITY_STOP}
            question_matches.append((len(question_terms & chapter_terms), index))
        overlap, matched = max(question_matches, default=(0, -1))
        matched_terms = ({word.casefold() for word in _WORD_RE.findall(remaining_questions[matched])
                          if len(word) > 2 and word.casefold() not in _QUALITY_STOP}
                         if matched >= 0 else set())
        relevant_answer = overlap >= 2 and overlap / max(1, len(matched_terms)) >= .35
        if relevant_answer and matched >= 0:
            remaining_questions.pop(matched)
        has_claim = any(isinstance(row, dict) and row.get("kind") == "claim"
                        and isinstance(row.get("evidence_ids"), list) and row["evidence_ids"]
                        for row in chapter.get("sentences", []))
        chapter_answers.append(relevant_answer and has_claim)
    answers_ok = bool(chapters) and len(chapter_answers) == len(chapters) and all(chapter_answers)

    number_rows = []
    repetition_rows = []
    by_chapter: dict[int, list[set[str]]] = {}
    for index, text, chapter in sentence_rows:
        row = next((entry for entry in chapter.get("sentences", [])
                    if isinstance(entry, dict) and entry.get("text") == text), {})
        ids = row.get("evidence_ids") if isinstance(row.get("evidence_ids"), list) else []
        cited = " ".join(str(facts.get(eid, {}).get("text") or "") for eid in ids)
        nonfactual_signpost = row.get("kind") in {"opening", "navigation"}
        numeric_terms = [] if nonfactual_signpost else [value.casefold().replace(" ", "") for value in _DIGITS.findall(text)]
        spoken_number_terms = (set() if nonfactual_signpost else
                               {value.casefold() for value in _WORD_RE.findall(text)
                                if value.casefold() in _NUMBER_WORDS})
        cited_numbers = cited.casefold().replace(" ", "")
        cited_spoken_numbers = {value.casefold() for value in _WORD_RE.findall(cited)
                                if value.casefold() in _NUMBER_WORDS}
        unsupported_numbers = [value for value in numeric_terms if value not in cited_numbers]
        unsupported_numbers.extend(sorted(spoken_number_terms - cited_spoken_numbers))
        if unsupported_numbers:
            number_rows.extend(unsupported_numbers)
        tokens = {word.casefold() for word in _WORD_RE.findall(text) if word.casefold() not in _QUALITY_STOP}
        by_chapter.setdefault(index, []).append(tokens)
    numbers_ok = not number_rows

    prior_sentences: list[set[str]] = []
    for chapter_index in sorted(by_chapter):
        current = by_chapter[chapter_index]
        for tokens in current:
            for previous in prior_sentences:
                if len(tokens) >= 6 and len(previous) >= 6 and len(tokens & previous) / len(tokens | previous) >= .72:
                    repetition_rows.append(True)
        prior_sentences.extend(current)
    repetition_ok = not repetition_rows

    sentence_lengths = [word_count(text) for _index, text, _chapter in sentence_rows]
    spoken_ok = bool(sentence_lengths) and all(count <= 25 for count in sentence_lengths)
    list_text = " ".join(text for _index, text, _chapter in sentence_rows)
    enumeration = len(_QUALITY_ENUMERATION.findall(list_text)) >= 3
    long_lists = any(text.count(",") >= 3 for _index, text, _chapter in sentence_rows)
    list_ok = not enumeration and not long_lists

    honest_uncertainty = episode_honest_uncertainty_check(episode, facts)
    framing_ok, framing_detail = _sensitive_framing(episode, plan, sentence_rows)

    checks = [
        {"name": "opening_hook", "passed": opening_ok,
         "detail": "Open with the direct answer as one specific, concise sentence rather than generic setup."},
        {"name": "chapter_answers_question", "passed": answers_ok,
         "detail": "Each chapter must connect to its assigned sub-question and contain an evidence-linked claim."},
        {"name": "numbers_sourced", "passed": numbers_ok,
         "detail": "Every numeric expression must appear in evidence cited by that sentence."},
        {"name": "no_cross_chapter_repetition", "passed": repetition_ok,
         "detail": "Avoid repeating a sentence or near-copy in another chapter."},
        {"name": "spoken_style", "passed": spoken_ok,
         "detail": "Keep every spoken sentence at 25 words or fewer."},
        {"name": "no_list_reading", "passed": list_ok,
         "detail": "Explain connected facts in prose; do not read an enumerated or comma-packed list."},
        honest_uncertainty,
        {"name": "sensitive_topic_framing", "passed": framing_ok,
         "detail": (framing_detail or
                    "Use calm language and attribute contested claims in sensitive-topic coverage.")},
    ]
    if requested_minutes is not None:
        spoken_words = sum(word_count(text) for _index, text, _chapter in sentence_rows)
        target_words = requested_minutes * WORDS_PER_MINUTE
        duration_ok = target_words * .90 <= spoken_words <= target_words * 1.10
        checks.append({"name": "requested_duration", "passed": duration_ok,
                       "detail": (f"{spoken_words} spoken words; target is {target_words} "
                                  "with a 90–110% acceptance band.")})
    score = sum(row["passed"] for row in checks)
    failed = [row["name"] for row in checks if not row["passed"]]
    bar = 7 if requested_minutes is not None else 6
    passed = score >= bar and not (_EPISODE_QUALITY_MANDATORY & set(failed))
    return {"score": score, "out_of": len(checks), "bar": bar, "passed": passed,
            "partial": not passed, "checks": checks, "issues": failed}


def check_episode_script(
    episode: dict[str, Any],
    rewrite: Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Score once and optionally ask an injected rewriter for one repair.

    ``rewrite(episode_copy, failed_report)`` must return a full replacement
    episode object. This is an interface only; callers decide whether/how a
    mock or model implements it. No provider is imported or called here.
    """
    candidate = deepcopy(episode)
    report = score_episode_script(candidate)
    repair_attempts = 0
    if not report["passed"] and rewrite is not None:
        repair_attempts = 1
        try:
            repaired = rewrite(deepcopy(candidate), deepcopy(report))
            if isinstance(repaired, dict):
                candidate = repaired
                report = score_episode_script(candidate)
            else:
                report["errors"].append("repair callback did not return an episode object")
                report["passed"] = False
        except Exception as exc:  # return a deterministic safe status, never exception contents
            report["errors"].append(f"repair callback failed: {type(exc).__name__}")
            report["passed"] = False
    return {"episode": candidate, "report": report, "repair_attempts": repair_attempts}
