# Any-topic coverage: grounded retrieval — 2026-11-24

Prompt set: **60**. Retrieval-sufficient: **22**. Insufficient evidence: **33**. Retrieval errors: **5**. Not measured: **0**. Model ledger cost: **$0.11957**.

## Provider availability during this run

The run measured all 60 prompts, but it was not a clean 60-prompt grounded-search coverage sample. Gemini first reported its configured daily query cap on prompt 26 (`opinion-02`); 35 prompt rows show that cap. GDELT reported rate limiting or backoff on 57 of 60 rows. Later outcomes therefore include results from the remaining available feeds and cached material, and should not be read as a sustained grounded-search success rate. The by-type results and per-prompt provider statuses below preserve what was actually available.

## What this measures

Mode `grounded` uses the product's retrieval provider path: feeds, grounded search, then GDELT. Searches are serial, spaced by at least five seconds, and rate-limited prompts are retried. For a measured prompt, up to two returned articles were fetched through the normal guarded reader and converted to evidence with the production `evidence_pack` function. The retrieval-sufficient threshold matches the generation entry check: at least one source and five extracted evidence sentences.

This is a retrieval estimate, not an episode outcome. Planning, topic entailment, verification, script checks, fallback writing, and audio were not run. `generation_outcome` is therefore `not run` for every prompt. A retrieval-sufficient result does not establish that a final episode would pass those later stages. Grounded-search mode uses the configured search provider; no script or audio is generated.

## Results by prompt type

| Type | Prompts | Retrieval-sufficient | Insufficient | Retrieval errors | Not measured | Average seconds |
|---|---:|---:|---:|---:|---:|---:|
| Explainer | 6 | 2 | 4 | 0 | 0 | 48.05 |
| How-to and advice | 6 | 5 | 1 | 0 | 0 | 60.35 |
| Current events | 6 | 1 | 5 | 0 | 0 | 72.64 |
| Comparison | 6 | 0 | 6 | 0 | 0 | 39.23 |
| Opinion and debate | 6 | 4 | 2 | 0 | 0 | 64.74 |
| Niche and technical | 6 | 1 | 5 | 0 | 0 | 28.49 |
| Very recent | 6 | 3 | 3 | 0 | 0 | 11.56 |
| One-word broad topic | 6 | 0 | 2 | 4 | 0 | 54.20 |
| Ambiguous | 4 | 1 | 2 | 1 | 0 | 10.38 |
| Sensitive health, legal, or money | 4 | 2 | 2 | 0 | 0 | 11.86 |
| Odd phrasing | 4 | 3 | 1 | 0 | 0 | 13.95 |

## Free and grounded results by prompt type

The free columns are from the matching dated free-mode report; retrieval outcomes are estimates, not completed episodes.

| Type | Prompts | Free sufficient | Free insufficient | Grounded sufficient | Grounded insufficient | Grounded errors | Grounded not measured |
|---|---:|---:|---:|---:|---:|---:|---:|
| Explainer | 6 | 0 | 4 | 2 | 4 | 0 | 0 |
| How-to and advice | 6 | 2 | 4 | 5 | 1 | 0 | 0 |
| Current events | 6 | 1 | 5 | 1 | 5 | 0 | 0 |
| Comparison | 6 | 0 | 6 | 0 | 6 | 0 | 0 |
| Opinion and debate | 6 | 3 | 3 | 4 | 2 | 0 | 0 |
| Niche and technical | 6 | 1 | 5 | 1 | 5 | 0 | 0 |
| Very recent | 6 | 3 | 3 | 3 | 3 | 0 | 0 |
| One-word broad topic | 6 | 0 | 2 | 0 | 2 | 4 | 0 |
| Ambiguous | 4 | 1 | 2 | 1 | 2 | 1 | 0 |
| Sensitive health, legal, or money | 4 | 1 | 3 | 2 | 2 | 0 | 0 |
| Odd phrasing | 4 | 3 | 1 | 3 | 1 | 0 | 0 |

## Per-prompt results

