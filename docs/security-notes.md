# On-demand abuse and injection threat model

The topic box is an untrusted public input. The reader may be curious, careless,
or deliberately trying to make the service reveal internal data, spend money,
or publish a false claim. Retrieved pages are also untrusted: a page can contain
instructions aimed at a model instead of facts about its subject.

## Hostile inputs and controls

| Attempt | Boundary that must hold |
|---|---|
| A question says “ignore your instructions” or asks for a key, prompt, path, or configuration | Topic text is data and a scope constraint. Provider system prompts say that the topic and request context are untrusted data, never instructions. Output is limited to the requested episode schema; errors use safe public codes. |
| A fetched article contains model instructions, fake citations, or a request to repeat secrets | Article text is evidence data, never instructions. Claims require exact evidence IDs and deterministic plus semantic checks. Unsupported or off-topic sentences are removed or the episode falls back to an honest evidence-gap response. |
| Giant, malformed, Unicode, control-character, bidi, or surrogate input | Request validation rejects overlong UTF-16 input, controls, invalid Unicode and empty/non-alphanumeric topics before estimation, ledger preflight, job creation, or provider dispatch. |
| Repeated requests try to burn the daily budget | The per-reader daily episode limit is checked under the profile lock. The usage ledger preflights the recorded cost cap and records actual usage after each call. Idempotency and recent-request reuse avoid accidental duplicate work. Concurrent requests must not create more than the configured daily limit. |
| A request asks the model to present harmful or false content as cited fact | Evidence IDs, source existence, on-topic checks, unsupported-number/name checks, citation checks and safety checks remain hard publication gates. Style and length checks may warn, but never weaken grounding or safety. |
| An internal exception, path, key, or stack trace escapes to a reader | API handlers return stable public error codes and messages. Provider payloads and exception details are not serialized into API responses or API access logs. |

## Review checklist

- Keep prompt boundaries explicit whenever a new model call receives topic,
  request context, headlines, article text, or repair feedback.
- Treat source text as evidence to cite, never as an instruction block.
- Add a fixture for every new input boundary and assert that the public response
  contains no path, key, traceback, or raw hostile instruction.
- Keep all abuse tests fixture-only. Real provider testing is quality work and
  follows the normal ledger cap in the real-run plan.

This note describes public application boundaries only; it contains no host
paths, credentials, addresses, or private deployment details.
