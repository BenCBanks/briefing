# Runbook

## Tests

Every pull request runs the Python suite and the browser checks in GitHub
Actions (`.github/workflows/tests.yml`). Locally:

```sh
mkdir -p /tmp/briefing-test/ledger
BRIEFING_TESTING=1 BRIEFING_EPISODES=fake \
BRIEFING_STATE_DIR=/tmp/briefing-test/state \
BRIEFING_TEST_LEDGER_DIR=/tmp/briefing-test/ledger \
python3 -m unittest discover -s dev/tests -p '*_test.py' -t .
```

Tests refuse to run without `BRIEFING_TESTING=1` and an isolated
`BRIEFING_STATE_DIR`. They use fictional fixtures and mocks and never call a
paid provider. Tests read fixed caps from `dev/tests/fixtures/usage-prices.json`,
so changing the real caps does not change test results.

## Real quality run

When the daily ledger has room, run one check at a time from the repository
root. Each command uses a fresh temporary generation state and the normal global
usage ledger; the daily and monthly caps still refuse calls at the limit:

```sh
python3 dev/try_real.py episode "How do instant payment systems work?" --minutes 5
python3 dev/try_real.py episode "What is NASA testing for its next moon mission?" --minutes 5
python3 dev/try_real.py daily
```

Budget about $0.10–$0.15 for a five-minute episode and $0.03–$0.05 for a daily;
the command prints the actual ledger cost, duration, word count, script and
stage diagnostics. Stop when the configured daily cap is reached.

## Read a daily scorecard

Start with the per-topic supply funnel, then inspect its per-source rows and
the article-read funnel. These are counts at different stages; a large early
count does not guarantee a story was selected.

### Per-topic and per-source supply

The per-topic row reports feed items for that topic:

| Field | Meaning |
|---|---|
| `fetched` | Items obtained from enabled feeds tagged for the topic. This is item count, not the number of feeds or successful HTTP requests. |
| `topic_matched` | Items that match the reader's topic terms and scope. |
| `relevance_passed` | Matched items that pass source-quality, date, and other eligibility checks. |
| `clustered` | Distinct story clusters after duplicate items are grouped. |
| `unseen` | Clusters left after recent-repeat suppression. |
| `candidate_clusters` | The bounded discovery pool offered to article reading and writing. |
| `selected` | Stories that made it into the edition. |

Drop reasons explain where items left the funnel (for example, low source
tier, stale date, topic mismatch, or insufficient full-text evidence). Some
reason counts describe a later check and should not be added to the earlier
funnel stages as if they were one disjoint total.

Each `source_funnels[topic][source_id]` row explains the same feed-stage flow
for one source:

- `fetched`: items from that source considered for the topic;
- `matched`: items that matched the topic;
- `accepted`: items that passed relevance and eligibility checks;
- `rejected`: fetched items not accepted (`fetched - accepted`);
- `rejection_reasons`: counts by recorded reason, including topic mismatch.

Use source rows to distinguish a feed that returned nothing from one that
returned items the topic or quality checks rejected. These fields do not mean
that a story was fact-checked or selected.

### Article reads

`article_read_funnel` begins after the bounded candidate plan:

| Field | Meaning |
|---|---|
| `candidates_planned` | Candidate stories placed in the article-read plan. |
| `article_requests_attempted` | Article requests actually started. |
| `outcome_reasons.ok` | Successful readable article responses; use this as the `article ok` count. |
| `skipped_for_deadline` | Candidates not started because the article-read deadline had passed. |
| `skipped_for_host_interval` | Candidates skipped because the same-host politeness interval could not fit the remaining budget. |
| `extraction_failures` | Started reads that failed for another retrieval/extraction reason. Check `outcome_reasons` for details such as an HTTP error or empty article. |
| `evidence_too_short` | Candidate stories whose available text did not meet the minimum evidence requirement. This is a story-candidate count, not necessarily one per HTTP request. |

`outcome_reasons` contains request-stage outcomes. A successful read can still
produce too little evidence, and enough evidence can still be rejected by the
writer or later content checks. Follow the funnel through `selected` to judge
the edition result.

### Quick health check

For a normal full-registry daily, a useful first-pass target is:

- more than **150 feed items fetched** across the run;
- at least **10 successful article reads** (`outcome_reasons.ok`);
- at least **3 selected stories across the reader's topics**.

These are triage thresholds, not guarantees or hard publication rules. Check
the per-topic rows for coverage, the source-level reasons for concentrated
gaps, and `status.json`/the edition date to confirm that the run completed and
published today's edition. A high fetched count with low `matched`, `ok`, or
`selected` points to a different stage than a feed-fetch problem.

