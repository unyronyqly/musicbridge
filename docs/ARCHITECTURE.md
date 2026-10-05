# Architecture

## Goal

MusicBridge is a local-first synchronization layer between Yandex Music and Spotify. It should feel like a one-time setup, not a recurring migration chore.

## Authority model

Yandex Music is the primary taste/library source.

### Tracks

- Yandex liked track → Spotify Liked Songs: **add-only**
- Spotify liked track absent from Yandex → Yandex private `Spotify Inbox`
- Never auto-delete a liked track on either side in v0.1

This asymmetry protects users from accidental loss and from Yandex liked-library capacity constraints.

### Playlists

Yandex playlists may have MusicBridge-owned Spotify mirrors.

For those mirrors only:
- preserve track order
- add new tracks
- remove tracks removed from the Yandex source
- never mutate unrelated Spotify playlists
- identify ownership by persisted source→target mapping, not playlist name alone

### Albums and artists

Yandex liked albums and followed artists → Spotify add-only.

These are low-priority operations and must yield immediately when Spotify quota is constrained.

## Local state

All mutable state stays on the user's machine:
- auth tokens / secrets
- source snapshots
- source↔target track mappings
- playlist ownership mappings
- unmatched candidates
- retry state
- logs

No secret or personal library data belongs in GitHub.

## Sync phases

1. Read Yandex snapshot.
2. Resolve and save Yandex liked tracks into Spotify.
3. Reconcile MusicBridge-owned playlist mirrors.
4. Populate Yandex `Spotify Inbox` from Spotify-only likes.
5. Opportunistically sync albums/artists.

Priority is strict. A Spotify 429 in a higher-priority phase prevents lower-priority Spotify work in that run.

## Reliability rules

- Every bulk operation is resumable.
- Persist mapping/progress before or immediately after remote mutation as appropriate.
- Respect `Retry-After`.
- Never spin aggressively on 429.
- A partial run is normal state, not corruption.
- Exact playlist reconciliation must be idempotent.
- Wrong matches must be correctable without rebuilding the entire database.

## Matching

Preferred signals, strongest first when available:
1. ISRC
2. canonical artist + title
3. duration
4. normalized/fuzzy title
5. transliteration-aware artist/title

Never silently accept a low-confidence match merely to maximize coverage. Unmatched is better than wrong.

## Product surface

Target UX is a small macOS-first local app:
- setup status
- source/target account state
- progress counts
- current phase
- next retry / quota cooldown
- Sync now
- Pause / Resume
- Open logs
- Open data folder
- Update / Repair
- Uninstall

CLI remains an engine/debug surface, not the primary UX.

## Prior art policy

Before implementation, inspect relevant open-source projects and current Spotify/Yandex API behavior.

Decision vocabulary:
- **REUSE** — adopt directly
- **WRAP** — keep upstream mostly intact behind our interface
- **ADAPT** — borrow implementation with changes
- **REFERENCE** — learn from it, do not ship it
- **BUILD** — no suitable prior art

Default order: **BORROW → ADAPT → BUILD**.
