# Real quality run plan — 2026-11-25

Run these checks sequentially from the repository root after 05:00 UTC. Each
runner creates fresh temporary generation state and records actual usage in the
normal capped ledger. Do not run them in parallel. The log files retain each
full script, word count, duration, ledger cost, stage timings, sources read,
slow hosts dropped, and source timeouts. The two daily runs use separate fresh
temporary states and the same fictional three-topic profile. The payments
golden report is written to `docs/quality/2026-11-25-hybrid.md` and includes its
judged script.

The hybrid flag is on for every command so the classifier is exercised. It
should select general-background writing for the three evergreen explainers
(payments, natural reactors, and the medical comparison), while the NASA
request stays on the researched path and the one-word topic is not classified
as an explainer. The outline writer is on only for the obscure natural-reactor
question.

```sh
set +e
RUN_OUTPUT_DIR=/tmp/briefing-real-2026-11-25
RUN_SPEND=0
RUN_ENVELOPE=1.00
STOP_RUN=0
RUN_STATUS=0
mkdir -p "$RUN_OUTPUT_DIR"
if [[ "$(date -u +%Y%m%d%H%M)" < 202611250500 ]]; then
  echo 'Wait until 2026-11-25 05:00 UTC before starting provider calls.' >&2
  exit 2
fi

run_case() {
  case_name=$1
  shift
  "$@" >"$RUN_OUTPUT_DIR/$case_name.log" 2>&1
  case_status=$?
  printf '%s: exit %s; %s/%s.log\n' "$case_name" "$case_status" "$RUN_OUTPUT_DIR" "$case_name"
  if (( case_status != 0 )); then RUN_STATUS=1; fi
  case_cost=$(python3 - "$RUN_OUTPUT_DIR/$case_name.log" <<'PY'
import re
import sys
from pathlib import Path
matches = re.findall(r'(?i)ledger cost:\s*\$([0-9]+(?:\.[0-9]+)?)',
                     Path(sys.argv[1]).read_text(errors='replace'))
print(matches[-1] if matches else '0')
PY
  )
  RUN_SPEND=$(python3 - "$RUN_SPEND" "$case_cost" <<'PY'
from decimal import Decimal
import sys
print(Decimal(sys.argv[1]) + Decimal(sys.argv[2]))
PY
  )
  printf 'Cumulative run cost: $%s of $%s\n' "$RUN_SPEND" "$RUN_ENVELOPE"
  if python3 - "$RUN_SPEND" "$RUN_ENVELOPE" <<'PY'
from decimal import Decimal
import sys
raise SystemExit(0 if Decimal(sys.argv[1]) >= Decimal(sys.argv[2]) else 1)
PY
  then
    echo 'Run envelope reached; stop before starting another provider call.' >&2
    STOP_RUN=1
    RUN_STATUS=1
  fi
  return 0
}

# 1. Payments: the existing judged golden case (three minutes, no TTS).
if (( STOP_RUN == 0 )); then
  run_case payments env BRIEFING_EPISODE_HYBRID=on BRIEFING_EPISODE_OUTLINE_WRITER=off \
    python3 dev/golden.py --only instant-payments --date 2026-11-25-hybrid
fi

# 2. Current and specific: the hybrid classifier must leave this researched.
if (( STOP_RUN == 0 )); then
  run_case nasa env BRIEFING_EPISODE_HYBRID=on BRIEFING_EPISODE_OUTLINE_WRITER=off \
    python3 dev/try_real.py episode "What is NASA testing for its next moon mission?" --minutes 5
fi

# 3. Obscure explainer: hybrid plus the outline-first writer.
if (( STOP_RUN == 0 )); then
  run_case obscure-natural-reactors env BRIEFING_EPISODE_HYBRID=on BRIEFING_EPISODE_OUTLINE_WRITER=on \
    python3 dev/try_real.py episode "How do natural nuclear reactors form underground?" --minutes 5
fi

# 4. Medical comparison: sensitive framing and a general-background boundary.
if (( STOP_RUN == 0 )); then
  run_case medical-comparison env BRIEFING_EPISODE_HYBRID=on BRIEFING_EPISODE_OUTLINE_WRITER=off \
    python3 dev/try_real.py episode "How do medical and non-medical approaches to managing chronic pain differ?" --minutes 5
fi

# 5. Broad single-word request.
if (( STOP_RUN == 0 )); then
  run_case broad-ai env BRIEFING_EPISODE_HYBRID=on BRIEFING_EPISODE_OUTLINE_WRITER=off \
    python3 dev/try_real.py episode "AI" --minutes 5
fi

# 6. Daily baseline: force the outline writer off even if the shell has an override.
if (( STOP_RUN == 0 )); then
  run_case daily-default env -u BRIEFING_DAILY_OUTLINE_WRITER python3 dev/try_real.py daily
fi

# 7. Daily A/B: same fictional profile, fresh temporary state, outline writer on.
if (( STOP_RUN == 0 )); then
  run_case daily-outline env BRIEFING_DAILY_OUTLINE_WRITER=on python3 dev/try_real.py daily
fi
exit "$RUN_STATUS"
```

## Budget

| Use | Planning amount |
|---|---:|
| Payments golden generation and judge | $0.10 |
| NASA researched episode and audio | $0.15 |
| Obscure outlined episode and audio | $0.15 |
| Medical comparison and audio | $0.15 |
| Broad single-word episode and audio | $0.15 |
| Variation allowance, within this run envelope | $0.10 |
| Daily default narration | $0.10 |
| Daily outline-writer comparison | $0.10 |
| **Reed run envelope** | **$1.00** |
| Separate daily floor validation allocation | $0.10 |
| 6 am daily reserve; leave untouched | $0.40 |

These amounts are planning limits, not quoted prices. Read the actual ledger
deltas in each log. Stop the sequence if the seven checks reach the $1.00
envelope; never spend the separate $0.10 floor allocation or the $0.40 daily
reserve on these checks. The normal global daily and monthly ledger caps remain
active.

## Review after the run

- Compare the three explainer scripts with the host style target. Confirm each
  says “general background” and separates remembered background from checked
  details. The medical comparison must include its safety note.
- Confirm NASA has sources read and does not use the general-background path.
- For the obscure episode, record whether `outline_pre_audio.passed` is true,
  whether a rewrite was needed, and whether a non-empty not-found list appears
  as one standalone gap sentence.
- For every `try_real` episode, compare research elapsed time with sources read,
  slow hosts dropped, and timeouts in the same log. A faster run that loses
  usable evidence is a regression.
- These `try_real` episode commands call the generation and audio functions
  directly; they do not exercise the job API's opt-in chapter streaming path.
  Keep `BRIEFING_EPISODE_STREAMING` off for this budgeted batch. Measure
  first-chapter availability and full completion only in a separately budgeted
  job-backed run after the streaming backend is merged.
- Keep the broad single-word result even if it is honestly short or falls back;
  do not pad it to meet the requested duration.
- Compare the two daily logs: total words, story count, ledger-cost delta, and
  any spoken-form defects against `dev/tests/fixtures/spoken-forms.json` (in
  particular acronyms, model names, currencies, percentages and quarter labels).
  Note the daily outline flag value, story-title overlap and any feed/source
  differences between the runs; a shared profile does not guarantee identical
  live feed inputs. Do not infer an improvement from word count alone.
