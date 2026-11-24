# Any-topic coverage: retrieval-only — 2026-11-24

Prompt set: **60**. Retrieval-sufficient: **0**. Insufficient evidence: **0**. Retrieval errors: **1**. Not measured: **59**. Model ledger cost: **$0.00000**.

## What this measures

Each measured prompt was sent once to the free GDELT search adapter. Prompts marked `not_measured` were not sent because the run stopped after a provider rate limit. For a measured prompt, up to two returned articles were fetched through the normal guarded reader and converted to evidence with the production `evidence_pack` function. The retrieval-sufficient threshold matches the generation entry check: at least one source and five extracted evidence sentences.

This is a retrieval estimate, not an episode outcome. Planning, topic entailment, verification, script checks, fallback writing, and audio were not run. `generation_outcome` is therefore `not run` for every prompt. A retrieval-sufficient result does not establish that a final episode would pass those later stages. GDELT search is free; the harness constructs no model client, and the model cost column is $0.00000 for every prompt.

## Results by prompt type

| Type | Prompts | Retrieval-sufficient | Insufficient | Retrieval errors | Not measured | Average seconds |
|---|---:|---:|---:|---:|---:|---:|
| Explainer | 6 | 0 | 0 | 1 | 5 | 29.47 |
| How-to and advice | 6 | 0 | 0 | 0 | 6 | 0.00 |
| Current events | 6 | 0 | 0 | 0 | 6 | 0.00 |
| Comparison | 6 | 0 | 0 | 0 | 6 | 0.00 |
| Opinion and debate | 6 | 0 | 0 | 0 | 6 | 0.00 |
| Niche and technical | 6 | 0 | 0 | 0 | 6 | 0.00 |
| Very recent | 6 | 0 | 0 | 0 | 6 | 0.00 |
| One-word broad topic | 6 | 0 | 0 | 0 | 6 | 0.00 |
| Ambiguous | 4 | 0 | 0 | 0 | 4 | 0.00 |
| Sensitive health, legal, or money | 4 | 0 | 0 | 0 | 4 | 0.00 |
| Odd phrasing | 4 | 0 | 0 | 0 | 4 | 0.00 |

## Per-prompt results

