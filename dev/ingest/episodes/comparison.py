"""Deterministic question expansion for explicit X-versus-Y requests."""
import re


_VERSUS = re.compile(r"^\s*(.+?)\s+(?:vs\.?|versus|compared with|compared to)\s+(.+?)\s*[?.!]*\s*$", re.I)


def comparison_questions(topic):
    """Return one question per side and a comparison question, or None.

    Keep this deliberately narrow: only explicit pair phrasing is expanded.
    The generated questions are passed individually to retrieval, so each side
    gets a source search of its own before the writer compares the evidence.
    """
    if not isinstance(topic, str):
        return None
    match = _VERSUS.fullmatch(topic)
    if not match:
        return None
    left, right = (part.strip(' \t\r\n,;:') for part in match.groups())
    if not left or not right or left.casefold() == right.casefold():
        return None
    return [f"What is known about {left}?",
            f"What is known about {right}?",
            f"How do {left} and {right} compare?"]
