# Narration style

Write for one warm, natural host: the sound of a thoughtful podcast, not a bulletin reader. Keep the host’s presence light. The reporting and its sources supply the substance; the host does not add a personality, joke, memory, banter or opinion that the reporting cannot support.

## The script is the transcript

The approved spoken script is the canonical transcript. Send that exact text for narration and display the same words to readers. Do not let a provider, export step or last-minute edit rewrite, normalize or add words between approval and recording. A pronunciation lexicon can guide how a name is said; it does not replace the written transcript. A material text correction invalidates the recording until the corrected script is approved and recorded.

Keep titles, navigation labels, source URLs, stage directions and production notes outside the spoken script. Do not read URLs aloud. If a link matters, say “the link is in the transcript” and include the link in the source record.

## Sound like a host

- Use contractions where people naturally use them: “here’s,” “doesn’t,” “that’s.”
- Keep most sentences between eight and eighteen words. Put one thought in a sentence; split a sentence before it needs a second breath. The checker flags sentences over twenty-five words.
- Vary the rhythm. Follow a longer explanation with a short sentence. Avoid a run of same-length lines.
- Use clear spoken transitions such as “First up,” “Meanwhile,” “But here’s the catch,” and “One more thing.” Make each transition describe the story’s relationship, not manufacture suspense.
- Repeat the name of a person, organization or idea when “it,” “they,” or “the latter” would make listeners guess.
- Address listeners lightly when useful. Do not pretend to know them, remember a prior conversation, or share a personal reaction.
- Use paragraph breaks for section changes and short pauses. Keep pause instructions out of the canonical script.

## Say numbers, dates and names

Spell out numbers as they should be spoken. Use the amount and precision supported by the source; do not round away a meaningful distinction.

| In a spoken script | Example |
|---|---|
| Money | “one point two billion dollars”; “twenty-four dollars and eighty cents” |
| Percentages | “twelve percent”; “ninety-nine point nine percent” |
| Decimal values | “three point five” |
| Dates | “March fourth”; include a year when it matters, spoken as words |
| Times | “about three hours”; use “three fifteen in the afternoon” when the exact time matters |

Avoid digits, currency signs, percent signs, slashes, mathematical shorthand and unexplained abbreviations in spoken copy. The checker flags common cases; it cannot judge whether a number is accurate or whether rounding is safe.

Write an acronym as the letters the host should say, or add a pronunciation entry in `dev/pronunciations.json`. For example, “A P I” and “F D I C” cue letter names; “NASA” is spoken as a word. Keep mixed-case names such as “FedNow” in their natural spelling. Spell out titles and abbreviations such as “Doctor” instead of “Dr.” when those are the spoken words.

Keep names in their readable spelling. Add a lexicon entry for a name that may be mispronounced. Entries use this provider-neutral shape:

```json
{
  "format_version": 1,
  "entries": [
    {
      "term": "Siobhan Ó Murchú",
      "spoken_form": "shi-VAWN oh mur-KHOO",
      "note": "Name pronunciation"
    }
  ]
}
```

`term` must appear in the script. `spoken_form` is a pronunciation hint, not replacement transcript text. The note is optional. Keep entries short and specific; do not use a lexicon to add words, claims or stage directions.

## Keep claims grounded

Every factual statement, comparison and implication must be supported by the edition’s sources. Transitions may orient the listener, but must not imply urgency, certainty or causation the sources do not establish. Do not invent banter, reactions, anecdotes or quotes. If a claim is uncertain, preserve the uncertainty in the spoken words.

Read table information as a clear sentence. Do not leave a number, abbreviation or visual symbol for the voice to interpret. Do not add a summary that creates a stronger conclusion than the source material.

## Review before recording

1. Run `python3 dev/check_narration.py dev/audition_script.txt` (or pass another script path).
2. Resolve every digit, symbol, long-sentence and unspoken-text warning. Review the words against the cited material; a clean checker run is not a fact check.
3. Read the script aloud. Fix awkward breath points, unclear pronouns and transitions that sound like headlines.
4. Confirm the transcript is exactly the approved script. Record its revision and hash with any later-approved recording.

The audition in `dev/audition_script.txt` is fictional, provider-independent and not approved audio. No provider or account is needed to review the writing.
