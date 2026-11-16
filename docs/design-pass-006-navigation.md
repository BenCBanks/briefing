# Navigation revision · owner feedback, 16 November 2026

Status: Draft for Ben's preview. Ben found the first public reader clunky and confusing across the whole navigation system. This spec records Astra's read-only critique of actual phone/desktop captures and implementation. It is advice for the team; Ben's preview controls the final decision.

## Core model

- Keep three root destinations: **Today**, **Topics**, **Library**. Today holds the current daily edition. Topics is the explanation directory. Library holds saved items, previous daily editions and weekly recaps.
- Every nested reader or source retains the root destination from which it was opened. Today → Explanation → Source keeps Today selected; Topics and Library behave the same way. A direct explanation link defaults to Topics.
- Back always returns to the immediate parent and restores its reading position and keyboard focus. The visible Back action and browser Back use the same history path. Forward and refresh preserve the root selection and parent chain.
- Browsing never changes the selected audio item or its saved playback position.

## Today and edition reader

- The introduction shows date, `Sample · Fictional content`, title, reading time and a short summary. Remove the redundant `Today's briefing` eyebrow and the prominent `Audio pending approval` line. If a real approved recording exists, show its Play action; otherwise show no audio control.
- Replace the coverage modal with an inline `In this briefing` disclosure. Each covered topic row includes its topic name and story headline and jumps to that section in the current edition. Quiet or limited topics show their status and reason without pretending to link to a story. The expanded state is keyboard accessible and never opens an explanation.
- After each story, show `Read explanation: [topic] →` as the main optional next action. Put references in a `Sources (n)` disclosure underneath. Its rows show publisher and title and open the shared internal Source detail.
- Retain source limitations, material corrections, finish/save actions and reading position.

## Explanation and Source

- Explanation has one compact top back action named for its actual parent: `← Today`, `← Topics`, or `← Library`. Its hierarchy is `[Topic] · Explanation`, title, reading time, summary and body. Use the same 18px reading text as editions. Consolidate scope and fictional-example caveats into one quiet paragraph. Remove the prominent audio approval line.
- Every reference from an edition or explanation opens the same internal Source detail. At the top, show `← Briefing` or `← Explanation` according to its immediate parent. Show publisher, title, available publication date and `Why this source`. Link externally only through `Open original ↗`, with `Opens in a new tab` nearby. Do not show implementation labels such as `Kind` or `SOURCE REFERENCE`.
- Source return restores the exact article section and focused source link. Explanation return restores the originating story and focused explanation link.

## Topics, Library and Settings

- Topics has a short directory description and `Manage topics`. Each flat row shows topic name, linked explanation title and reading time. Remove the vague `Edit` label, duplicate `Read explanation` action and repeated review metadata from the list.
- Library has `Saved`, `Daily editions` and `Weekly recaps` sections. Rows show title, date and reading time. The Saved section may be suppressed when empty or show one quiet empty message. Do not include a topic-settings shortcut.
- Settings is available on root pages. Saving settings returns to the actual origin with visible confirmation.

## Layout and behavior

- Phone uses 20px gutters, 28px reader titles, 22px section headings, consistent 18px/1.55 body text, and actions at least 44px high. Keep the text bottom navigation on root and nested pages with the true origin highlighted. Nested pages replace the generic title/Settings row with the contextual back action and short page-type label.
- Desktop retains a centered reading column about 680px wide and the same hierarchy and return control. No sidebar is needed.
- New content appears promptly after a tap; show a clear loading and retry state when content cannot load. Keep the last readable edition visible when a refresh fails.

## Acceptance checks

1. At 390px, Today → explanation → source → Back → Back restores both reading positions and focus, with Today selected throughout. Repeat from Topics and Library.
2. Browser Back/Forward and refresh preserve the correct root tab, parent chain and return position. Direct explanation URLs select Topics.
3. Source inspection is identical from editions and explanations; only `Open original` leaves the app.
4. `In this briefing` links jump to edition sections below the header and never open explanations.
5. At phone and desktop widths, controls do not clip, overflow horizontally or hide behind fixed navigation. Focus remains visible.
6. Inspect light/dark Today, expanded contents, explanation, source, Topics and Library screenshots. A scripted navigation journey, not screenshots alone, checks the history/focus behavior. Native iOS compilation and simulator screenshots are separate gates; a browser pass is not a physical iPhone result.

This change stays in Draft until Ben previews the revised flow.
