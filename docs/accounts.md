# Accounts backend contract

Accounts are an opt-in launch foundation. `BRIEFING_ACCOUNTS=off` is the
default. With the flag off, the existing single-reader routes, JSON, cookies,
state layout and scheduled 5 am daily behavior remain unchanged; account routes
are unavailable and no account state is created.

## Threat and privacy boundaries

- Signup uses an operator-provided invite code. The service sends no email and
  contacts no external recipient.
- Passwords are stored only as `hashlib.scrypt` records with a per-account salt,
  explicit work parameters and a format version. Plain passwords never enter a
  log, response, job payload or ledger record.
- A successful login creates a random server-side session. The browser receives
  only an opaque session ID in an `HttpOnly; SameSite=Lax; Secure` cookie. The
  session store contains account ID, creation, expiry, last-seen and a CSRF
  secret. Login rotates the session ID, so a pre-login session cannot be fixed.
- Mutating account routes require the session's CSRF token in a header or body
  field. Cross-site requests without that token fail before state changes.
- Login attempts are limited independently by client IP and normalized account
  identifier. A lockout response is deliberately generic and does not reveal
  whether an account exists.
- Account data uses the existing profile identifier idea beneath a per-reader
  account directory. Profile, episode, library, correction and audio lookups
  always derive the reader ID from the authenticated session; request fields
  cannot select another reader's directory.
- Episode use is counted per reader and charged through the normal global usage
  ledger. The global daily and monthly caps remain authoritative; account
  attribution adds reporting, not a second way around those caps.

## JSON API contract

These routes exist only when `BRIEFING_ACCOUNTS=on`. Responses use stable public
error codes and never include filesystem paths, provider keys, stack traces or
password details.

| Method and route | Request | Success response |
|---|---|---|
| `POST /api/account/signup` | `{ "invite_code": "...", "account": "...", "password": "..." }` | `201 { "account": { "id": "...", "name": "..." }, "csrf_token": "..." }` plus a rotated session cookie |
| `POST /api/account/login` | `{ "account": "...", "password": "..." }` | `200 { "account": { "id": "...", "name": "..." }, "csrf_token": "..." }` plus a rotated session cookie |
| `POST /api/account/logout` | CSRF header required | `204` and expired session cookie |
| `GET /api/account/me` | session cookie | `200 { "account": { "id": "...", "name": "..." }, "usage": { ... } }` |
| `GET /api/account/csrf` | session cookie | `200 { "csrf_token": "..." }` |
| `GET /api/account/export` | session cookie | `200 { "account": { ... }, "profile": { ... }, "episodes": [ ... ], "library": [ ... ], "corrections": [ ... ], "usage": { ... } }` |
| `POST /api/account/delete` | `{ "confirm": "DELETE" }` plus CSRF header | `202 { "status": "deletion_scheduled" }`; the implementation must erase the account's state and invalidate every session before returning `204` or a final `200` status |

Account-scoped existing routes keep their current paths and JSON shapes. The
authenticated reader context is selected from the session, never from a
`profile` or account ID query parameter. When accounts are off, these routes
continue to use today's single-reader context exactly as they do now.

## Session and cookie rules

- Session IDs and CSRF tokens come from a cryptographically secure random
  source, are single-use on rotation, and expire after a bounded idle and
  absolute lifetime.
- Set `Path=/; HttpOnly; SameSite=Lax; Secure` on the session cookie. Do not
  expose the session ID to JavaScript or include it in URLs.
- Logout, password failure lockout, expiry and account deletion invalidate the
  server-side record. Deletion invalidates all sessions for that account.

## State, usage and deletion

The account index stores only the minimum account record and a password hash.
The reader directory holds that reader's profile, episodes, audio, library,
corrections and exportable usage attribution. Account export is a JSON snapshot
of that reader's own data and contains no password hash, session secret, invite
code or provider data. Delete removes the account record, sessions, reader
directory and account usage attribution; the global ledger keeps its aggregate
cost history needed for the service cap.

## Rollout and tests

The flag remains off until fixture tests cover wrong passwords, brute-force
limits, session rotation, CSRF, cookie attributes, two-reader route isolation,
export and full deletion. Tests use temporary state and fake generation/TTS;
they never send email, call a provider or touch the live single-reader state.
