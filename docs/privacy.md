# Your profile data

The app keeps your written interests and generated material in its local state folder. Reading signals and saved reading progress stay in your browser. The profile export and delete endpoints are available only from this machine over the loopback preview server; they do not use an account or send data to another service.

## Export a profile

Send `GET /api/profile/export`. The JSON includes the saved profile and interests, edition history metadata, episode metadata, and a `confirmation_token` for a later delete request. Episode and edition bodies, source URLs, credentials, and API keys are not included.

Reading signals live in browser storage, so the server cannot read them by itself. To include them in the export, send the browser's signal array as JSON in the `X-Briefing-Local-Signals` request header. It accepts at most 1,000 events and 16 KB. The server checks each event and returns only its timestamp, type, story ID, and topic ID. If the header is omitted, the export says `local_signals_included: false` and returns an empty array.

The export token is valid for ten minutes and is replaced by a newer export for the same profile. Keep the export file private: it contains the interests you chose to save.

## Delete a profile

Send `POST /api/profile/delete` with JSON containing the fresh confirmation token:

```json
{"confirmation_token":"token-from-the-profile-export"}
```

Deletion removes that profile's saved server profile, generated edition history, episode jobs and audio, and correction records. It refuses to run while that profile has an active generation or episode job. It does not write an audit record. A successful response lists the `briefing:` browser-storage prefix and session-storage keys the browser should clear. The server cannot clear browser storage for you; the browser must remove those keys after receiving success. Browser signals are not uploaded by deletion.

If the active profile changed after export, or the token is missing, expired, or already used, deletion stops without removing the profile. Export again to confirm the current profile and receive a fresh token.