| ID | Type | Prompt | Retrieval outcome | Drop stage | Candidates | Articles read | Evidence sentences | Seconds | Model cost |
|---|---|---|---|---|---:|---:|---:|---:|---:|
| explain-01 | Explainer | How do heat pumps move heat instead of generating it? | retrieval_error | discovery | 0 | 0 | 0 | 29.47 | $0.00000 |
| explain-02 | Explainer | Why does inflation make some prices rise faster than others? | not_measured | provider_unavailable | 0 | 0 | 0 | 0.00 | $0.00000 |
| explain-03 | Explainer | What happens inside a lithium-ion battery as it charges? | not_measured | provider_unavailable | 0 | 0 | 0 | 0.00 | $0.00000 |
| explain-04 | Explainer | How do instant payment networks settle transfers between banks? | not_measured | provider_unavailable | 0 | 0 | 0 | 0.00 | $0.00000 |
| explain-05 | Explainer | Why can a local election change school funding? | not_measured | provider_unavailable | 0 | 0 | 0 | 0.00 | $0.00000 |
| explain-06 | Explainer | How do vaccines train the immune system? | not_measured | provider_unavailable | 0 | 0 | 0 | 0.00 | $0.00000 |
| howto-01 | How-to and advice | How should a first-time renter compare two apartments? | not_measured | provider_unavailable | 0 | 0 | 0 | 0.00 | $0.00000 |
| howto-02 | How-to and advice | How can a small business prepare for a ransomware incident? | not_measured | provider_unavailable | 0 | 0 | 0 | 0.00 | $0.00000 |
| howto-03 | How-to and advice | What is a practical way to start learning conversational Spanish? | not_measured | provider_unavailable | 0 | 0 | 0 | 0.00 | $0.00000 |
| howto-04 | How-to and advice | How do I build a household emergency kit on a budget? | not_measured | provider_unavailable | 0 | 0 | 0 | 0.00 | $0.00000 |
| howto-05 | How-to and advice | How should I prepare for a salary negotiation? | not_measured | provider_unavailable | 0 | 0 | 0 | 0.00 | $0.00000 |
| howto-06 | How-to and advice | How can a city reduce dangerous heat on its streets? | not_measured | provider_unavailable | 0 | 0 | 0 | 0.00 | $0.00000 |
| current-01 | Current events | What is changing in the US semiconductor industry? | not_measured | provider_unavailable | 0 | 0 | 0 | 0.00 | $0.00000 |
| current-02 | Current events | What are the latest developments in European energy policy? | not_measured | provider_unavailable | 0 | 0 | 0 | 0.00 | $0.00000 |
| current-03 | Current events | What is happening with public transit funding in major cities? | not_measured | provider_unavailable | 0 | 0 | 0 | 0.00 | $0.00000 |
| current-04 | Current events | How are hospitals responding to nurse staffing shortages? | not_measured | provider_unavailable | 0 | 0 | 0 | 0.00 | $0.00000 |
| current-05 | Current events | What is the current state of electric vehicle charging expansion? | not_measured | provider_unavailable | 0 | 0 | 0 | 0.00 | $0.00000 |
| current-06 | Current events | What is changing in global shipping routes? | not_measured | provider_unavailable | 0 | 0 | 0 | 0.00 | $0.00000 |
| compare-01 | Comparison | Compare nuclear power and solar power as electricity sources. | not_measured | provider_unavailable | 0 | 0 | 0 | 0.00 | $0.00000 |
| compare-02 | Comparison | What is the difference between a debit card and a credit card? | not_measured | provider_unavailable | 0 | 0 | 0 | 0.00 | $0.00000 |
| compare-03 | Comparison | How do direct air capture and carbon mineralization differ? | not_measured | provider_unavailable | 0 | 0 | 0 | 0.00 | $0.00000 |
| compare-04 | Comparison | Compare passkeys with passwords for a small business. | not_measured | provider_unavailable | 0 | 0 | 0 | 0.00 | $0.00000 |
| compare-05 | Comparison | How are heat pumps different from gas furnaces in cold climates? | not_measured | provider_unavailable | 0 | 0 | 0 | 0.00 | $0.00000 |
| compare-06 | Comparison | How do grid batteries compare with pumped-storage hydropower? | not_measured | provider_unavailable | 0 | 0 | 0 | 0.00 | $0.00000 |
| opinion-01 | Opinion and debate | Should cities charge drivers to enter busy downtown areas? | not_measured | provider_unavailable | 0 | 0 | 0 | 0.00 | $0.00000 |
| opinion-02 | Opinion and debate | What are the strongest arguments for and against a four-day workweek? | not_measured | provider_unavailable | 0 | 0 | 0 | 0.00 | $0.00000 |
| opinion-03 | Opinion and debate | Is nuclear power a sensible part of a low-carbon electricity mix? | not_measured | provider_unavailable | 0 | 0 | 0 | 0.00 | $0.00000 |
| opinion-04 | Opinion and debate | Should schools limit student use of generative AI? | not_measured | provider_unavailable | 0 | 0 | 0 | 0.00 | $0.00000 |
| opinion-05 | Opinion and debate | Does remote work help or hurt early-career employees? | not_measured | provider_unavailable | 0 | 0 | 0 | 0.00 | $0.00000 |
| opinion-06 | Opinion and debate | Should governments regulate social media recommendation algorithms? | not_measured | provider_unavailable | 0 | 0 | 0 | 0.00 | $0.00000 |
| technical-01 | Niche and technical | How does zero-knowledge proof verification work in rollups? | not_measured | provider_unavailable | 0 | 0 | 0 | 0.00 | $0.00000 |
| technical-02 | Niche and technical | Why do sodium-ion batteries use different cathode materials from lithium-ion cells? | not_measured | provider_unavailable | 0 | 0 | 0 | 0.00 | $0.00000 |
| technical-03 | Niche and technical | What is a phase-locked loop, and where is it used? | not_measured | provider_unavailable | 0 | 0 | 0 | 0.00 | $0.00000 |
| technical-04 | Niche and technical | How does differential privacy protect information in a dataset? | not_measured | provider_unavailable | 0 | 0 | 0 | 0.00 | $0.00000 |
| technical-05 | Niche and technical | Why do data centers use liquid cooling for high-density AI servers? | not_measured | provider_unavailable | 0 | 0 | 0 | 0.00 | $0.00000 |
| technical-06 | Niche and technical | How does an eBPF program run safely inside the Linux kernel? | not_measured | provider_unavailable | 0 | 0 | 0 | 0.00 | $0.00000 |
| recent-01 | Very recent | What changed in the past 48 hours in the Taiwan Strait situation? | not_measured | provider_unavailable | 0 | 0 | 0 | 0.00 | $0.00000 |
| recent-02 | Very recent | What have regulators announced this week about AI model safety? | not_measured | provider_unavailable | 0 | 0 | 0 | 0.00 | $0.00000 |
| recent-03 | Very recent | What is the latest on the current federal budget negotiations? | not_measured | provider_unavailable | 0 | 0 | 0 | 0.00 | $0.00000 |
| recent-04 | Very recent | What changed in the last day in Red Sea shipping? | not_measured | provider_unavailable | 0 | 0 | 0 | 0.00 | $0.00000 |
| recent-05 | Very recent | What new information emerged this week about mpox outbreaks? | not_measured | provider_unavailable | 0 | 0 | 0 | 0.00 | $0.00000 |
| recent-06 | Very recent | What was announced this week about major battery factories? | not_measured | provider_unavailable | 0 | 0 | 0 | 0.00 | $0.00000 |
| broad-01 | One-word broad topic | AI | not_measured | provider_unavailable | 0 | 0 | 0 | 0.00 | $0.00000 |
| broad-02 | One-word broad topic | Climate | not_measured | provider_unavailable | 0 | 0 | 0 | 0.00 | $0.00000 |
| broad-03 | One-word broad topic | Energy | not_measured | provider_unavailable | 0 | 0 | 0 | 0.00 | $0.00000 |
| broad-04 | One-word broad topic | Cancer | not_measured | provider_unavailable | 0 | 0 | 0 | 0.00 | $0.00000 |
| broad-05 | One-word broad topic | Inflation | not_measured | provider_unavailable | 0 | 0 | 0 | 0.00 | $0.00000 |
| broad-06 | One-word broad topic | Water | not_measured | provider_unavailable | 0 | 0 | 0 | 0.00 | $0.00000 |
| ambiguous-01 | Ambiguous | Explain the court case. | not_measured | provider_unavailable | 0 | 0 | 0 | 0.00 | $0.00000 |
| ambiguous-02 | Ambiguous | Is it safe now? | not_measured | provider_unavailable | 0 | 0 | 0 | 0.00 | $0.00000 |
| ambiguous-03 | Ambiguous | Tell me what happened with the system. | not_measured | provider_unavailable | 0 | 0 | 0 | 0.00 | $0.00000 |
| ambiguous-04 | Ambiguous | Should I switch? | not_measured | provider_unavailable | 0 | 0 | 0 | 0.00 | $0.00000 |
| sensitive-01 | Sensitive health, legal, or money | What should I ask my doctor about recurring headaches? | not_measured | provider_unavailable | 0 | 0 | 0 | 0.00 | $0.00000 |
| sensitive-02 | Sensitive health, legal, or money | Can my landlord evict me for requesting repairs? | not_measured | provider_unavailable | 0 | 0 | 0 | 0.00 | $0.00000 |
| sensitive-03 | Sensitive health, legal, or money | What are the trade-offs of using a balance transfer to manage credit-card debt? | not_measured | provider_unavailable | 0 | 0 | 0 | 0.00 | $0.00000 |
| sensitive-04 | Sensitive health, legal, or money | How should someone think about taking a new medication while pregnant? | not_measured | provider_unavailable | 0 | 0 | 0 | 0.00 | $0.00000 |
| odd-01 | Odd phrasing | Why is the moon not falling on us if gravity keeps pulling? | not_measured | provider_unavailable | 0 | 0 | 0 | 0.00 | $0.00000 |
| odd-02 | Odd phrasing | Explain the internet like I'm a cat, but please don't make it babyish. | not_measured | provider_unavailable | 0 | 0 | 0 | 0.00 | $0.00000 |
| odd-03 | Odd phrasing | What would break first if every office printer stopped working? | not_measured | provider_unavailable | 0 | 0 | 0 | 0.00 | $0.00000 |
| odd-04 | Odd phrasing | Why can a city be short of water while surrounded by water? | not_measured | provider_unavailable | 0 | 0 | 0 | 0.00 | $0.00000 |

