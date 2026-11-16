# Task 004 · One complete narrated daily edition

**Owner:** Morrow. **Design advice:** Astra/Lumen, 16 November 2026. **Status:** Preparation; no provider, recording, expense or release approved. Ben chooses the voice after a short audition. Keep the work on the Draft redesign branch until Ben previews the real listening experience.

**Technical checkpoint:** `dev/prepare_recording.py` stages only a current daily PCM WAV review master outside the source checkout. It requires the exporter manifest and ordered chapter JSON, checks the complete audio payload, script identity, timing, checksum and measured levels, then leaves human review pending. Eighteen focused tests pass. A disposable ten-minute tone also passed an end-to-end check with the real fictional daily text and five chapters. This proves the staging contract, not narration quality or final delivery playback.

**Handoff checkpoint:** `dev/propose_recording.py` accepts a private staged master and explicit full-recording approval attestation, then writes a separate, private proposed content pack and asset. `dev/check_content.py` validates recorded asset bytes against SHA-256. The proposal rounds the displayed duration up to an integer for the current Swift content model while keeping the exact measured duration in the private staging report. All 35 focused recording/export/handoff tests pass with fictional fixtures. No actual approval record or recording exists, and the app's current content remains pending.

## Result

The existing fictional daily edition has one composed professional recording that matches its approved text and plays in the native iOS app and private browser reader. Today shows its measured duration, and the same recording follows the listener through the mini-player, expanded player, Chapters, transcript return, background and relaunch. The weekly recap and topic explanations continue to say audio is pending.

## Audition and decision

1. Use the identical text in `narration-sample-script.md` for each candidate. Target 45–60 seconds at about 150–165 spoken words per minute, neutral delivery and natural section pauses. No music, host persona or cloned voice.
2. Candidate routes and pricing/rights questions are kept in an owner-only decision record outside this repository. Before any provider request, record the chosen account, current terms, commercial rights, AI disclosure requirement, usage cap and expected cost. The owner chooses the route and authorizes any account or spend.
3. Give Ben the same written script and a small set of clearly named clips. Compare dollar and percentage readings, “API,” “FDIC,” pacing, abrupt joins, artifacts and listening comfort at 1× and 1.25×. Record Ben's selected voice and revision; do not render the complete edition until then.

## Exact content and asset contract

- Freeze one approved revision of `daily-sample-001`. Run `dev/export_narration.py` into a private empty directory and keep its manifest. The exporter must select the current non-superseded revision if older revisions enter the content pack; duplicate stable IDs must never lead to first-match narration.
- Narrate the exported text verbatim, including section headings. For a provider character cap, split only at section boundaries and keep the manifest order. The final audio must have continuous voice, loudness and pauses across any joins.
- Record the provider/model/voice, settings, rights and disclosure decision, render date, source revision and SHA-256 script hash, final audio SHA-256 checksum, exact measured duration and chapter start times. Store only the approved asset under the app's audio content path; keep audition clips and technical fixtures outside ordinary app bundles.
- Mark audio `recorded` only when its script revision and SHA-256 match displayed text and the asset is present. An edited script immediately makes old audio unavailable, even if the stable ID or revision string did not change. A correction must not restore old listening seconds or imply that stale audio matches current text.

### Technical staging before editorial approval

- The recording validator selects the unique current daily revision, independently rebuilds its script and checks the export manifest's item, revision, full hash, segment order and segment checksums. It rejects ambiguous, withdrawn, tampered or changed text, including a changed script under the same revision string.
- Inspect the actual audio container and codec, decode the complete file, and measure duration, channels, sample rate, file size and final checksum. Reject a corrupt, empty or implausibly short candidate. A WAV may be a private review master; final compressed delivery and iOS/web playback need their own validation. Report loudness and peak measurements for review; these measurements alone do not establish voice quality.
- Chapter IDs must match the exported sections in order. Their starts must begin at zero, increase strictly and fall within the measured recording. A human listening review confirms that each boundary lands on its spoken heading.
- Validation writes a private **technically valid, awaiting listening review** candidate. It does not change the content pack, bundle an asset or expose Play. Activation rechecks the staged checksum and script identity after Ben approves the full listen.

### Approved-asset handoff

- After Ben listens to and approves a complete recording, save a private approval record with an explicit `approved` decision scoped to the full recording, its item ID, revision ID, script SHA-256, final audio SHA-256, reviewed chapter list or digest, reviewer, UTC review date, private decision reference, provider/model/voice, affirmative usage-rights decision and explicit disclosure decision. Approval includes spoken completeness, listening quality and chapter alignment; audition approval or technical validation does not suffice. This record documents a decision made in the authorized conversation; JSON alone cannot prove who approved it. A fictional approval fixture is permitted in disposable tests and must never be presented as Ben's decision.
- A handoff tool rechecks the current daily script, the staged report and bytes, the approval record and ordered chapters. It writes only a **proposed** content pack and asset in a new private directory for review. It must not alter `content/`, the browser preview or the iOS bundle, and technical validation alone cannot produce the proposal.
- A recorded content entry must include the asset SHA-256; content validation reads the actual bytes and rejects missing, changed or mismatched files. Test approved, pending, stale-revision, changed-script and changed-audio fixtures in disposable content directories. Weekly and explanations remain pending.

## Experience checks

1. Today presents measured audio duration beside estimated reading time. Play, Pause, Resume, seek, speed, Chapters and Replay agree across the page, mini-player and expanded player. The other six sample items retain honest pending-audio copy and no false Play action.
2. Start the daily recording, open an explanation, open **Read transcript** for the selected recording, return, pause, relaunch and resume. The selected recording, paused/playing state, speed and seconds remain accurate; reading position stays independent. Completion stops without autoplaying another item.
3. Browser checks use the approved file with no speech fallback. Native simulator checks verify bundled-file presence, absence of technical fixtures, exact revision/hash routing and light/dark/large-text states.
4. On a physical iPhone, check repeated Pause/Resume, paused seek, background and lock-screen controls, interruption, headphone removal, relaunch, VoiceOver, Dynamic Type and dark mode. Record the device and result separately from simulator evidence. This is required before TestFlight dispatch.

## Ben's preview and release boundary

Show the audition clips and script first. After voice selection, show the complete recording in the private browser preview and a short native walkthrough of Today → Play → explanation → transcript → return → Pause/Resume, with one light and one dark native capture. Ben previews the changed experience before merge. Signing, TestFlight upload, production hosting and live source ingestion are separate decisions.
