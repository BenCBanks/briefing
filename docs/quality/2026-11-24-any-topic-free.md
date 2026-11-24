# Any-topic coverage: free retrieval — 2026-11-24

Prompt set: **60**. Retrieval-sufficient: **15**. Insufficient evidence: **38**. Retrieval errors: **7**. Not measured: **0**. Model ledger cost: **$0.00000**.

## What this measures

Mode `free` uses the product's retrieval provider path: feeds then GDELT. Searches are serial, spaced by at least five seconds, and rate-limited prompts are retried. For a measured prompt, up to two returned articles were fetched through the normal guarded reader and converted to evidence with the production `evidence_pack` function. The retrieval-sufficient threshold matches the generation entry check: at least one source and five extracted evidence sentences.

The per-prompt `Seconds` value is the elapsed duration of the recorded attempt. This initial run did not include earlier retry attempts or provider backoff waits in that value.

This is a retrieval estimate, not an episode outcome. Planning, topic entailment, verification, script checks, fallback writing, and audio were not run. `generation_outcome` is therefore `not run` for every prompt. A retrieval-sufficient result does not establish that a final episode would pass those later stages. The free mode makes no model calls; the model cost column is $0.00000 for every prompt.

## Results by prompt type

| Type | Prompts | Retrieval-sufficient | Insufficient | Retrieval errors | Not measured | Average seconds |
|---|---:|---:|---:|---:|---:|---:|
| Explainer | 6 | 0 | 4 | 2 | 0 | 25.89 |
| How-to and advice | 6 | 2 | 4 | 0 | 0 | 21.66 |
| Current events | 6 | 1 | 5 | 0 | 0 | 9.35 |
| Comparison | 6 | 0 | 6 | 0 | 0 | 17.39 |
| Opinion and debate | 6 | 3 | 3 | 0 | 0 | 17.90 |
| Niche and technical | 6 | 1 | 5 | 0 | 0 | 20.47 |
| Very recent | 6 | 3 | 3 | 0 | 0 | 11.25 |
| One-word broad topic | 6 | 0 | 2 | 4 | 0 | 10.09 |
| Ambiguous | 4 | 1 | 2 | 1 | 0 | 10.27 |
| Sensitive health, legal, or money | 4 | 1 | 3 | 0 | 0 | 12.69 |
| Odd phrasing | 4 | 3 | 1 | 0 | 0 | 13.03 |

## Per-prompt results

