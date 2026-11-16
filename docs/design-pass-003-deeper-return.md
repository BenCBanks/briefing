# Design pass 003 · read deeper, then return

**Status:** Astra/Lumen read-only advice, refined by Morrow, 16 November 2026. Ben's decisions override it. This new interaction stays in Draft PR #1 until Ben previews it.

## Reader flow

In a daily-edition section that names a topic with a reviewed explanation, show one quiet text action, **Read deeper: [topic]**, after the section body and source links. If a section names several topics, show one action for each. Do not add an action for a missing explanation. Opening it must leave the daily reader mounted at the same section and preserve its scroll and reading position. Merely opening an explanation does not switch or pause audio.

The explanation retains its own title, review date, text and source links. When opened from Today, provide a clear **Back to today's briefing** action. That action returns to the same reading place; it does not mark either item finished. When opened from Topics or Library, use the ordinary close/back action and do not imply a Today origin. A person may choose Play on the explanation; that explicit action follows the existing single-player rule and saves the prior track's position.

## Acceptance

- The same fictional edition can open a topic explanation from a section below the first viewport and return to that section after scrolling within the explanation.
- The edition's saved reading section and selected audio track/position remain unchanged when the person only reads the explanation.
- Source links remain available in the explanation; the route does not create a second copy of content or an unreviewed summary.
- The action and return control work with keyboard/VoiceOver and remain legible at large text. A missing explanation leaves no dead action.
- Browser and native simulator evidence are labeled as fictional and do not claim physical-iPhone verification.