## Daily job: rehearsal and authorized execution

For a focused daily/generation run, use an isolated state root and the existing mocks:

```sh
TEST_STATE=$(mktemp -d)
BRIEFING_STATE_DIR="$TEST_STATE" BRIEFING_EPISODES=fake \
  python3 -m unittest -q dev.tests.daily_test dev.tests.generation_api_test
```

Remove the directory named by `TEST_STATE` when finished if retaining
focused-test state is unnecessary. No fixture-mode flag exists on `daily.py` itself.

When provider work is authorized again, the operator must select the intended
runtime account, reviewed source checkout and private state root. In this
section, `RUN_STATE` is an operator-supplied shell variable identifying
that root, not a new application setting. Ensure `profile.json` contains a valid
saved profile, keys are available to that account, source rights are current,
and the preview and scheduler use the same state configuration. Then:

```sh
BRIEFING_STATE_DIR="$RUN_STATE" python3 dev/ingest/daily.py
```

This performs network/provider work and publishes on success. It prints one
aggregate JSON summary; exit 0 means completed publication, exit 1 failure.
Inspect `status.json`, `generated/default/current.json` and the latest job
scorecard. One or two checked stories can publish as a thin day; three substantive
stories remain the acceptance target. Failed runs preserve the previous bundle.

The wrapper `dev/ingest/generate_today.sh PROFILE_JSON OUTPUT_DIR` instead calls
`generate_now.py`: that CLI writes exports before returning its scorecard result
and does not use the scheduled job's atomic current-bundle publication. Use
`daily.py` for the scheduled operation, not that diagnostic wrapper.

The daily lock prevents concurrent writes, not repeated same-day purchases.
Check for an active job before manually rerunning. Jobs created by the preview
can be polled at `/api/generate/<job-id>`; daily CLI jobs live in their own process
and are not discoverable through the preview's in-memory job map. After restart,
use persisted status/scorecards instead of expecting old job IDs to be pollable.

## Verify the 5:00 Eastern run

No service/timer units are shipped in this repository, and this documentation
does not attest that a particular host has them installed. The deployment's
private inventory supplies the actual daily service/timer names and source/state
locations. An external systemd timer should express the intended schedule as:

```ini
[Timer]
OnCalendar=*-*-* 05:00:00 America/New_York
Persistent=true
```

Its service runs the installed `dev/ingest/daily.py` with the explicit state root.
This is a template fragment, not an installed unit. `Persistent=true` permits a
missed run after downtime; it does not guarantee completion at 05:00. Eastern
time changes with daylight saving: 05:00 is 10:00 UTC in winter and 09:00 UTC in
summer. `BRIEFING_TIMEZONE` does not configure the timer.

For an authorized operational check, set `daily-timer-name` and
`daily-service-name` to the installed unit names from the private inventory:

```sh
systemd-analyze calendar '*-*-* 05:00:00 America/New_York'
systemctl --user list-timers "$daily-timer-name" --all
systemctl --user show "$daily-service-name" \
  -p Result -p ExecMainStatus -p ExecMainStartTimestamp -p ExecMainExitTimestamp
python3 dev/morning_report.py --state-dir "$RUN_STATE" \
  --timezone America/New_York
```

Check the last trigger, successful process exit, today's Eastern edition date,
story count, evidence depth and failed/disabled feed counts. A timer trigger alone
does not prove publication. `morning_report.py` reads local files only and emits
six bounded lines; `--today YYYY-MM-DD` supports fixture-date verification. It
returns nonzero for stale/missing editions, thin days and failed runs. Review its
counts as well as its exit status.

Next verify what the browser actually receives, using the configured loopback
preview URL as `http://127.0.0.1:8898`:

```sh
python3 dev/smoke_live.py "$http://127.0.0.1:8898"
python3 dev/morning_check.py "$http://127.0.0.1:8898"
```

These perform GET checks, not generation. Check `/api/edition?profile=active`,
not just the default route, and `/api/status`. The morning check compares the
active edition with today's Eastern date and reports substantive story counts.
At this baseline its separate local feed-health reader uses the runtime account's
default home state and **does not honor `BRIEFING_STATE_DIR`**. For an alternate
root, use `morning_report.py --state-dir` and the API counts; do not assume the
local feed-health lines describe that alternate state. Legacy edition GETs can
perform migration writes, so these checks are not appropriate against live state
during a no-live-access task.

## Gemini credit exhaustion / HTTP 402