| ID | Type | Prompt | Retrieval outcome | Drop stage | Likely reason | Candidates | Articles read | Evidence sentences | Seconds | Model cost |
|---|---|---|---|---|---|---:|---:|---:|---:|---:|
| explain-01 | Explainer | How do heat pumps move heat instead of generating it? | retrieval_error | discovery | — | 0 | 0 | 0 | 11.30 | $0.00000 |
| explain-02 | Explainer | Why does inflation make some prices rise faster than others? | insufficient_evidence | article_read | candidates found, but readable source text was unavailable | 6 | 0 | 0 | 60.36 | $0.00000 |
| explain-03 | Explainer | What happens inside a lithium-ion battery as it charges? | retrieval_error | discovery | — | 0 | 0 | 0 | 12.15 | $0.00000 |
| explain-04 | Explainer | How do instant payment networks settle transfers between banks? | insufficient_evidence | article_read | candidates found, but readable source text was unavailable | 8 | 0 | 0 | 60.46 | $0.00000 |
| explain-05 | Explainer | Why can a local election change school funding? | insufficient_evidence | article_read | candidates found, but readable source text was unavailable | 3 | 0 | 0 | 10.84 | $0.00000 |
| explain-06 | Explainer | How do vaccines train the immune system? | insufficient_evidence | article_read | candidates found, but readable source text was unavailable | 8 | 0 | 0 | 0.21 | $0.00000 |
| howto-01 | How-to and advice | How should a first-time renter compare two apartments? | retrieval_sufficient | none | — | 8 | 2 | 93 | 4.00 | $0.00000 |
| howto-02 | How-to and advice | How can a small business prepare for a ransomware incident? | insufficient_evidence | article_read | candidates found, but readable source text was unavailable | 8 | 0 | 0 | 60.34 | $0.00000 |
| howto-03 | How-to and advice | What is a practical way to start learning conversational Spanish? | retrieval_sufficient | none | — | 3 | 2 | 181 | 4.89 | $0.00000 |
| howto-04 | How-to and advice | How do I build a household emergency kit on a budget? | insufficient_evidence | article_read | candidates found, but readable source text was unavailable | 4 | 0 | 0 | 0.02 | $0.00000 |
| howto-05 | How-to and advice | How should I prepare for a salary negotiation? | insufficient_evidence | article_read | candidates found, but readable source text was unavailable | 8 | 0 | 0 | 0.02 | $0.00000 |
| howto-06 | How-to and advice | How can a city reduce dangerous heat on its streets? | insufficient_evidence | article_read | candidates found, but readable source text was unavailable | 4 | 0 | 0 | 60.71 | $0.00000 |
| current-01 | Current events | What is changing in the US semiconductor industry? | insufficient_evidence | article_read | candidates found, but readable source text was unavailable | 8 | 0 | 0 | 0.13 | $0.00000 |
| current-02 | Current events | What are the latest developments in European energy policy? | insufficient_evidence | article_read | candidates found, but readable source text was unavailable | 8 | 0 | 0 | 0.02 | $0.00000 |
| current-03 | Current events | What is happening with public transit funding in major cities? | insufficient_evidence | article_read | candidates found, but readable source text was unavailable | 8 | 0 | 0 | 0.03 | $0.00000 |
| current-04 | Current events | How are hospitals responding to nurse staffing shortages? | insufficient_evidence | article_read | candidates found, but readable source text was unavailable | 8 | 0 | 0 | 45.15 | $0.00000 |
| current-05 | Current events | What is the current state of electric vehicle charging expansion? | insufficient_evidence | article_read | candidates found, but readable source text was unavailable | 8 | 0 | 0 | 0.02 | $0.00000 |
| current-06 | Current events | What is changing in global shipping routes? | retrieval_sufficient | none | — | 4 | 2 | 181 | 10.72 | $0.00000 |
| compare-01 | Comparison | Compare nuclear power and solar power as electricity sources. | insufficient_evidence | article_read | comparison needs evidence for both sides | 8 | 0 | 0 | 44.28 | $0.00000 |
| compare-02 | Comparison | What is the difference between a debit card and a credit card? | insufficient_evidence | article_read | comparison needs evidence for both sides | 8 | 0 | 0 | 16.19 | $0.00000 |
| compare-03 | Comparison | How do direct air capture and carbon mineralization differ? | insufficient_evidence | article_read | comparison needs evidence for both sides | 8 | 0 | 0 | 43.79 | $0.00000 |
| compare-04 | Comparison | Compare passkeys with passwords for a small business. | insufficient_evidence | article_read | comparison needs evidence for both sides | 8 | 0 | 0 | 0.03 | $0.00000 |
| compare-05 | Comparison | How are heat pumps different from gas furnaces in cold climates? | insufficient_evidence | article_read | comparison needs evidence for both sides | 8 | 0 | 0 | 0.02 | $0.00000 |
| compare-06 | Comparison | How do grid batteries compare with pumped-storage hydropower? | insufficient_evidence | article_read | comparison needs evidence for both sides | 8 | 0 | 0 | 0.02 | $0.00000 |
| opinion-01 | Opinion and debate | Should cities charge drivers to enter busy downtown areas? | retrieval_sufficient | none | — | 2 | 2 | 74 | 66.32 | $0.00000 |
| opinion-02 | Opinion and debate | What are the strongest arguments for and against a four-day workweek? | insufficient_evidence | article_read | candidates found, but readable source text was unavailable | 8 | 0 | 0 | 0.15 | $0.00000 |
| opinion-03 | Opinion and debate | Is nuclear power a sensible part of a low-carbon electricity mix? | insufficient_evidence | article_read | candidates found, but readable source text was unavailable | 6 | 0 | 0 | 0.02 | $0.00000 |
| opinion-04 | Opinion and debate | Should schools limit student use of generative AI? | insufficient_evidence | article_read | candidates found, but readable source text was unavailable | 4 | 0 | 0 | 0.22 | $0.00000 |
| opinion-05 | Opinion and debate | Does remote work help or hurt early-career employees? | retrieval_sufficient | none | — | 8 | 1 | 11 | 39.54 | $0.00000 |
| opinion-06 | Opinion and debate | Should governments regulate social media recommendation algorithms? | retrieval_sufficient | none | — | 1 | 1 | 74 | 1.16 | $0.00000 |
| technical-01 | Niche and technical | How does zero-knowledge proof verification work in rollups? | retrieval_sufficient | none | — | 8 | 1 | 11 | 60.51 | $0.00000 |
| technical-02 | Niche and technical | Why do sodium-ion batteries use different cathode materials from lithium-ion cells? | insufficient_evidence | article_read | niche or technical topic | 8 | 0 | 0 | 12.03 | $0.00000 |
| technical-03 | Niche and technical | What is a phase-locked loop, and where is it used? | insufficient_evidence | article_read | niche or technical topic | 8 | 0 | 0 | 0.03 | $0.00000 |
| technical-04 | Niche and technical | How does differential privacy protect information in a dataset? | insufficient_evidence | article_read | niche or technical topic | 8 | 0 | 0 | 0.28 | $0.00000 |
| technical-05 | Niche and technical | Why do data centers use liquid cooling for high-density AI servers? | insufficient_evidence | article_read | niche or technical topic | 8 | 0 | 0 | 49.96 | $0.00000 |
| technical-06 | Niche and technical | How does an eBPF program run safely inside the Linux kernel? | insufficient_evidence | article_read | niche or technical topic | 8 | 0 | 0 | 0.02 | $0.00000 |
| recent-01 | Very recent | What changed in the past 48 hours in the Taiwan Strait situation? | retrieval_sufficient | none | — | 3 | 2 | 126 | 2.03 | $0.00000 |
| recent-02 | Very recent | What have regulators announced this week about AI model safety? | insufficient_evidence | article_read | very recent; source availability may lag | 8 | 0 | 0 | 0.01 | $0.00000 |
| recent-03 | Very recent | What is the latest on the current federal budget negotiations? | retrieval_sufficient | none | — | 3 | 2 | 126 | 2.68 | $0.00000 |
| recent-04 | Very recent | What changed in the last day in Red Sea shipping? | retrieval_sufficient | none | — | 3 | 2 | 126 | 2.40 | $0.00000 |
| recent-05 | Very recent | What new information emerged this week about mpox outbreaks? | insufficient_evidence | article_read | very recent; source availability may lag | 8 | 0 | 0 | 60.36 | $0.00000 |
| recent-06 | Very recent | What was announced this week about major battery factories? | insufficient_evidence | article_read | very recent; source availability may lag | 8 | 0 | 0 | 0.02 | $0.00000 |
| broad-01 | One-word broad topic | AI | retrieval_error | discovery | — | 0 | 0 | 0 | 0.01 | $0.00000 |
| broad-02 | One-word broad topic | Climate | retrieval_error | discovery | — | 0 | 0 | 0 | 0.01 | $0.00000 |
| broad-03 | One-word broad topic | Energy | retrieval_error | discovery | — | 0 | 0 | 0 | 60.12 | $0.00000 |
| broad-04 | One-word broad topic | Cancer | retrieval_error | discovery | — | 0 | 0 | 0 | 0.00 | $0.00000 |
| broad-05 | One-word broad topic | Inflation | insufficient_evidence | article_read | too broad or ambiguous | 3 | 0 | 0 | 0.16 | $0.00000 |
| broad-06 | One-word broad topic | Water | insufficient_evidence | evidence_sufficiency | too broad or ambiguous | 1 | 1 | 4 | 0.23 | $0.00000 |
| ambiguous-01 | Ambiguous | Explain the court case. | insufficient_evidence | article_read | too broad or ambiguous | 8 | 0 | 0 | 0.03 | $0.00000 |
| ambiguous-02 | Ambiguous | Is it safe now? | retrieval_error | discovery | — | 0 | 0 | 0 | 0.01 | $0.00000 |
| ambiguous-03 | Ambiguous | Tell me what happened with the system. | insufficient_evidence | article_read | too broad or ambiguous | 8 | 0 | 0 | 40.16 | $0.00000 |
| ambiguous-04 | Ambiguous | Should I switch? | retrieval_sufficient | none | — | 1 | 1 | 74 | 0.88 | $0.00000 |
| sensitive-01 | Sensitive health, legal, or money | What should I ask my doctor about recurring headaches? | insufficient_evidence | article_read | candidates found, but readable source text was unavailable | 6 | 0 | 0 | 0.30 | $0.00000 |
| sensitive-02 | Sensitive health, legal, or money | Can my landlord evict me for requesting repairs? | insufficient_evidence | article_read | candidates found, but readable source text was unavailable | 8 | 0 | 0 | 0.02 | $0.00000 |
| sensitive-03 | Sensitive health, legal, or money | What are the trade-offs of using a balance transfer to manage credit-card debt? | retrieval_sufficient | none | — | 8 | 1 | 58 | 50.29 | $0.00000 |
| sensitive-04 | Sensitive health, legal, or money | How should someone think about taking a new medication while pregnant? | insufficient_evidence | article_read | candidates found, but readable source text was unavailable | 8 | 0 | 0 | 0.16 | $0.00000 |
| odd-01 | Odd phrasing | Why is the moon not falling on us if gravity keeps pulling? | retrieval_sufficient | none | — | 8 | 1 | 47 | 0.65 | $0.00000 |
| odd-02 | Odd phrasing | Explain the internet like I'm a cat, but please don't make it babyish. | retrieval_sufficient | none | — | 8 | 2 | 99 | 5.75 | $0.00000 |
| odd-03 | Odd phrasing | What would break first if every office printer stopped working? | insufficient_evidence | article_read | candidates found, but readable source text was unavailable | 8 | 0 | 0 | 44.45 | $0.00000 |
| odd-04 | Odd phrasing | Why can a city be short of water while surrounded by water? | retrieval_sufficient | none | — | 3 | 2 | 121 | 1.27 | $0.00000 |

