# Episode jobs API

Episodes answer any supported topic on demand, with one narrator and cited evidence.
`POST /api/episodes` accepts `topic`, `length`, and `style`, plus an optional
boolean `fresh` override:

```json
{"topic":"Fictional town transit","length":3,"style":"explainer"}
```

`length` is an integer: **3, 5, 10, or 15 minutes**, targeting **450, 750, 1500,
or 2250 spoken words** at 150 words/minute. Booleans and unsupported lengths fail.
`style` is `news-brief`, `explainer`, or `conversational`. Topic validation currently
accepts 2–80 letters/numbers and the existing limited punctuation; no file paths.
The saved profile owns the job; callers cannot supply another profile ID.
Daily editions remain separate, with the product's five-minute daily briefing goal.

## Real by default and budget admission

Unset `BRIEFING_EPISODES` means `real`. A configured Gemini writer key and, when
TTS is enabled, a configured narration key plus a
successful whole-job budget preflight are required. There is no automatic fake
fallback. `BRIEFING_EPISODES=fake` requires `BRIEFING_TESTING=1`; explicit injected
fixture generators also remain available to tests. Invalid mode values fail closed.
`BRIEFING_TTS=0` disables narration without disabling verified text episodes; the
default is `1`. Disabled TTS is omitted from the admission estimate, and a
completed text job reports `audio_status: "pending"` with no audio URL. Provider
errors are failures and are never silently converted to pending audio.

Before accepting a job, and again after its queue wait before starting, the worker
prices its expected text tokens (including low thinking), bounded grounding calls,
and, when enabled, TTS lite duration using `dev/ingest/usage.py` and `usage-prices.json`. The estimate
must fit both the remaining daily allowance after the unattended-run reserve and
the monthly allowance. Cap refusal is HTTP 429 with:

```json
{"error":"cost_cap_reached","message":"refused: budget"}
```

A missing key or invalid usage configuration returns HTTP 503, `episode_unavailable`,
with a plain unavailable message. Refused jobs do not consume the request allowance.
The estimate is an admission check, **not a whole-job reservation or billing ceiling**:
other work can spend between creation and execution, and model usage can differ.
Every actual provider call still reserves atomically in the shared global ledger.
A later cap refusal ends the job with the same budget message; paid stages never
retry the entire job. Large lengths can be refused even on an otherwise unused day
when the configured allowance is smaller than their estimate. This change does not
increase any configured cap or change keys/provider settings.

HTTP 202 returns `job_id`, `status=queued`, `stage=queued`, `reused=false`, zeroed `counts`,
`target_words`, `cost_estimate` (including `label: "about N cents"`, USD, model,
low-thinking and TTS assumptions), and `left_today`. Three accepted requests per
profile per America/New_York day remain the limit; failed/cancelled requests count.
This request-count limit returns `daily_limit_reached`, distinct from a spend cap.

Before preflight, a request checks completed jobs for the **same saved profile**
from the previous 24 hours. Length and style must match, and normalized topic-word
sets must have Jaccard similarity of at least 0.85. Normalization case-folds text,
removes punctuation and common stopwords, then sorts unique tokens. A match returns
HTTP 200 with the original completed job fields and `reused=true`; it does not
create a job, make provider calls, or consume another daily request slot. Reuse is
profile-scoped to preserve job and audio isolation. Set `fresh: true` to skip reuse
and request a new generation. Reuse is only for finished jobs; queued, running,
failed and cancelled jobs do not match.

## Progress and cancellation

`GET /api/episodes` lists the profile's latest jobs and `daily_limit`, `used_today`,
`left_today`, `words_per_minute=150`, and `length_options` with minute/word targets
and bounded research policy. `GET /api/episodes/<job_id>` returns one job.
Both list rows and individual jobs include `queue_position`: one-based among all
currently queued jobs across profiles, or `0` while the job is already running or no
longer waiting. Queued jobs are dispatched FIFO by their persisted submission
order, with one live generation at a time per service process. Cancelling a
queued job removes it from the waiting positions immediately.
Jobs use 32-character hexadecimal IDs; invalid/foreign IDs return 404.