Treat confirmed provider HTTP 402 as a billing/credit stop, not a feed-quality
failure. Do not retry generation repeatedly, rotate keys, increase application
caps or switch to another paid provider to work around it. Keep the last good
edition readable and record that new text/audio is unavailable.

The code does not have a complete dedicated 402 circuit breaker:

- The text proxy converts provider HTTP failures to `502 upstream_unavailable`
  with a bounded internal diagnostic such as `http_402`.
- Daily jobs may expose only `generation_failed:edition_writer:ProxyError`, or a
  scorecard failure after candidate attempts fail. Those are not proof of 402.
- Retrieval reports `provider HTTP error`; episode jobs sanitize it further.
- TTS can report `Gemini API returned HTTP 402`; the renderer can retry each
  segment up to three times. Do not trigger it again while credits are depleted.

Use existing sanitized diagnostics and the provider's account console to confirm
the credit condition; never copy raw provider responses, request headers or keys
into an issue. Local `429 usage_cap_reached` means the estimated application cost
cap, not necessarily exhausted provider credits. Check the count-only usage
ledger separately; do not delete reservations or counters to manufacture budget.

An authorized operator should pause the daily trigger and new generation requests
while resolving credits. The `ingestion.disabled` marker under STATE stops the
normal daily ingest/writer path; it is **not** a global stop for interpretation,
episode research or TTS. Disable real episode work at process startup and avoid
correction requests that enqueue audio. There is no global provider-off API in
this revision. Restoring credits or restarting the app does not itself authorize
paid acceptance: resume only within the operator's current approved scope.

## Truncated JSON responses

The text proxy classifies `finish_reason=MAX_TOKENS` with unparseable JSON as
a truncation, distinct from an HTTP/provider failure. It retries once with a
larger output-token cap (up to 14,000), reserving that attempt through the usual
cost ledger before sending it. If no larger cap is available or the retry still
fails, required episode chapters are marked unanswered; the pipeline does not
make a second smaller-cap retry. Optional chapter expansion remains best-effort:
an exhausted truncation retry leaves the verified shorter chapter and episode
available. Logs contain only fixed failure codes and chapter counts/indexes.

## Feed health and targeted recovery

Inspect `feed-health.json`, `/api/status` health counts and the registry entry
before changing a feed. A quiet feed is not broken. Policy in
[feed_health.py](../dev/ingest/feed_health.py):

| Outcome | Behavior |
| --- | --- |
| Timeout, 429, 5xx, network failure | 1 h, 6 h, then 24 h retry; streak escalates only after six hours |
| 401/403/404/410, robots denial | Weekly retry; four failed weekly probes retire the source |
| Parse failure | Transient first; three separated parse failures become permanent |
| Success or 304 | Clears fetch failure/retry state outside a batch outage |
| More than half of attempted feeds fail | `outage`; all health rows remain unchanged, including successful ones |

Exactly half failing does not activate outage protection. Duplicate-rate and
discovery-score restrictions remain separate quality controls. Due retry selection
is read-only; it does not reset a streak. Registry restrictions and fresh robots
checks still apply to every retry.

There is no feed-health reset API or supported reset CLI at this baseline. Prefer
the scheduled due probe and its success reset. If an administrator has reviewed
and corrected the underlying cause and needs an earlier probe:

1. Pause generation writers; back up the private health file and acquire
   `generation.lock` exclusively for the edit. Preserve every unrelated row.
2. For a transient backoff, move only that row's `disabled_until` to a due UTC
   timestamp. Keep failure counts and prior error evidence until a real success.
3. A retired or indefinitely disabled row needs explicit source/rights review
   first. Only then clear its retirement/indefinite restriction and make a probe
   due; decide and record any deliberate retry-count reset. Do not mark a failed
   fetch as a success or enable an unreviewed registry entry.
4. Atomically replace valid JSON with private permissions, release the lock and
   run one authorized probe through the normal guarded pipeline. During the
   credit freeze, rehearse this using feed-health mocks instead.
5. Verify the actual outcome and new health row. If the problem remains, retain
   the backoff; do not delete the entire file, robots cache or usage ledger.

Relevant fixture coverage: `dev.tests.feed_health_test`. Source permission
changes are separate from retry-state recovery; credibility scores never grant
permission to fetch or retain material.

## Rollback and restart recovery

Keep code and private state backups separate. Before an authorized rollout,
record the last tested code SHA, state configuration, enabled flags and a
consistent backup of generated bundles, history, correction logs and audio
manifests/assets. Back up credentials privately through the deployment's own
secret procedure; never put them in Git or a diagnostic archive.

