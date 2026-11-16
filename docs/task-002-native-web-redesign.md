# Task 002 · iOS and web redesign after Ben's rejected preview

**Owner:** Morrow. **Review:** Astra/Lumen design passes; Ben's decision prevails. **Status:** Working review branch `redesign-ios-web`; hold merge until Ben previews the revised screens.

## Result

A standalone native iOS prototype and a browser version present the same approved edition, topic explanations and weekly recap with the minimal reader design in `design-pass-001.md`. Both use recorded script-matched audio with familiar, reliable controls. The iOS project has an independent bundle identity and TestFlight build plan. The web app remains accessible through a local browser preview.

## Boundaries and RAM

- Do not edit or depend at runtime on Kinetta, Home Assistant or DappleAndDish. The new iOS app gets its own bundle ID, project files, resources, build workflow and eventual App Store Connect record.
- No Docker, simulator or local speech-model process on the 7.4 GiB host. Source/build configuration can be prepared here; Apple compilation and physical-device checks require macOS/iPhone.
- No paid TTS, Apple upload, external notifications, account setup or public launch without Ben's relevant decision.

## Work slices

1. Native foundation: XcodeGen configuration, SwiftUI app shell, bundled sample content loader, typed content model, Today/Topics/Library navigation and offline states. Ownership `mobile/ios/**` except `mobile/ios/BriefingApp/Audio/**`.
2. Native audio: one `AVPlayer` state controller, progress persistence, interruption/route handling, lock-screen metadata and mini/expanded player views. Ownership `mobile/ios/BriefingApp/Audio/**`.
3. Web redesign: revise `index.html`, CSS and web views to the same visual contract; replace browser speech with `HTMLAudioElement` using script-matched assets.
4. Voice and content: prepare a 45–60-second professional reader sample and complete assets, verify rights and script match. No temporary voice becomes a TestFlight asset by accident.
5. Gates: syntax/data checks, private browser screenshots and interactions, macOS build, Ben screen/voice preview, then physical iPhone and TestFlight preparation checks.

## 16 November checkpoint

- The private browser preview has the minimal reader, readable topic coverage, weekly edition and deeper explanations. It uses an `HTMLAudioElement` only for a script-matched recorded asset. Pending audio is quiet status text, with no false Play action.
- Reed expanded the fictional daily script to about 1,600 spoken words and the weekly script to about 1,470; Mica expanded each evergreen explanation to roughly 500–570 words. Unrelated source references were removed. Script SHA-256 hashes are validated against the displayed text.
- The native SwiftUI source has Today, Topics, Library, weekly navigation, coverage links, local saves, revision-keyed reading/finished state, AVPlayer controls and a name-independent icon. Project version/build metadata, a content test target and a macOS build workflow are prepared.
- Native Settings now saves future topic choices, a 10/12/15-minute target and time zone on this iPhone. Article labels scale with Dynamic Type; source links have larger touch targets. The audio controller now respects a Pause during an outstanding seek.
- Browser checks passed at phone/desktop in light/dark, including coverage, end-of-article clearance, Play/Pause, paused seeking, resume, reload persistence and keyboard focus with a temporary technical audio fixture. The fixture is not shipped.
- [Run 43798997600](https://github.com/BenCBanks/briefing/actions/runs/43798997600) passed the macOS build, ordinary-bundle check, native content tests and real `AVPlayer` chapter tests. It captured actual Today, player, Chapters and Today-to-explanation states. Astra passed the chapter list in light/dark/large text. The explanation's large-text toolbar truncated its title, so its visible return label was shortened to “‹ Today” with the full accessibility name retained. A later capture shows the complete title and readable dark layout; Astra found no remaining visual blocker.
- The fictional material-correction preview keeps the edition and section IDs stable while changing 120 participants to 12. Browser checks pass save continuity, revision-specific Finished state, correction/source navigation, focus return and stale audio rejection after reload. Its light/dark Today, Library and source captures are attached to Draft PR #1. Native active-playback invalidation and legacy-save tests passed in [run 43802454942](https://github.com/BenCBanks/briefing/actions/runs/43802454942), but its correction screenshot step was skipped after a brightness gate sampled white text in a genuinely dark explanation image. The multi-point margin gate fix is unverified: [run 43805189070](https://github.com/BenCBanks/briefing/actions/runs/43805189070) never started because GitHub Actions rejected the job for account billing or spending-limit status. The technical WAV and correction fixtures stay outside the ordinary app bundle.
- The exact script exporter now prepares revision-checked, section-aligned text for a future voice audition without calling a provider.
- There is no approved voice recording, physical-iPhone playback result, signing identity or TestFlight upload. Ben still needs to preview the new screens before merge. A 45–60-second professional voice audition, then script-matched audio, native correction captures and physical-iPhone Pause/Resume, seeking, background and lock-screen, interruption, and restored-position checks remain release gates. The source is in private Draft PR #1; `main` remains unmerged. Task 003's exact-item transcript return is under native builder review; Task 004 prepares the one-edition narration gate.
