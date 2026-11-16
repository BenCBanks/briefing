# Design pass 002 · audio chapters

**Status:** Astra/Lumen read-only design advice, 16 November 2026. Ben's decisions override it. New interaction remains in Draft PR #1 until Ben previews it.

## Expanded player

Keep the large transport controls and mini-player unchanged. When valid chapter metadata exists, replace the expanded player's neutral “Recorded briefing” subtitle with the current chapter heading. Let it wrap naturally. Beneath the speed selector, show a plain **Chapters** row followed by **Read transcript**. Use no card, artwork, marquee or extra mini-player control.

Chapters opens a secondary view within the player navigation container on iOS and within the existing player dialog on web. Audio keeps its current playback state. The view has a **Back to player** control and a **Chapters** title. Each full-width row shows its heading and start time (`m:ss`, or `h:mm:ss` after one hour). Mark the current chapter with a checkmark and an accessible “Current chapter” label. Rows have separators and a minimum 44-point target. Initially bring the current row into view; do not move the list automatically while the person browses.

Selecting a row seeks to its start and returns to the player. Playing continues; paused stays paused. It must not select a different recording or change the reading position. Keep the prior confirmed chapter label until a seek succeeds. On seek failure, retain the prior state and show a concise retry message. Current chapter boundaries are start-inclusive and end-exclusive; the final chapter extends to the recording's end. At completion retain the final label; Replay returns to the first.

For zero or one chapter, omit the Chapters row and use the neutral subtitle “Recorded briefing” or “Recorded recap.” Reject invalid, duplicate or unordered boundaries. Times must come from the actual recording, never estimated from transcript length.

## Accessibility and acceptance

Support Dynamic Type, wrapping labels and visible keyboard focus. Announce a row as “heading, starts at time, current chapter” when relevant. Return focus to Chapters when leaving the list. Do not announce every automatic boundary change over narration.

At accessibility text sizes on iOS, put **Back to player** on its own row above a left-aligned **Chapters** heading. Keep the full control label; the inline navigation bar becomes too cramped at this size.

Use a three-chapter technical fixture to verify boundary changes, paused and playing selection, final-chapter completion, and unchanged reading position. Capture the list in light, dark and large text for Ben's preview. A production recording still needs a matching approved script and measured chapter times.