For a bad deployment, pause scheduled/new generation, restore the last tested
source revision in the deployment checkout and restart only the briefing preview
using its configured supervisor. Check for uncommitted work first; preserve it
instead of issuing an unconditional `git reset --hard`. Repeat the read checks
above and inspect the active profile's corrected text/audio state. During a
fixture-only task, perform this rehearsal solely in a disposable checkout/state.

A code rollback does not roll back data. If the content bundle itself is bad,
restore a known-good **complete** bundle, its matching sources/scorecard and
history from the backup. Do not restore `editions.json` alone: the API prefers
`current.json`. Scheduled and active-profile copies may both need restoration.
Keep correction events and audio revision/hash checks consistent; do not revive
withdrawn/superseded text or make old audio playable by changing its hash.
Preserve newer private data unless a specific data restore is approved.

Restarting loses daily in-memory jobs and episode worker threads. File-backed
unfinished episode records remain, with no automatic recovery dispatcher. Inspect
them before submitting a fresh request: do not blindly replay paid stages. A
failed or cancelled creation still consumes that day's three-episode allowance.
Correction audio worker threads are also in-memory, although their status files
persist; stale text/audio protection remains more important than forcing a ready
state after restart.

## Feeder and watch tooling

`briefing-feed`, `briefing-status`, `briefing-watch` and `briefing-watch-all` are
operator-installed coordination tools, **not repository commands or application
jobs**. Resolve their installation via the private operator inventory; examples
assume they are on PATH. The feeder can start agent work and consumes its private
queue. Never restart it as a documentation test.

After an authorized operator-session/host restart:

1. Check `briefing-feed status` and the process list. Status prints queue/log
   information; it does not itself prove a runner is alive. Preserve an owner's
   deliberate pause and review queued prompts for the current fixture-only rule.
2. If no runner exists and work is authorized, start exactly one:

   ```sh
   setsid nohup briefing-feed run 60 > "$PRIVATE_LOG_PATH" 2>&1 < /dev/null &
   ```

   `PRIVATE_LOG_PATH` is an operator-selected private log file. There is no
   singleton guard in the feeder; duplicate processes can duplicate assignments.
   `briefing-feed pause` creates its pause marker; `resume` removes it. Resume
   only when the owner has authorized ending the pause.
3. Run `briefing-status`, then arm `briefing-watch-all` in the lead's monitoring
   session and restore that session's wakeup arrangement. The shell watcher loops
   continuously and launches `briefing-watch 60`; an enclosing monitor may expire
   and require rearming. Check for orphan child watchers before starting another.
4. Respond to idle/checkpoint/ready events in the lead's current workflow. Keep
   queue/logs private. Agent coordination does not run the daily edition timer.

The feeder keeps `queue.json`, `last.json`, `log.txt`, the `paused` marker and
agent output files in its own home-local state directory named `briefing-feed`.
The watch keeps `watch.seen` there. These files are independent of
`BRIEFING_STATE_DIR`, and deleting them is not an application recovery procedure.

## Credentials: locate and check without printing

Key files are in the **runtime account's home directory**, outside the repository
and STATE. Changing `BRIEFING_STATE_DIR` does not relocate them. Code references:

| File basename | Consumer / configuration reference |
| --- | --- |
| `.briefing_gemini_key` | Text interpretation, daily/episode writing and TTS; `dev/interpreter_proxy.py:KEY_FILE`, TTS provider's `key_path` |
| `.briefing_gemini_search_key` | Gemini grounding; `RetrievalConfig.gemini_key_file` |
| `.briefing_search_key` | Optional Perplexity search; `RetrievalConfig.perplexity_key_file` |

This metadata-only check prints labels and booleans, never file paths or content:

```sh
python3 - <<'PY'
from pathlib import Path
import stat

for label, basename in (
    ('text_and_tts', '.briefing_gemini_key'),
    ('grounding', '.briefing_gemini_search_key'),
    ('optional_search', '.briefing_search_key'),
):
    try:
        info = (Path.home() / basename).stat()
        present = stat.S_ISREG(info.st_mode) and info.st_size > 0
        private = stat.S_IMODE(info.st_mode) == 0o600
    except OSError:
        present = private = False
    print(f'{label}: present={present}, mode_600={private}')
PY
```

Presence and permissions do not establish valid credentials, enabled billing or
available credit. Writer/search readers enforce mode 0600; keep that permission
for TTS too even though its reader does not enforce the same check. Do not run a
model-list/audition request as a key-presence test. Existing provider adapters use
file configuration, not a generic `GEMINI_API_KEY` environment lookup.
