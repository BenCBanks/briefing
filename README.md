# Personal briefing

A web app that turns any question into a short, cited, spoken episode, plus a
short daily briefing on the topics you follow.

- **On demand:** type any topic or question, pick a length (3, 5 or 10
  minutes) and get a narrated episode with a matching transcript and sources.
- **Daily:** every morning, a short briefing on your selected topics with
  developments you have not heard yet. Recent is preferred, not required; it is
  not a breaking-news service.

The bundled sample content in `content/` is fictional or illustrative.

## Run it locally

```sh
python3 dev/preview_server.py 127.0.0.1 8898
```

Open <http://127.0.0.1:8898>. The server binds to loopback only.

## Tests

```sh
mkdir -p /tmp/briefing-test/ledger
BRIEFING_TESTING=1 BRIEFING_EPISODES=fake \
BRIEFING_STATE_DIR=/tmp/briefing-test/state \
BRIEFING_TEST_LEDGER_DIR=/tmp/briefing-test/ledger \
python3 -m unittest discover -s dev/tests -p '*_test.py' -t .
```

Tests use fictional fixtures and mocks only; they never call a paid provider.
The same suite runs in GitHub Actions on every pull request.

## Layout

| Path | What |
|---|---|
| `index.html`, `src/`, `styles/` | the web app |
| `dev/preview_server.py` | the HTTP server and API |
| `dev/ingest/` | sourcing, writing, narration and audio pipeline |
| `dev/tests/` | tests and fixtures |
| `content/` | sample content and the source registry |

More detail: [architecture](docs/architecture.md), [runbook](docs/runbook.md),
[cost control](docs/cost-control.md), [privacy](docs/privacy.md),
[sources](docs/sources.md).

Reader profiles may opt into local sources by listing their IDs in `local_sources`. Define those sources
outside this public repository with `BRIEFING_LOCAL_SOURCES`, or place `local-sources.json` in
`BRIEFING_DATA_DIR` (default: `~/.local/share/briefing/local-sources.json`). When no file exists, the app
loads no local sources. [The fictional example](content/local-sources.example.json) shows the format.
Each reader's topic definition supplies its own place and subject.