Provider statuses and fixed failure reasons:

- `explain-01`: provider rate limited (feeds: no results, gdelt: provider rate limited)
- `explain-02`: fetch_guard_or_timeout; host_rate_limit_wait_timeout (gdelt: provider backoff, feeds: ok)
- `explain-03`: invalid provider response (feeds: no results, gdelt: invalid provider response)
- `explain-04`: empty_article; host_rate_limit_wait_timeout (gdelt: provider backoff, feeds: provider timeout; cached feed items used)
- `explain-05`: fetch_guard_or_timeout; host_rate_limit_wait_timeout (feeds: ok, gdelt: provider rate limited)
- `explain-06`: host_rate_limit_wait_timeout (gdelt: provider backoff, feeds: provider timeout; cached feed items used)
- `howto-02`: fetch_guard_or_timeout; host_rate_limit_wait_timeout (gdelt: provider backoff, feeds: ok)
- `howto-04`: host_rate_limit_wait_timeout (gdelt: provider backoff, feeds: ok)
- `howto-05`: host_rate_limit_wait_timeout (gdelt: provider backoff, feeds: ok)
- `howto-06`: empty_article; host_rate_limit_wait_timeout (gdelt: provider backoff, feeds: invalid provider response; cached feed items used)
- `current-01`: fetch_guard_or_timeout; host_rate_limit_wait_timeout (gdelt: provider backoff, feeds: ok)
- `current-02`: host_rate_limit_wait_timeout (gdelt: provider backoff, feeds: ok)
- `current-03`: host_rate_limit_wait_timeout (gdelt: provider backoff, feeds: ok)
- `current-04`: fetch_guard_or_timeout; host_rate_limit_wait_timeout (feeds: ok, gdelt: invalid provider response)
- `current-05`: host_rate_limit_wait_timeout (gdelt: provider backoff, feeds: ok)
- `compare-01`: fetch_guard_or_timeout; host_rate_limit_wait_timeout (gdelt: provider backoff, feeds: ok)
- `compare-02`: host_rate_limit_wait_timeout; empty_article (gdelt: provider backoff, feeds: ok)
- `compare-03`: fetch_guard_or_timeout; host_rate_limit_wait_timeout (gdelt: provider backoff, feeds: ok)
- `compare-04`: host_rate_limit_wait_timeout (gdelt: provider backoff, feeds: ok)
- `compare-05`: host_rate_limit_wait_timeout (gdelt: provider backoff, feeds: ok)
- `compare-06`: host_rate_limit_wait_timeout (gdelt: provider backoff, feeds: ok)
- `opinion-02`: fetch_guard_or_timeout; host_rate_limit_wait_timeout (gdelt: provider backoff, feeds: ok)
- `opinion-03`: host_rate_limit_wait_timeout (gdelt: provider backoff, feeds: ok)
- `opinion-04`: host_rate_limit_wait_timeout; empty_article (gdelt: provider backoff, feeds: ok)
- `opinion-05`: host_rate_limit_wait_timeout (gdelt: provider backoff, feeds: ok)
- `technical-01`: host_rate_limit_wait_timeout (gdelt: provider backoff, feeds: invalid provider response; cached feed items used)
- `technical-02`: fetch_guard_or_timeout; host_rate_limit_wait_timeout (feeds: ok, gdelt: provider rate limited)
- `technical-03`: host_rate_limit_wait_timeout (gdelt: provider backoff, feeds: ok)
- `technical-04`: host_rate_limit_wait_timeout (gdelt: provider backoff, feeds: provider timeout; cached feed items used)
- `technical-05`: fetch_guard_or_timeout; host_rate_limit_wait_timeout (gdelt: provider backoff, feeds: ok)
- `technical-06`: host_rate_limit_wait_timeout (gdelt: provider backoff, feeds: ok)
- `recent-02`: host_rate_limit_wait_timeout (gdelt: provider backoff, feeds: ok)
- `recent-05`: fetch_guard_or_timeout; host_rate_limit_wait_timeout (gdelt: provider backoff, feeds: invalid provider response; cached feed items used)
- `recent-06`: host_rate_limit_wait_timeout (gdelt: provider backoff, feeds: ok)
- `broad-01`: provider backoff (gdelt: provider backoff, feeds: no results)
- `broad-02`: provider backoff (gdelt: provider backoff, feeds: no results)
- `broad-03`: provider backoff (gdelt: provider backoff, feeds: provider timeout)
- `broad-04`: provider backoff (feeds: provider backoff, gdelt: provider backoff)
- `broad-05`: fetch_guard_or_timeout; host_rate_limit_wait_timeout (gdelt: provider backoff, feeds: ok)
- `ambiguous-01`: host_rate_limit_wait_timeout (gdelt: provider backoff, feeds: ok)
- `ambiguous-02`: provider backoff (gdelt: provider backoff, feeds: no results)
- `ambiguous-03`: fetch_guard_or_timeout; host_rate_limit_wait_timeout (gdelt: provider backoff, feeds: ok)
- `sensitive-01`: empty_article; host_rate_limit_wait_timeout (gdelt: provider backoff, feeds: ok)
- `sensitive-02`: host_rate_limit_wait_timeout (gdelt: provider backoff, feeds: ok)
- `sensitive-03`: empty_article (feeds: ok, gdelt: provider timeout)
- `sensitive-04`: fetch_guard_or_timeout; host_rate_limit_wait_timeout (gdelt: provider backoff, feeds: ok)
- `odd-01`: host_rate_limit_wait_timeout (gdelt: provider backoff, feeds: ok)
- `odd-03`: fetch_guard_or_timeout; host_rate_limit_wait_timeout (gdelt: provider backoff, feeds: ok)
