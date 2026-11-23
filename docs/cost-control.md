# Cost control

Provider usage is counted in the global, count-only ledger at
`~/.local/state/briefing-usage/ledger.json`, with an adjacent `ledger.lock`.
The location is independent of `BRIEFING_STATE_DIR`, so preview servers,
scheduled jobs, episode jobs and acceptance copies share the same cap. The
ledger stores counts, model names, token totals and estimated USD only; it does
not store prompts, search terms, response text, URLs or credentials. File and
directory permissions are restricted to the current user.

The editable configuration is `dev/ingest/usage-prices.json`. Current defaults
are $0.50 overall per day and $5.00 per month, with $0.15 per day reserved for
the unattended 5:00 ET generation. Other callers are stopped at $0.35 for the
day. Every paid Gemini text, Gemini/Perplexity search, and Gemini TTS call is
charged or reserved before its transport is invoked. A denied call raises
`cost_cap_reached`. If the ledger cannot be read or written, provider calls
fail closed. Free feed and GDELT requests are countable but have zero configured
price.

The current Gemini text rate table is marked verified in the config from
[Google AI Studio pricing](https://aistudio.google.com/docs/pricing), dated 18
November 2026. The ledger charges actual Gemini `usageMetadata` token counts
when returned, including thinking tokens. If a response omits usage metadata,
the ledger uses its conservative request reservation and a character-based
fallback; the API and morning check label totals as estimates. TTS remains an
estimate from characters and duration. No displayed estimate should be read as
a billing statement.

`BRIEFING_THINKING` accepts `low` (default), `default`, or `off`. `low` sends
`thinkingLevel: low`; `off` sends `thinkingBudget: 0`; `default` leaves the
model's thinking setting unspecified. This setting is passed through the shared
Gemini call path. Do not use provider-backed tests to compare settings; use a
separately authorized, explicitly budgeted release check.

`GET /api/usage` returns today and this month's counts, estimated spend,
configured caps, remaining allowance and price-list status. `dev/morning_check.py`
prints the same daily and monthly spend/remaining values from that endpoint.
Tests must use an explicit temporary `ledger_dir` (`BRIEFING_TEST_LEDGER_DIR`)
and never write under `~/.local/state/briefing*`.

## One-time handling of the old per-state ledger

Older builds may have written `usage-ledger.json` under
`~/.local/state/briefing/`, and fixture tests may have added counts such as
`fictional-model`. Do not copy that aggregate into the new global ledger: it is
not possible to distinguish each historical real and fictional charge from
the combined period total. Preserve the old file as a restricted historical
archive, inspect its `by_model` counts, and note known fixture counts (for
example, the reported 64 `fictional-model` calls) in the migration record. The
new global ledger starts independently and is the source for future caps and
reports. Do not delete or rewrite the historical file as part of deployment.
