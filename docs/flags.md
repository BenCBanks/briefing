# Feature flag rollout checklist

These flags stay off by default until their evidence gates below are met. A
single successful run is useful feedback, but it does not establish safe
default behavior. Keep the flag value and the evidence used for a default change
in the pull request that changes the default.

| Flag | Current default | Evidence required before default-on | Rollback |
|---|---|---|---|
| `BRIEFING_EPISODE_HYBRID` | Off | Fixture and golden-harness coverage for explainers, specific/current questions, broad prompts and sensitive topics; real scripts show the classifier sends explainers to general background, leaves specific/current requests on research, labels remembered background separately from checked facts, and does not add unsupported specifics. | Set `BRIEFING_EPISODE_HYBRID=off` or unset it. |
| `BRIEFING_EPISODE_OUTLINE_WRITER` | Off | Outline fixtures and the golden harness pass; a real thin-evidence run passes the pre-audio checks, keeps each paragraph tied to its question/evidence, and gives any not-found items one standalone gap sentence. No fact, citation, safety or topic-coverage regression. | Set `BRIEFING_EPISODE_OUTLINE_WRITER=off` or unset it. |
| `BRIEFING_DAILY_OUTLINE_WRITER` | Off | Fixture and CI coverage passes; compare default and outline-on dailies on the same fictional profile and on more than one real profile. Review word/story counts, evidence and citations, spoken forms, audio readiness, cost and honest gap beats. Turn it on only when the outline path is at least as trustworthy and useful without pushing cost past the cap. | Set `BRIEFING_DAILY_OUTLINE_WRITER=off`; the validated runtime flag file can also set `daily_outline_writer` to `false`. Any outline failure must continue to fall back to the current narration writer. |
| `BRIEFING_EPISODE_STREAMING` | Off | Fixture/fake-TTS coverage passes for chapter one becoming playable first, later chapters retaining order, chapter-three failure leaving an honest short complete episode, and cancellation. The API and player must agree on making/playable/complete state. A real TTS run must confirm stable playback and acceptable partial-audio cost. | Set `BRIEFING_EPISODE_STREAMING=off`; the runtime flag file can set `episode_streaming` to `false`. Existing complete-episode behavior remains available. |
| `BRIEFING_SEARCH_FIRST` and the two-story topic floor | Search-first Off; floor is fixed in selection code | Fixture checks prove the floor does not pad, repeat or select off-topic stories. Compare isolated runs across multiple profiles: report fetched, relevant, unseen and selected items per topic, evidence quality, coverage, elapsed time and ledger cost. Search-first becomes default-on only when the extra retrieval reliably improves relevant coverage within the caps. The two-story floor is a selection rule, not an environment flag; changing its default requires its own reviewed code change. | Set `BRIEFING_SEARCH_FIRST=off` or unset it. Revert the floor rule in code if it causes weak or repeated selections; do not suppress thin-supply disclosure. |

## Before changing a default

- [ ] The flag has fixture coverage for both enabled and disabled behavior.
- [ ] Any reader-facing quality claim is checked against full scripts, not just
  a pass count or word count.
- [ ] Specific facts, citations, source boundaries and safety rules still pass.
- [ ] Cost is reported from the normal ledger and remains inside the existing
  daily and monthly caps.
- [ ] The PR names the exact rollback setting and who will check it after the
  change is live.
- [ ] The default-on change is a separate, reviewable change from the code that
  first introduced the experiment.