| ID | Type | Prompt | Retrieval outcome | Drop stage | Likely reason | Candidates | Articles read | Evidence sentences | Seconds | Model cost |
|---|---|---|---|---|---|---:|---:|---:|---:|---:|
| explain-01 | Explainer | How do heat pumps move heat instead of generating it? | insufficient_evidence | article_read | candidates found, but readable source text was unavailable | 8 | 0 | 0 | 92.93 | $0.00856 |
| explain-02 | Explainer | Why does inflation make some prices rise faster than others? | insufficient_evidence | article_read | candidates found, but readable source text was unavailable | 8 | 0 | 0 | 60.31 | $0.00187 |
| explain-03 | Explainer | What happens inside a lithium-ion battery as it charges? | insufficient_evidence | article_read | candidates found, but readable source text was unavailable | 8 | 0 | 0 | 16.97 | $0.00323 |
| explain-04 | Explainer | How do instant payment networks settle transfers between banks? | insufficient_evidence | article_read | candidates found, but readable source text was unavailable | 8 | 0 | 0 | 60.37 | $0.00506 |
| explain-05 | Explainer | Why can a local election change school funding? | retrieval_sufficient | none | — | 7 | 1 | 74 | 15.43 | $0.00412 |
| explain-06 | Explainer | How do vaccines train the immune system? | retrieval_sufficient | none | — | 8 | 2 | 111 | 42.28 | $0.01499 |
| howto-01 | How-to and advice | How should a first-time renter compare two apartments? | retrieval_sufficient | none | — | 8 | 1 | 74 | 14.11 | $0.01670 |
| howto-02 | How-to and advice | How can a small business prepare for a ransomware incident? | retrieval_sufficient | none | — | 8 | 1 | 107 | 87.40 | $0.00412 |
| howto-03 | How-to and advice | What is a practical way to start learning conversational Spanish? | retrieval_sufficient | none | — | 8 | 2 | 194 | 61.54 | $0.00268 |
| howto-04 | How-to and advice | How do I build a household emergency kit on a budget? | retrieval_sufficient | none | — | 8 | 1 | 39 | 118.60 | $0.00843 |
| howto-05 | How-to and advice | How should I prepare for a salary negotiation? | insufficient_evidence | article_read | candidates found, but readable source text was unavailable | 8 | 0 | 0 | 15.49 | $0.00260 |
| howto-06 | How-to and advice | How can a city reduce dangerous heat on its streets? | retrieval_sufficient | none | — | 1 | 1 | 22 | 64.97 | $0.00000 |
| current-01 | Current events | What is changing in the US semiconductor industry? | insufficient_evidence | article_read | candidates found, but readable source text was unavailable | 8 | 0 | 0 | 12.45 | $0.00205 |
| current-02 | Current events | What are the latest developments in European energy policy? | insufficient_evidence | article_read | candidates found, but readable source text was unavailable | 8 | 0 | 0 | 11.52 | $0.00231 |
| current-03 | Current events | What is happening with public transit funding in major cities? | insufficient_evidence | article_read | candidates found, but readable source text was unavailable | 8 | 0 | 0 | 48.45 | $0.00501 |
| current-04 | Current events | How are hospitals responding to nurse staffing shortages? | insufficient_evidence | article_read | candidates found, but readable source text was unavailable | 8 | 0 | 0 | 330.58 | $0.00935 |
| current-05 | Current events | What is the current state of electric vehicle charging expansion? | insufficient_evidence | article_read | candidates found, but readable source text was unavailable | 8 | 0 | 0 | 12.80 | $0.00214 |
| current-06 | Current events | What is changing in global shipping routes? | retrieval_sufficient | none | — | 8 | 1 | 107 | 20.06 | $0.00495 |
| compare-01 | Comparison | Compare nuclear power and solar power as electricity sources. | insufficient_evidence | article_read | comparison needs evidence for both sides | 8 | 0 | 0 | 60.33 | $0.00623 |
| compare-02 | Comparison | What is the difference between a debit card and a credit card? | insufficient_evidence | article_read | comparison needs evidence for both sides | 8 | 0 | 0 | 60.04 | $0.00368 |
| compare-03 | Comparison | How do direct air capture and carbon mineralization differ? | insufficient_evidence | article_read | comparison needs evidence for both sides | 8 | 0 | 0 | 12.11 | $0.00000 |
| compare-04 | Comparison | Compare passkeys with passwords for a small business. | insufficient_evidence | article_read | comparison needs evidence for both sides | 8 | 0 | 0 | 0.02 | $0.00000 |
| compare-05 | Comparison | How are heat pumps different from gas furnaces in cold climates? | insufficient_evidence | article_read | comparison needs evidence for both sides | 8 | 0 | 0 | 42.87 | $0.00464 |
| compare-06 | Comparison | How do grid batteries compare with pumped-storage hydropower? | insufficient_evidence | article_read | comparison needs evidence for both sides | 8 | 0 | 0 | 59.98 | $0.00277 |
| opinion-01 | Opinion and debate | Should cities charge drivers to enter busy downtown areas? | retrieval_sufficient | none | — | 2 | 2 | 74 | 61.77 | $0.00000 |
| opinion-02 | Opinion and debate | What are the strongest arguments for and against a four-day workweek? | insufficient_evidence | article_read | candidates found, but readable source text was unavailable | 8 | 0 | 0 | 323.70 | $0.00409 |
| opinion-03 | Opinion and debate | Is nuclear power a sensible part of a low-carbon electricity mix? | insufficient_evidence | article_read | candidates found, but readable source text was unavailable | 5 | 0 | 0 | 0.04 | $0.00000 |
| opinion-04 | Opinion and debate | Should schools limit student use of generative AI? | retrieval_sufficient | none | — | 2 | 1 | 74 | 1.07 | $0.00000 |
| opinion-05 | Opinion and debate | Does remote work help or hurt early-career employees? | retrieval_sufficient | none | — | 8 | 1 | 11 | 0.28 | $0.00000 |
| opinion-06 | Opinion and debate | Should governments regulate social media recommendation algorithms? | retrieval_sufficient | none | — | 1 | 1 | 74 | 1.58 | $0.00000 |
| technical-01 | Niche and technical | How does zero-knowledge proof verification work in rollups? | retrieval_sufficient | none | — | 8 | 1 | 11 | 60.42 | $0.00000 |
| technical-02 | Niche and technical | Why do sodium-ion batteries use different cathode materials from lithium-ion cells? | insufficient_evidence | article_read | niche or technical topic | 8 | 0 | 0 | 60.26 | $0.00000 |
| technical-03 | Niche and technical | What is a phase-locked loop, and where is it used? | insufficient_evidence | article_read | niche or technical topic | 8 | 0 | 0 | 0.02 | $0.00000 |
| technical-04 | Niche and technical | How does differential privacy protect information in a dataset? | insufficient_evidence | article_read | niche or technical topic | 8 | 0 | 0 | 0.18 | $0.00000 |
| technical-05 | Niche and technical | Why do data centers use liquid cooling for high-density AI servers? | insufficient_evidence | article_read | niche or technical topic | 8 | 0 | 0 | 50.00 | $0.00000 |
| technical-06 | Niche and technical | How does an eBPF program run safely inside the Linux kernel? | insufficient_evidence | article_read | niche or technical topic | 8 | 0 | 0 | 0.04 | $0.00000 |
| recent-01 | Very recent | What changed in the past 48 hours in the Taiwan Strait situation? | retrieval_sufficient | none | — | 2 | 2 | 126 | 4.58 | $0.00000 |
| recent-02 | Very recent | What have regulators announced this week about AI model safety? | insufficient_evidence | article_read | very recent; source availability may lag | 8 | 0 | 0 | 0.01 | $0.00000 |
| recent-03 | Very recent | What is the latest on the current federal budget negotiations? | retrieval_sufficient | none | — | 2 | 2 | 126 | 2.33 | $0.00000 |
| recent-04 | Very recent | What changed in the last day in Red Sea shipping? | retrieval_sufficient | none | — | 2 | 2 | 126 | 2.10 | $0.00000 |
| recent-05 | Very recent | What new information emerged this week about mpox outbreaks? | insufficient_evidence | article_read | very recent; source availability may lag | 8 | 0 | 0 | 60.31 | $0.00000 |
| recent-06 | Very recent | What was announced this week about major battery factories? | insufficient_evidence | article_read | very recent; source availability may lag | 8 | 0 | 0 | 0.03 | $0.00000 |
| broad-01 | One-word broad topic | AI | retrieval_error | discovery | — | 0 | 0 | 0 | 0.02 | $0.00000 |
| broad-02 | One-word broad topic | Climate | retrieval_error | discovery | — | 0 | 0 | 0 | 0.01 | $0.00000 |
| broad-03 | One-word broad topic | Energy | retrieval_error | discovery | — | 0 | 0 | 0 | 324.61 | $0.00000 |
| broad-04 | One-word broad topic | Cancer | retrieval_error | discovery | — | 0 | 0 | 0 | 0.03 | $0.00000 |
| broad-05 | One-word broad topic | Inflation | insufficient_evidence | article_read | too broad or ambiguous | 3 | 0 | 0 | 0.19 | $0.00000 |
| broad-06 | One-word broad topic | Water | insufficient_evidence | evidence_sufficiency | too broad or ambiguous | 1 | 1 | 4 | 0.34 | $0.00000 |
| ambiguous-01 | Ambiguous | Explain the court case. | insufficient_evidence | article_read | too broad or ambiguous | 8 | 0 | 0 | 0.02 | $0.00000 |
| ambiguous-02 | Ambiguous | Is it safe now? | retrieval_error | discovery | — | 0 | 0 | 0 | 0.01 | $0.00000 |
| ambiguous-03 | Ambiguous | Tell me what happened with the system. | insufficient_evidence | article_read | too broad or ambiguous | 8 | 0 | 0 | 40.21 | $0.00000 |
| ambiguous-04 | Ambiguous | Should I switch? | retrieval_sufficient | none | — | 1 | 1 | 74 | 1.26 | $0.00000 |
| sensitive-01 | Sensitive health, legal, or money | What should I ask my doctor about recurring headaches? | retrieval_sufficient | none | — | 3 | 2 | 181 | 2.40 | $0.00000 |
| sensitive-02 | Sensitive health, legal, or money | Can my landlord evict me for requesting repairs? | insufficient_evidence | article_read | candidates found, but readable source text was unavailable | 8 | 0 | 0 | 0.04 | $0.00000 |
| sensitive-03 | Sensitive health, legal, or money | What are the trade-offs of using a balance transfer to manage credit-card debt? | retrieval_sufficient | none | — | 8 | 1 | 58 | 44.96 | $0.00000 |
| sensitive-04 | Sensitive health, legal, or money | How should someone think about taking a new medication while pregnant? | insufficient_evidence | article_read | candidates found, but readable source text was unavailable | 5 | 0 | 0 | 0.02 | $0.00000 |
| odd-01 | Odd phrasing | Why is the moon not falling on us if gravity keeps pulling? | retrieval_sufficient | none | — | 8 | 1 | 58 | 0.31 | $0.00000 |
| odd-02 | Odd phrasing | Explain the internet like I'm a cat, but please don't make it babyish. | retrieval_sufficient | none | — | 8 | 2 | 95 | 6.35 | $0.00000 |
| odd-03 | Odd phrasing | What would break first if every office printer stopped working? | insufficient_evidence | article_read | candidates found, but readable source text was unavailable | 8 | 0 | 0 | 43.65 | $0.00000 |
| odd-04 | Odd phrasing | Why can a city be short of water while surrounded by water? | retrieval_sufficient | none | — | 4 | 2 | 84 | 5.50 | $0.00000 |

