# Source registry coverage

This is an offline audit of the public, location-neutral source registry, which contains 239 general feeds. Local source definitions stay outside the public repository. Configure `BRIEFING_LOCAL_SOURCES`, or put `local-sources.json` in `BRIEFING_DATA_DIR` (default `~/.local/share/briefing/local-sources.json`). If no file exists, the application loads no local sources. The format example contains three fictional sources in [`local-sources.example.json`](../content/local-sources.example.json).

## Coverage audit

| Area | Registry sources | Enabled |
| --- | ---: | ---: |
| Payments and fintech | 46 | 42 |
| Health policy | 19 | 10 |
| AI | 15 | 13 |
| Housing | 43 | 43 |
| Energy | 6 | 1 |
| Local news | 0 | 0 |
| Science | 11 | 6 |
| Sports business | 3 | 0 |

Topics can overlap: feeds tagged with multiple broad subjects are counted in each matching row. Reader profiles provide their own place and subject definition.

## Rights, readability and cadence

The checked-in registry records source access and review metadata. Every enabled source has a reuse-status note and a matching row in [`source-rights.md`](source-rights.md). `readability` describes feed-summary length; `yield_estimate.items_per_week` is a dated estimate; and `cadence` is a proposed polling interval. Source enablement and feed access do not imply permission to retain article bodies.

## Verification

```sh
python3 dev/tools/registry_lint.py
python3 -m unittest -q dev.tests.source_registry_coverage_test dev.tests.sources_yield_test
```

These checks are offline and use the checked-in registry and fictional item fixtures.
