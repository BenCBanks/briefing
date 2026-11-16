# Briefing prototype

This repository contains a standalone iOS and browser prototype for a daily briefing across selected topics, optional deeper explanations, and a weekly synthesis. `briefing` is the internal project key; the public product name is still undecided.

The sample editions and source records are fictional or illustrative. Audio remains pending approval. The prototype has no app account, database, paid voice provider, production feed, or public hosting route.

## Browser preview

Run `python3 dev/preview_server.py 127.0.0.1 8898` from the repository root, then open `http://127.0.0.1:8898`. The allowlisted server serves the prototype without exposing repository metadata or planning documents. Browser preferences and reading progress stay in that browser.

## iOS

The SwiftUI project is in `mobile/ios/`; see its README for XcodeGen build steps. The GitHub Actions workflow compiles and tests the app on an iPhone simulator and captures clearly labeled test-only states. An approved professional recording, physical iPhone checks, Apple signing, and an owner preview are required before TestFlight distribution.

## Content and narration

`content/` holds the fictional sample pack. Run `python3 dev/check_content.py` to validate its sources, revisions, script hashes, and any recorded asset checksums.

`python3 dev/export_narration.py --item daily-sample-001 --max-chars 10000 --out-dir /tmp/briefing-narration-daily` exports exact section-aligned text and a revision manifest. The provider-independent recording preparation and approval proposal tools are described in `docs/task-004-one-daily-recording.md`; neither generates speech or changes live content.

Development requirements and design notes are in `docs/`. New screens and interactions remain in a Draft pull request until the owner previews them.