Provider statuses and fixed failure reasons:

- `explain-01`: empty_article (feeds: no results, gdelt: provider rate limited, gemini: ok)
- `explain-02`: fetch_guard_or_timeout; host_rate_limit_wait_timeout (gdelt: provider backoff, gemini: ok, feeds: ok)
- `explain-03`: robots_disallowed; empty_article (feeds: no results, gdelt: invalid provider response, gemini: ok)
- `explain-04`: empty_article; host_rate_limit_wait_timeout (gdelt: provider backoff, gemini: ok, feeds: provider timeout; cached feed items used)
- `explain-05`: fetch_guard_or_timeout (feeds: ok, gemini: ok, gdelt: invalid provider response)
- `howto-01`: robots_disallowed (gdelt: provider backoff, feeds: ok, gemini: ok)
- `howto-02`: empty_article (feeds: ok, gdelt: provider rate limited, gemini: ok)
- `howto-04`: fetch_guard_or_timeout (feeds: ok, gdelt: provider rate limited, gemini: ok)
- `howto-05`: host_rate_limit_wait_timeout; response_not_html (gdelt: provider backoff, feeds: ok, gemini: ok)
- `current-01`: fetch_guard_or_timeout; host_rate_limit_wait_timeout (gdelt: provider backoff, feeds: ok, gemini: ok)
- `current-02`: host_rate_limit_wait_timeout (gdelt: provider backoff, feeds: ok, gemini: ok)
- `current-03`: fetch_guard_or_timeout; empty_article (gdelt: provider backoff, feeds: ok, gemini: ok)
- `current-04`: fetch_guard_or_timeout; host_rate_limit_wait_timeout (feeds: ok, gdelt: provider timeout, gemini: ok)
- `current-05`: host_rate_limit_wait_timeout (gdelt: provider backoff, feeds: ok, gemini: ok)
- `current-06`: article_access_disabled (gdelt: provider backoff, feeds: ok, gemini: ok)
- `compare-01`: fetch_guard_or_timeout; host_rate_limit_wait_timeout (gdelt: provider backoff, gemini: no results, feeds: ok)
- `compare-02`: fetch_guard_or_timeout; empty_article (gdelt: provider backoff, feeds: ok, gemini: ok)
- `compare-03`: host_rate_limit_wait_timeout (gdelt: provider backoff, feeds: ok, gemini: provider timeout)
- `compare-04`: host_rate_limit_wait_timeout (gemini: provider backoff, gdelt: provider backoff, feeds: ok)
- `compare-05`: fetch_guard_or_timeout; host_rate_limit_wait_timeout (gdelt: provider backoff, feeds: ok, gemini: ok)
- `compare-06`: fetch_guard_or_timeout; host_rate_limit_wait_timeout (gdelt: provider backoff, feeds: ok, gemini: ok)
- `opinion-02`: fetch_guard_or_timeout; host_rate_limit_wait_timeout (feeds: ok, gemini: daily query cap reached, gdelt: provider rate limited)
- `opinion-03`: host_rate_limit_wait_timeout (gdelt: provider backoff, feeds: ok, gemini: daily query cap reached)
- `opinion-04`: host_rate_limit_wait_timeout (gdelt: provider backoff, feeds: ok, gemini: daily query cap reached)
- `opinion-05`: host_rate_limit_wait_timeout (gdelt: provider backoff, feeds: ok, gemini: daily query cap reached)
- `technical-01`: host_rate_limit_wait_timeout (gdelt: provider backoff, gemini: daily query cap reached, feeds: invalid provider response; cached feed items used)
- `technical-02`: fetch_guard_or_timeout; host_rate_limit_wait_timeout (gdelt: provider backoff, gemini: daily query cap reached, feeds: ok)
- `technical-03`: host_rate_limit_wait_timeout (gdelt: provider backoff, feeds: ok, gemini: daily query cap reached)
- `technical-04`: host_rate_limit_wait_timeout (gdelt: provider backoff, gemini: daily query cap reached, feeds: provider timeout; cached feed items used)
- `technical-05`: fetch_guard_or_timeout; host_rate_limit_wait_timeout (gdelt: provider backoff, gemini: daily query cap reached, feeds: ok)
- `technical-06`: host_rate_limit_wait_timeout (gdelt: provider backoff, feeds: ok, gemini: daily query cap reached)
- `recent-02`: host_rate_limit_wait_timeout (gdelt: provider backoff, feeds: ok, gemini: daily query cap reached)
- `recent-05`: fetch_guard_or_timeout; host_rate_limit_wait_timeout (gdelt: provider backoff, gemini: daily query cap reached, feeds: invalid provider response; cached feed items used)
- `recent-06`: host_rate_limit_wait_timeout (gdelt: provider backoff, feeds: ok, gemini: daily query cap reached)
- `broad-01`: provider backoff (gdelt: provider backoff, feeds: no results, gemini: daily query cap reached)
- `broad-02`: provider backoff (gdelt: provider backoff, feeds: no results, gemini: daily query cap reached)
- `broad-03`: provider rate limited (feeds: no results, gemini: daily query cap reached, gdelt: provider rate limited)
- `broad-04`: provider backoff (gdelt: provider backoff, feeds: no results, gemini: daily query cap reached)
- `broad-05`: fetch_guard_or_timeout; host_rate_limit_wait_timeout (gdelt: provider backoff, feeds: ok, gemini: daily query cap reached)
- `ambiguous-01`: host_rate_limit_wait_timeout (gdelt: provider backoff, feeds: ok, gemini: daily query cap reached)
- `ambiguous-02`: provider backoff (gdelt: provider backoff, feeds: no results, gemini: daily query cap reached)
- `ambiguous-03`: fetch_guard_or_timeout; host_rate_limit_wait_timeout (gdelt: provider backoff, feeds: ok, gemini: daily query cap reached)
- `sensitive-02`: host_rate_limit_wait_timeout (gdelt: provider backoff, gemini: daily query cap reached, feeds: ok)
- `sensitive-03`: fetch_guard_or_timeout (gdelt: provider backoff, feeds: ok, gemini: daily query cap reached)
- `sensitive-04`: host_rate_limit_wait_timeout (gdelt: provider backoff, feeds: ok, gemini: daily query cap reached)
- `odd-01`: host_rate_limit_wait_timeout (gdelt: provider backoff, feeds: ok, gemini: daily query cap reached)
- `odd-03`: fetch_guard_or_timeout; host_rate_limit_wait_timeout (gdelt: provider backoff, gemini: daily query cap reached, feeds: ok)
