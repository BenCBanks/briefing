# Design pass 004 · material correction preview

**Status:** Astra/Lumen read-only advice, refined by Morrow, 16 November 2026. Ben's decisions override it. This is an isolated fictional preview fixture, not live correction delivery or an editorial backend. New states remain in Draft PR #1 until Ben previews them.

## Fixture and data contract

Use two versions of one fictional daily edition, loaded sequentially with the same edition ID and stable section IDs. In `r1`, a made-up checkout pilot has **120 participants**. In `r2`, it has **12 participants**. Recompute `script_hash` from the corrected script and mark `r1` superseded. The corrected edition's audio state is `pending` with no URL or chapters.

The `r2` correction record names its ID, `severity: material`, `previous_revision_id`, `revision_id`, `corrected_at`, affected `section_ids`, `what_changed`, `why_it_matters`, and `source_ids`. Use a bundled, explicitly invented **Fictional pilot record — demonstration source** as the evidence for the count. Source details show publisher (Prototype fixture), title, date, kind, supported section, scope and availability. Do not claim a real publisher supports the invented number. An internal fixture source has no external Open link.

## Reader states

- **Today:** Put a plain correction notice below edition metadata and before the summary. State the corrected count and its meaning, link to the affected section, and say that updated audio is unavailable. Do not show Play/Resume for an unavailable or revision/hash-mismatched recording.
- **Library:** A saved `r1` item remains one saved item when `r2` loads. Mark it **Corrected** and open the current `r2` text and notice. Save identity is the stable item ID; retain the saved revision as metadata. Finished and audio progress remain revision-specific.
- **Source details:** Show the invented source's metadata and concise evidence in an accessible page/sheet. Return to the same reading area without changing audio or saved reading progress.
- **Previously active audio:** Accepting `r2` stops/unloads any `r1` playback and explains why. Test active playback invalidation separately from cold launch. Do not describe a reload-only fixture as live correction delivery.

## Acceptance

1. Save `r1`, then load `r2`: exactly one Library item persists, opens `r2`, and does not carry `r1`'s Finished state.
2. The correction link reaches the named section. Source details return to the reader without losing its place.
3. A mismatched `recorded` asset has no visible Play action and cannot restore old audio. Old seconds never map silently to a new recording.
4. Capture Today, Library and source details in browser and iPhone simulator light/dark and large text. Test keyboard/VoiceOver names and focus where tools permit. Keep any technical WAV outside the ordinary app bundle.

This preview uses fictional content and no external ingestion, private data, paid provider, notification or public account.
