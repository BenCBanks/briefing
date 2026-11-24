# Daily supply funnel — 24 November 2026

## Measurement

One real daily used the standard capped ledger, a fresh temporary state, and the fictional AI chips, Climate policy, and Space launches profile. It fetched 72 configured feeds with no feed-fetch errors. The generation cost was **$0.07926** according to the ledger and took **125.20 seconds**. The run selected three Space launches stories and none for the other two topics; the edition failed the narration scorecard because narration was underdeveloped with unused evidence.

| Topic | Feed items | Topic matched | Relevance passed | Distinct clusters | Unseen after repeat check | Candidate pool after refill | Selected |
|---|---:|---:|---:|---:|---:|---:|---:|
| AI chips | 419 | 419 | 73 | 70 | 70 | 32 | 0 |
| Climate policy | 165 | 165 | 43 | 33 | 33 | 30 | 0 |
| Space launches | 193 | 193 | 193 | 177 | 177 | 30 | 3 |

`Unseen` is the number of distinct clusters remaining after the 14-day repeat filter. It is not the count before clustering: in this run, clustering reduced relevance-passed items by 3, 10, and 16 respectively, and repeat suppression removed no clusters. The previous non-monotonic funnel came from ranking cached items from disabled or differently tagged sources while the fetched count included only enabled sources tagged for that topic. The funnel now ranks only current enabled topic-feed items, so topic-matched cannot exceed feed items.

## Where candidates were lost

- Source-quality and policy filters removed low-tier, denied-publisher, crypto-only, affiliate, and listicle results before story writing.
- Evidence checks rejected 9 AI chips, 7 Climate policy, and 1 Space launches candidate. Semantic relevance checks rejected 2, 4, and 3 respectively; the Space outlet cap removed 1 more.
- The bounded open-web fallback searched the two uncovered topics, made six requests, and added two candidates. The feeds provider returned successfully; GDELT was unavailable for 3/3 requests and Gemini for 3/3. Grounded search made zero requests. The two added candidates did not pass story selection.

The refill path now preserves untried feed clusters after the first 24 candidate extractions, alongside any open-web additions, so evidence failures do not strand the remaining writer budget. The real run still did not meet the product target of one story per topic. Feed supply and the currently unavailable external search providers leave the thin-topic fallback unproven; this report does not claim the daily-supply issue is complete.

## Search-first comparison run

One additional real daily was run with `search_first=on` through `dev/try_real.py daily --search-first`, using the same fresh temporary state and fictional profile. It stopped during the refill round with `RecursionError` in composed article-prefetch callbacks, before story selection. **No stories were selected; no script or first-100-word excerpt was produced.** Ledger cost was **$0.04850** and wall time was **120.82 seconds**. The callback composition and 90-day search-result window now have fixture regression tests; no second provider run was made.

## Fixture verification

`ingest_writer_test`, `retrieval_fill_test`, `try_real_test`, `daily_briefing_test`, and `daily_monitor_test`: **120 passed**. No browser or preview server was run.
