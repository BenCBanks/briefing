# Task 003 · Return to the selected recording's text

**Owner:** Morrow. **Design:** `design-pass-005-transcript-return.md` (Astra/Lumen advice). **Status:** Draft work stacked on task 002; no merge until Ben previews the interaction.

**Review checkpoint, 16 November:** Browser exact-item routing, playback continuity, contextual return and focus passed the focused Playwright walkthrough. Native source now retains the originating transcript in a navigation stack when another transcript opens from its child player; Astra found no source-level blocker after the Back focus fix. GitHub Actions rejected the macOS job before it started because of account billing or spending-limit status. This native change still needs Swift compilation, simulator screenshots, repeated return and VoiceOver checks before Ben's native preview.

## User-visible result

**Read transcript** from the player opens the exact current item, including a weekly recap or topic explanation, and returns to the prior screen without disturbing audio or reading progress. Corrected items open the current corrected text; missing items give a useful message. The app remains a minimal reader with no new screen.

## Assignments

- Reed owns browser routing/player handoff in `src/app.js`, `src/player.js`, browser tests and web styles only if needed. Use an isolated worktree, fictional fixtures and keyboard-focus assertions.
- Vale owns native routing/player handoff in `mobile/ios/BriefingApp/BriefingApp.swift`, `ReaderViews.swift`, related native tests and debug-only captures only if needed. Use an isolated worktree. Do not touch audio controller internals unless Morrow approves an exact fix.
- Morrow approved a focused `AudioController.swift` persistence fix on 16 November: persist and compare the selected script hash with ID/revision, and key saved listening seconds by that complete content identity. A changed script under the same revision must not restore stale audio or seconds. Vale backs up the file and adds a regression test; no unrelated playback change is included.
- Earlier Stage A preview positions used ID/revision keys without a script hash. Those old listening and reading positions reset when the new hash-scoped keys take effect; they cannot be safely attributed to the current text. This affects only local prototype state, not a production account migration.
- Morrow reviews actual diffs, tests, screenshots, source/revision safety, Astra advice and Ben's preview before any merge.

## Boundaries

No provider, account, notification, real user data, public hosting or TestFlight upload. Keep iOS and browser work independent of Kinetta, the house and other services.
