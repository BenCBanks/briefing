# Launch readiness

The accounts flag stays off until the operator has reviewed each item below.
This checklist describes the remaining work before real readers can sign up.

## Service and cost controls

- [ ] Run the daily edition for many reader profiles. Each reader needs an
  isolated six o'clock run and the shared global daily and monthly ledger caps
  must still win when demand is high.
- [ ] Exercise rapid signup, login and episode requests against the deployed
  service. Confirm rate limits, the per-reader episode limit and global cap
  responses are plain and recover at the next midnight ET boundary.
- [ ] Decide how much operator review is required for abuse reports, harmful
  requests and suspected automated signup.

## Transport and storage

- [ ] Put the preview/API behind HTTPS. The trusted proxy must send
  `X-Forwarded-Proto: https` only on the private proxy path, with
  `BRIEFING_TRUST_PROXY_HEADERS=on`; direct HTTP must not receive a `Secure`
  cookie.
- [ ] Back up per-reader state, including profiles, episode jobs, editions,
  corrections and audio. Test restoring one reader without exposing another.
- [ ] Confirm retention and deletion procedures remove reader state and all
  sessions while preserving only aggregate ledger history needed for caps.

## Reader-facing policy

- [ ] Publish a privacy page after the owner reviews its data-use wording.
- [ ] Publish terms of use after the owner reviews acceptable-use and content
  boundaries.
- [ ] Choose the password-reset policy. Until email delivery is approved,
  password reset is operator-assisted and no reset email is sent.

## Final gate

- [ ] Enable `BRIEFING_ACCOUNTS=on` only after the account API, route-isolation,
  export/delete, invite-tool and HTTPS checks are green in the deployed
  environment. Keep a rollback that sets the flag to `off`.