Stages reflect actual work: `queued`, `researching`, `reading`, `evidence`,
`checking`, `writing`, `checking`, `recording`, `done`. Research/read stages can
repeat across rounds, and a single complete-script repair can repeat writing.
Counts are cumulative observed work: `sources_found` (unique URL hints),
`sources_read` (successfully extracted pages), `claims_matched` (verified claims),
`chapters_written`, and `audio_seconds` (measured completed chapter audio; zero
when TTS is disabled).
Legacy `sources`, `chapters`, and `sentences` remain for existing clients.
A source hint is never represented as a verified claim.

Chapters are written in parallel. If one provider response fails or is malformed,
that chapter is retried once with a smaller target and compact JSON instructions.
If it still fails, the job keeps the successful sibling chapters and adds the
unanswered question to `could_not_confirm`; a later whole-script repair cannot
restore that failed chapter. Safe diagnostics contain fixed reason/finish codes,
JSON parse state, schema key, chapter index, HTTP status class and token counts.
The warning log line contains only those counts and codes, never prompt, evidence,
response text or keys.

`POST /api/episodes/<job_id>/cancel` is idempotent for cancelled jobs; done/failed
jobs return 409. Cancellation is checked at stage boundaries, before/after model
and read calls, before each TTS segment, and before publishing. Paid ledger calls
also check cancellation, including copied contexts in worker threads. In-flight
requests cannot be refunded or interrupted; no next segment starts after a
cancellation check. Partial audio staging is removed. Real jobs have one attempt;
TTS gets one attempt per chapter. Fake jobs retain their bounded retry behavior.

## Count-only quality metrics

Each job exposes a `quality_metrics` object. The same object is stored on the
completed episode record so episode and job views stay aligned. It contains only
counts, estimated minutes/cost and stage durations; it does not copy a topic,
heading, transcript, source URL or profile identifier:

```json
{
  "requested_minutes": 5,
  "spoken_minutes": 4.7,
  "spoken_minutes_basis": "transcript_words_at_150_wpm",
  "sources_read": 8,
  "sources_cited": 6,
  "chapters_answered": 3,
  "chapters_unanswered": 0,
  "retries": {"job": 0, "chapter": 1, "verification": 0, "total": 1},
  "cost_cents": 7,
  "cost_basis": "usage_ledger_estimate",
  "stage_seconds": {
    "researching": 2.1, "reading": 8.4, "evidence": 0.2,
    "checking": 1.0, "writing": 21.3, "recording": 18.0
  }
}
```

When audio is recorded, spoken minutes use measured rendered seconds. Without
audio, they use the approved transcript word count at 150 words per minute.
Costs are rounded up to cents from the count-only usage ledger and remain
estimates, not provider invoices. Admission estimates are labelled separately
until a ledger record exists. Chapter retries count bounded chapter repairs;
verification retries count complete-script repair passes; job retries count
fixture worker attempts. Each stage duration accumulates repeated visits to that
stage. Older jobs without these fields remain readable and are omitted from the
metric coverage count.

To review the latest 20 jobs without printing their content:

```sh
python3 dev/tools/episode_report.py
```

The report aggregates requested and spoken minutes, sources read/cited, answered
and unanswered chapters, retries, estimated cents and seconds by stage. Use
`--state-dir PATH` for a copied fixture state; `--limit` accepts 1 through 20.

## Queue durability and duplicate submissions

Real-mode service startup scans the file-backed queue. Jobs left in a generation
stage by a stopped process are returned to `queued`, marked with the safe
`failure_reason=interrupted`, and resumed in their original FIFO order. A
resumed real job restarts its generation from the beginning; its normal global
ledger preflight runs before provider work, so a depleted cap leaves it failed
without another paid stage. The retry count is visible as `resume_count`.
Queued jobs that have waited more than 24 hours are reaped to `failed` with
`failure_reason=queue_timeout`. An active job with no persisted progress for two
hours is failed as `job_timeout` and its cancellation signal is set so the worker
stops at its next boundary. The ordinary per-profile daily request cap still
counts accepted, failed, and cancelled requests.

Clients may send an optional `Idempotency-Key` header (1–128 letters, numbers,
dots, underscores, colons, or hyphens) with `POST /api/episodes`. The key is
scoped to the saved profile. The store persists only a SHA-256 digest and a request
fingerprint, never the raw key. Repeating the same key and request returns the
original job with `idempotent_replay=true` and does not consume another daily
slot. Reusing a key for different request content returns HTTP 409 with
`idempotency_conflict`. Idempotency is checked before budget preflight and
before the daily request limit.

