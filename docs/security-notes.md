# Security notes for the local briefing preview

The preview is a single-user, loopback application. API `GET` and `POST` routes reject non-loopback clients, and known `POST` routes have per-address sliding-window limits: interpretation 8/minute, generation 10/minute, episodes 10/minute (also capped at three accepted jobs per profile per Eastern day), corrections 10/minute, profile saves 60/minute, and episode actions 30/minute. Request bodies have route-specific byte caps; cancellation accepts no body. API requests and failures are not logged with their body or query values.

API request lines are omitted from the preview access log because query strings
may carry profile selectors. Domain error responses expose fixed error codes,
not exception messages; unexpected route exceptions become a generic
`request_failed` JSON response and are not re-raised into the server traceback
logger. `dev.tests.security_boundary_test` scans output from mocked fixture
requests containing topic, story, API-key, and profile sentinels to guard both
logs and error bodies.

Daily and episode job workers emit one-line JSON terminal summaries. They use
aggregate counts, durations, opaque job IDs, safe status/failure codes and
rounded cost metrics; they omit topic names, profile IDs, prompts, scripts,
source details and exception text. Keep changes to these logs allowlist-based.

## Outbound fetch boundary

Publisher/feed article reads use the shared HTTPS opener in `dev/ingest/core.py`. It requires an approved hostname, rejects credentials and nonstandard ports, resolves the host and refuses any non-public result (including loopback, private, link-local and metadata addresses), then connects to the checked IP while retaining TLS hostname verification. Each redirect is revalidated before a new connection. Redirect response bodies are discarded by closing the connection. A deadline timer closes slow responses, while callers impose response-size caps and retrieval budgets.

Search-provider JSON requests use the same pinned HTTPS boundary, disable redirects and cap response bodies. Gemini interpretation and TTS endpoints are fixed to `generativelanguage.googleapis.com`, use the pinned transport, disable redirects and cap JSON responses. No URL from a topic, article, feed or provider response is used as an authenticated API endpoint.

Tests mock DNS, connections and HTTP responses. They cover public/private mixed DNS answers, IPv4/IPv6 local and metadata targets, unsupported schemes, redirects, response timeouts and oversize bodies. Tests do not contact a provider.

## Untrusted text and prompt injection

Topic text, feed fields, article text, and retrieved snippets are data. Discovery and writer system instructions state this explicitly; profile interpretation removes instruction-like sentences before model submission. Candidate URLs are still mechanically checked and do not gain trust from model output. Writer claims must cite supplied evidence and pass local structural/support checks. No key is added to topic, article or feed prompt data; keys are sent only in headers to the fixed provider endpoint and are not returned or written to logs.

## Files and routes

Static files are served only from resolved files beneath `src/`, `styles/` and `content/`. Audition files must be direct children of the configured audition audio directory and use an allowed audio/manifest suffix. Episode audio is served from the state-managed episode audio store after job/profile/revision checks. Correction and episode identifiers have strict allowlisted formats; path resolution and audio tests reject encoded traversal. Unsupported methods cannot overwrite audition files.

`POST` JSON routes reject duplicate or malformed `Content-Length`, all
`Transfer-Encoding`, empty bodies where required, invalid JSON and oversize
bodies before dispatch. API query strings are limited to 4,096 characters and
64 fields before parsing. Response headers use fixed server values; request
headers, paths and query values are never copied into response headers. The
job API validates topic character sets, length and style; profile IDs are
derived server-side and do not come from request paths. The profile-specific
state directories and atomic file-write rules are documented with the episode
API.

## Scope and limits

These checks harden a local preview, not a public multi-user deployment. The two worker cap and request-rate counters are process-local. Do not bind the preview to a public interface or expose it through a tunnel. Provider generation is not run by these security tests; this branch was verified with fixtures and mocked transports only.

Run the focused boundary tests with:

```sh
python3 -m unittest -q dev.tests.ingest_test
python3 -m unittest -q dev.tests.interpreter_proxy_test
python3 -m unittest -q dev.tests.tts_provider_test
python3 -m unittest -q dev.tests.ingest_writer_test
python3 -m unittest -q dev.tests.security_boundary_test
```