Provider statuses and fixed failure reasons:

- `explain-01`: provider rate limited
- `explain-02`: not attempted after provider rate limit
- `explain-03`: not attempted after provider rate limit
- `explain-04`: not attempted after provider rate limit
- `explain-05`: not attempted after provider rate limit
- `explain-06`: not attempted after provider rate limit
- `howto-01`: not attempted after provider rate limit
- `howto-02`: not attempted after provider rate limit
- `howto-03`: not attempted after provider rate limit
- `howto-04`: not attempted after provider rate limit
- `howto-05`: not attempted after provider rate limit
- `howto-06`: not attempted after provider rate limit
- `current-01`: not attempted after provider rate limit
- `current-02`: not attempted after provider rate limit
- `current-03`: not attempted after provider rate limit
- `current-04`: not attempted after provider rate limit
- `current-05`: not attempted after provider rate limit
- `current-06`: not attempted after provider rate limit
- `compare-01`: not attempted after provider rate limit
- `compare-02`: not attempted after provider rate limit
- `compare-03`: not attempted after provider rate limit
- `compare-04`: not attempted after provider rate limit
- `compare-05`: not attempted after provider rate limit
- `compare-06`: not attempted after provider rate limit
- `opinion-01`: not attempted after provider rate limit
- `opinion-02`: not attempted after provider rate limit
- `opinion-03`: not attempted after provider rate limit
- `opinion-04`: not attempted after provider rate limit
- `opinion-05`: not attempted after provider rate limit
- `opinion-06`: not attempted after provider rate limit
- `technical-01`: not attempted after provider rate limit
- `technical-02`: not attempted after provider rate limit
- `technical-03`: not attempted after provider rate limit
- `technical-04`: not attempted after provider rate limit
- `technical-05`: not attempted after provider rate limit
- `technical-06`: not attempted after provider rate limit
- `recent-01`: not attempted after provider rate limit
- `recent-02`: not attempted after provider rate limit
- `recent-03`: not attempted after provider rate limit
- `recent-04`: not attempted after provider rate limit
- `recent-05`: not attempted after provider rate limit
- `recent-06`: not attempted after provider rate limit
- `broad-01`: not attempted after provider rate limit
- `broad-02`: not attempted after provider rate limit
- `broad-03`: not attempted after provider rate limit
- `broad-04`: not attempted after provider rate limit
- `broad-05`: not attempted after provider rate limit
- `broad-06`: not attempted after provider rate limit
- `ambiguous-01`: not attempted after provider rate limit
- `ambiguous-02`: not attempted after provider rate limit
- `ambiguous-03`: not attempted after provider rate limit
- `ambiguous-04`: not attempted after provider rate limit
- `sensitive-01`: not attempted after provider rate limit
- `sensitive-02`: not attempted after provider rate limit
- `sensitive-03`: not attempted after provider rate limit
- `sensitive-04`: not attempted after provider rate limit
- `odd-01`: not attempted after provider rate limit
- `odd-02`: not attempted after provider rate limit
- `odd-03`: not attempted after provider rate limit
- `odd-04`: not attempted after provider rate limit
