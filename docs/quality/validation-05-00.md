# 05:00 UTC validation order

Run this sequence only after 05:00 UTC on 2026-11-25. Keep the commands
sequential and stop when a run fails or reaches its stated spend envelope. All
generation states are temporary. Provider usage is recorded in the normal
global ledger.

## 1. Episode checks

Run the five episode commands in steps 1–5 of
[`next-real-run.md`](next-real-run.md), in order and with the flags shown there:

1. Payments explainer, with the hybrid writer.
2. NASA current-events question, with the hybrid classifier.
3. Obscure natural-reactor question, with the outline writer.
4. Sensitive medical comparison, with the hybrid writer.
5. Broad single-word request, with the hybrid classifier.

Keep the episode sequence within **$0.80**: the five cases have a combined
planning amount of $0.70, with $0.10 for variation. Use the scripts and output
checks in `next-real-run.md`; do not run its two daily-writing comparisons in
this step.

## 2. Retrieval fallback daily

Use the prepared combined checkout containing PRs #121 and #117. Run the
fixture checks there first, then run exactly one daily:

```sh
set -e
VALIDATION_ROOT="$(mktemp -d)"
trap 'rm -rf "$VALIDATION_ROOT"' EXIT
mkdir -p "$VALIDATION_ROOT/state" "$VALIDATION_ROOT/ledger"
BRIEFING_TESTING=1 BRIEFING_EPISODES=fake \
  BRIEFING_STATE_DIR="$VALIDATION_ROOT/state" \
  BRIEFING_TEST_LEDGER_DIR="$VALIDATION_ROOT/ledger" \
  python3 -m unittest -q dev.tests.retrieval_fill_test dev.tests.daily_briefing_test
python3 dev/try_real.py daily
```

The fixture command is free. Keep this real daily within **$0.10** and do not
retry it after a timeout or failure. Its temporary state is produced by
`try_real.py`'s isolation guard.

## 3. Compare main with #176

Run the comparison from a checkout that has both refs fetched:

```sh
git fetch origin refs/pull/176/head:refs/validation/briefing-176
python3 dev/tools/compare_daily.py \
  --ref-a origin/main \
  --ref-b refs/validation/briefing-176 \
  --max-cost 1.00
```

The tool prepares one feed/store snapshot in a throwaway state, then runs the
accounts-off daily from each ref in separate throwaway states using the same
fictional profile and frozen snapshot. It also compares status and edition key
shapes, stories per topic, audio state, ledger cost by call type, and warning
changes. It prints keys and structure only, never edition text. A structural
shape difference exits nonzero. The comparison envelope is **$1.00**, including
the one-time feed preparation; the normal global ledger cap remains the hard
limit. The comparison renderer uses the non-reserved caller allowance so it
cannot consume the morning reserve.

## Spend ceiling

The daily cap is **$3.00**. Keep **$0.40** untouched for the scheduled morning
run, leaving at most **$2.60** for validation. The maximum allocations are:

| Validation | Maximum |
|---|---:|
| Five episode checks, including variation | $0.80 |
| PR #121/#117 daily | $0.10 |
| Main versus #176 comparison | $1.00 |
| Unallocated non-reserve headroom | $0.70 |
| **Total non-reserve ceiling** | **$2.60** |
| **Protected morning reserve** | **$0.40** |

Cap math: **$3.00 − $0.80 − $0.10 − $1.00 − $0.70 = $0.40**. Read actual
ledger deltas after each step; planning amounts are limits, not quoted costs.
