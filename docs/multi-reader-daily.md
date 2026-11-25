# Multi-reader daily design

This is the design for the accounts-on daily run. The default remains the
existing one-reader path: when `BRIEFING_ACCOUNTS=off`, the scheduler calls the
current daily generation code with the same state paths, ordering, prompts and
ledger calls. A golden fixture must compare that output byte-for-byte before
the accounts path is enabled.

## Run shape

At the daily start, take a stable snapshot of enabled reader profiles. A reader
added or edited after the snapshot waits for the next run. The runner then has
three bounded phases:

1. **Plan the union.** Normalize each reader's topic identity and form the
   sorted union of topics. Duplicate identities are one pool key, so overlapping
   readers share work.
2. **Build the evidence pool.** For each pool key, perform one feed fetch and
   extraction pass, using the existing per-source deadlines and total run
   deadline. Cache the resulting evidence pack by topic and run date in a
   temporary shared-run directory. The cache contains source IDs, claims and
   provenance, never a reader's private profile data. A failed topic is recorded
   as failed evidence and does not abort other topics.
3. **Fan out.** In stable reader order, select items from only that reader's
   topic keys, write that reader's edition, and render its audio. The writer and
   TTS calls are charged to that reader; retrieval calls were charged once to
   the shared run. A reader receives no evidence or sources from a topic absent
   from its profile.

The shared cache is an optimization boundary, not a publication boundary.
Each reader gets a separate edition, history, audio and status record under its
own state directory. A reader's failed writer or audio render records a plain
failure state and does not prevent other readers from completing.

## Budget and fairness

The global usage ledger remains authoritative for both retrieval and writing.
The scheduled six o'clock reserve is allocated before work begins:

- Reserve `R` is divided into equal reader shares, with any remainder handed
  out one cent at a time in stable account-ID order.
- Stable rotation starts at `(run_date modulo reader_count)` and visits readers
  circularly. Over a five-day simulation, every reader gets the same number of
  first opportunities, within one when the reader count does not divide five.
- A reader may spend only its current share. Unused share is returned to the
  common reserve before the next reader is considered; no reader can consume
  another reader's share without the same stable rotation reaching it.
- Every provider call still performs the normal ledger preflight and records
  actual cost afterward. If the global cap is reached, remaining readers are
  marked `not_today` with a reader-facing message such as “There was not enough
  shared budget to make your briefing today. It will try again tomorrow.”

`not_today` is a terminal daily state for that run. It has no partial edition,
no misleading audio link and no half-written `current.json`. A later retry may
replace it only by creating a complete edition. Retrieval failure, writer
failure and audio failure use distinct internal diagnostics but expose plain
reader-safe wording.

## Stop and recovery rules

The multi-reader runner has one wall-clock deadline, configured by the operator,
and a bounded per-topic deadline inherited from research. At the deadline it
stops starting new fetches or provider calls, closes the shared cache, and
finishes only already committed reader records. It writes a run summary with
reader states, topics attempted, shared retrieval cost and per-reader writing
cost. A crashed run can be retried from the next scheduled window; it must not
reuse a partial reader edition as today's result.

The shared cache is disposable and keyed by the run date plus normalized topic
identity and source-pack version. It is never used by the accounts-off path.
Concurrent daily starts take the existing daily-run lock; only one union pass
may run at a time.

## Test plan and rollout gate

- Three fictional readers with overlapping topics prove each union topic is
  fetched and extracted once, while each edition contains only that reader's
  topics.
- A forced failure for one reader proves the other two complete. A forced
  global-cap refusal proves later readers receive `not_today`, no partial
  edition, and no extra calls.
- A fake ledger proves total retrieval plus fan-out spend never exceeds the
  configured daily and monthly caps. A five-day simulation proves stable
  rotation fairness.
- A golden accounts-off fixture compares the current single-reader output
  byte-for-byte and remains the release gate for this flag.

The implementation stays behind `BRIEFING_ACCOUNTS`; the flag is not default-on
until these fixtures and a multi-reader dry run pass in a temporary state.
