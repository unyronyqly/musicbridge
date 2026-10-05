# MusicBridge

Local-first bridge between **Yandex Music** and **Spotify**.

MusicBridge exists because moving a music library should not require a chain of SaaS subscriptions, manual playlist clicking, or starting a recommendation profile from zero.

## Product contract

- **Yandex liked tracks → Spotify Liked Songs:** add-only. MusicBridge never auto-unlikes existing Spotify tracks.
- **Yandex playlists → Spotify:** exact mirror for MusicBridge-owned mirror playlists only. Unrelated Spotify playlists are never mutated.
- **Spotify-only liked tracks → Yandex:** collected into a private `Spotify Inbox` playlist instead of consuming Yandex's liked-track capacity.
- **Yandex liked albums / followed artists → Spotify:** add-only, low-priority, quota-aware.
- **Local cumulative archive:** keeps historical snapshots and mappings locally so sync state is not trapped in either service.
- **Resumable:** rate limits, restarts, and partial failures must never force a full restart.
- **Local secrets:** OAuth tokens, client secrets, state databases, logs, and personal library data stay off GitHub.

## Status

Early alpha. The current prototype has already authenticated both services and read a Yandex library containing 8,750 liked tracks. The current engineering focus is a robust resumable sync core and a clear macOS UX.

## Project doctrine

**Prior art first. BORROW → ADAPT → BUILD.**

Do not build a new subsystem until existing implementations have been checked and either reused, wrapped, or rejected with a concrete reason.

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) and the GitHub Control Room issue for current decisions.

## Security

Never commit:
- `.env`
- Spotify client secrets or refresh tokens
- Yandex OAuth tokens
- local sync databases / mappings
- logs
- exported personal library snapshots

## License

MIT.