Files are private JSON under `BRIEFING_STATE_DIR/episode-jobs/profiles/<profile-hash>/`.
Writes are atomic, directory mode 0700/file mode 0600. One real worker runs per
process. Cross-process queue locking is not implemented; the supported preview
service uses one process, and the global cost ledger still guards every provider
call if another process is started accidentally.

## Research, writing and narration

The query planner requests three, three, four, or five independent questions for
3/5/10/15 minutes. Each question gets an equal share of the spoken-word budget.
Research uses existing `dev/ingest/retrieval` adapters: feeds (transient refresh),
GDELT, and Gemini grounding (URL hints only; grounding prose is discarded).
Grounding requires the existing separate search key and is limited to 1/2/3/4
attempts by length; the remaining rounds use free adapters. Planning, writing and
grounding request low thinking; default narration is Gemini TTS lite.

There are at most 2/2/3/3 rounds, 12/12/16/20 successfully read pages, and eight
replacement attempts, within a shared 135-second research/read window and the
335-second text-generation deadline. Later rounds search unresolved questions with
primary-evidence/report qualifiers. Reads are interleaved by subquestion, deduplicate
URLs, limit each host, and use the existing guarded fetch/extraction, robots,
redirect, SSRF and publisher-policy boundaries. Structural passage coverage only
controls when to stop searching; it does not certify relevance or independence.
No search snippet, blocked page or model background knowledge fills an evidence gap.

Extracted sentences form a transient evidence pack. Each factual sentence and
chapter label cites exact evidence IDs. Persisted sources retain URL, publication
and fetch dates, credibility/rights metadata, content hashes and hashed passage
locators, never full source bodies. Unknown reuse rights stay explicit; the existing
Gemini grounding terms limitation remains unresolved for public launch.

Chapter writers receive only their question's evidence. The assembled script runs
the edition claim checks, `episodes/script_checks.py`, and a separate complete-script
semantic verifier. One repair and bounded pruning/rechecking are allowed. Failure
is `insufficient_evidence`; no unverified script reaches TTS. Numbers are spoken
as words, URLs are excluded, repetition and unsupported filler fail checks.

Thin evidence produces an honest shorter episode. Below 85% of the word target,
`shorter_than_requested=true` and `shortfall_notice` appear in both episode/job
results. The frozen speech explicitly says the episode is shorter because evidence
is limited. Relevant verified missing-answer questions are also read under
“What we couldn't confirm.” These editorial notices have empty citation arrays and
`kind=editorial`; factual claims retain sentence-level citations. The complete final
speech, including these notices, passes deterministic script checks. Nothing is
padded to reach the requested length; fewer than 80 verified content words fails.

Player's `episodes_audio.render_episode_audio` renders each frozen chapter, stitches
the WAV and publishes atomically. Narration loads `BRIEFING_STATE_DIR/voice.json`
(default `~/.local/state/briefing/voice.json`), falling back to the first audition
voice only when absent. Invalid settings fail clearly. Tests inject temporary voice
files and fake TTS. Each chapter includes `spoken_text`, `audio_start_seconds`, and
`audio_duration_seconds`. Text and audio hashes/revisions match; a changed script,
voice or model cannot reuse old audio. The result includes `audio.url` at
`/api/episodes/<job_id>/audio`, measured duration, sources and a `cost_record`.

`GET /api/voice` returns only the selected audition letter (`A`–`F`), defaulting to
`A`; `POST /api/voice` accepts `{"letter":"C"}` and stores the manifest-mapped voice
ID in the private settings file. The response returns only the letter. The voice
selection is used for newly rendered audio; it does not silently replace existing
recordings. The audition page posts a letter when its “Use this voice” control is
chosen.

`cost_record` attributes only that job's calls across threads: actual text prompt,
candidate and thinking tokens, priced search requests and TTS estimates derived from
measured audio seconds. Failed requests without usage retain conservative pending
reservations. This is not a provider invoice. `cost_estimate` at creation remains
explicitly an estimate. No source text or credentials enter the usage ledger.

## Fixture verification

Run the unit suite as described in the runbook; CI runs it on every pull request.
