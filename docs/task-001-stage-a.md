# Task 001: first complete browser prototype

**Lead:** Morrow. **Preview gate:** Ben sees the new Today, Topics, deep-dive and weekly screens before any merge to `main`. **Source:** standalone public repository; unfinished screens remain in a Draft pull request.

## User-visible result

Ben opens one illustrative daily briefing across his selected topics, reads or plays the exact displayed script, sees one coverage state per topic, opens a current explanation for any topic and reads a weekly synthesis. He can adjust a 10/12/15-minute preference, save an item and resume in the same browser. Every screen says the content is illustrative and browser state is local.

## Design contract

- Editorial publication, not a dashboard. Warm paper background, dark ink, restrained green, no decorative hero or card grid.
- Phone: one reading column; Today/Topics/Library navigation and a compact fixed player must leave enough visible text. Desktop: centered reading column, lightweight side rail only if useful.
- The daily edition is one continuous story with a clear beginning and finish. Coverage is a compact list near the top, not five independent large cards.
- A topic row shows scope, current explanation and review date. A deep dive has question, mechanism, change/uncertainty, practical implication, sources and return link.
- Buttons are at least 44px high; focus styles are visible; dark mode and 320px reflow work. Source links open safely.

## Content and module contracts

- Reed owns `src/edition/edition.js`, `src/edition/edition.css`, `content/editions.json`. Export `renderEdition(container, edition, topics, actions)`. `actions` has `play(item)`, `openTopic(topicId, fromSectionId)`, `openSource(sourceId)`, `save(itemId)`, `feedback(itemId, category)`, `isSaved(itemId)`, `readingPlace()`, and `continueReading(sectionId)`. Render daily and weekly with the same function. No edits outside owned files. JSON shape: `{ "editions": [{ "id", "kind", "local_date" or "week_start", "revision_id", "title", "as_of", "duration_minutes", "summary", "sections": [{"id","heading","body","topic_ids","source_ids"}], "coverage": [{"topic_id","state","reason"}], "script_hash", "audio": {"state":"browser-speech","revision_id","script_hash"} }] }`. Use stable IDs `daily-sample-001` and `weekly-sample-001` for now. `body` is plain text. Each selected topic has exactly one coverage row. Avoid invented current news; every development is explicitly hypothetical. The daily and weekly samples each contain about 1,400 words so the 10–15-minute experience is meaningful.
- Mica owns `src/topics/topics.js`, `src/topics/topics.css`, `content/topics.json`, `content/deep-dives.json`, `content/sources.json`. Export `renderTopics(container, topics, deepDives, selectedIds, coverage, actions)` and `renderDeepDive(container, topic, deepDive, sources, actions)`. `actions` has `openDeepDive(topicId)`, `editTopics()`, `play(item)`, `back()`. Topic IDs: `payments`, `banking`, `digital`, `insurance`, `sports`. Each topic has `{id,label,scope,description}`; each deep dive has `{id,topic_id,revision_id,title,reviewed_at,as_of,duration_minutes,summary,sections:[{id,heading,body,source_ids}],script_hash,audio:{state:"browser-speech",revision_id,script_hash}}`. Provide one explanation for every topic, each with a practical question and explicit illustrative or evergreen status. `sources.json` has `{sources:[{id,title,url,kind,note}]}`. Do not claim a source proves a fictional event. No edits outside owned files.
- Morrow owns `index.html`, `styles/base.css`, `src/app.js`, `src/storage.js`, `src/player.js`, content-pack validation, integration and preview. Builders must not edit these files.
- `script_hash` is a content identity token in this prototype. Morrow verifies the displayed sections and browser speech share the same revision. Stage B requires a cryptographic content hash and generated audio asset.

## Acceptance and limits

- One selected-topic coverage record per selected topic, including quiet/limited states; no filler story.
- A deep dive is reachable for every selected topic. Returning preserves the daily section context.
- Read and browser-speech playback use the same text. Only one item plays; changing items stops the first. Missing speech support shows an honest text-only status.
- Preferences, saved IDs, reading place and audio section position persist in localStorage with a `briefing:v1:` prefix and a reset path. Reading and audio progress are separate. No account sync.
- All content is clearly sample/illustrative; no live ingestion, email, paid model, notifications or public launch.
- Focused data checks and browser smoke at 390px and desktop in light/dark. Keep the preview private. No Docker, dependency installation or extra long-lived workers; available RAM was about 1.9GiB with 6.1GiB swap used at start.
