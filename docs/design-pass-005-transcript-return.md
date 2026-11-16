# Design pass 005 · Read transcript returns to the current item

**Status:** Astra/Lumen read-only advice, refined by Morrow, 16 November 2026. Ben's decisions override it. Use the existing reader screens. Changed interactions remain in Draft PR #1 until Ben previews them.

## Outcome and resolution

From any screen, **Read transcript** opens the text belonging to the selected recording, without changing playback or losing the previous reading place. Resolve by stable content ID and type to the current published revision. The selected item is never inferred from the current tab or whichever daily edition is newest.

| Selected recording | Destination |
|---|---|
| Current daily edition | Its daily reader |
| Older daily edition | That edition in the existing edition reader, not Today |
| Weekly recap | That recap in the existing edition reader |
| Topic explanation | That explanation in the existing topic reader |
| Superseded revision | Current corrected revision of the same item |
| Item absent from the current pack | Keep the player open and say “This transcript is unavailable.” |

For valid audio, verify the destination revision and script hash against the selected recording. A mismatch invalidates old audio before showing current text; do not imply that new text is a matching transcript for old audio. Never select the first matching array entry when older revisions are present. Web may add an item-specific route backed by the existing reader component.

## Reading place and return

If the item and revision are already visible, dismiss the player without another navigation or scroll. Otherwise restore that revision's saved reading anchor, or open at the beginning if none exists. Never infer text position from audio seconds or borrow another revision's anchor. A corrected item opens at its correction notice even when an old anchor exists. Navigation itself must not overwrite a saved anchor with a transient top position.

Capture the underlying screen and scroll before navigating. A different reader uses the existing contextual return control: **Back to Library**, **Back to Today**, or **Back to [topic]**. Returning restores scroll and focus without reopening the expanded player. Native sheet dismissal must complete before another sheet appears; web Back should avoid duplicate history entries and preserve any separate deeper-reading return origin.

Ordinary transcript navigation preserves the selected recording, playback or paused state, position and speed. It never seeks, restarts, autoplays or selects a different item. Superseded audio stays stopped and unloaded. A corrected **edition** says **Read corrected edition** and opens its current correction notice. The Stage A topic-explanation model has no correction record; a stale explanation says **Read current explanation** and must not imply that a correction notice exists. Neither path revives old seconds.

## Accessibility and checks

After navigation, focus the restored section heading or article heading; for a correction, focus the correction heading. On return, focus the originating mini-player control if present, otherwise the screen heading. Keep headings semantic, controls at least 44 points on iOS and scrolling respectful of Reduce Motion. Do not announce transcript navigation as an audio state change.

Verify fictional current and older daily, weekly and explanation items: playing and paused from another screen, return scroll/focus, same-reader dismissal, superseded revision with current text, duplicate weekly revisions, missing destination feedback, and large-text/keyboard/VoiceOver behavior. Inspect actual native and web captures before asking Ben to preview.
