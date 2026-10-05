"""SQLite is canonical; JSON is metadata/export, never the state store."""
import json
import sqlite3
from pathlib import Path

from .models import Match, MatchStatus, Playlist, PlaylistKey, SourceTrack, TrackKey

SCHEMA = """
CREATE TABLE IF NOT EXISTS source_tracks (
 provider TEXT NOT NULL, id TEXT NOT NULL, title TEXT NOT NULL,
 artists TEXT NOT NULL, duration_ms INTEGER, isrc TEXT,
 liked INTEGER NOT NULL CHECK(liked IN (0,1)), PRIMARY KEY(provider,id)
);
CREATE TABLE IF NOT EXISTS matches (
 source_provider TEXT NOT NULL, source_id TEXT NOT NULL,
 target_provider TEXT NOT NULL, status TEXT NOT NULL CHECK(status IN ('matched','unmatched','retry')),
 target_id TEXT, reason TEXT NOT NULL, candidates TEXT NOT NULL, retry_at INTEGER,
 PRIMARY KEY(source_provider,source_id,target_provider),
 FOREIGN KEY(source_provider,source_id) REFERENCES source_tracks(provider,id),
 CHECK((status='matched' AND target_id IS NOT NULL AND target_id!='') OR
       (status!='matched' AND target_id IS NULL))
);
CREATE TABLE IF NOT EXISTS playlists (
 provider TEXT NOT NULL, id TEXT NOT NULL, name TEXT NOT NULL,
 PRIMARY KEY(provider,id)
);
CREATE TABLE IF NOT EXISTS playlist_membership (
 provider TEXT NOT NULL, playlist_id TEXT NOT NULL, position INTEGER NOT NULL CHECK(position>=0),
 track_provider TEXT NOT NULL, track_id TEXT NOT NULL,
 PRIMARY KEY(provider,playlist_id,position),
 FOREIGN KEY(provider,playlist_id) REFERENCES playlists(provider,id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS playlist_ownership (
 source_provider TEXT NOT NULL, source_id TEXT NOT NULL,
 target_provider TEXT NOT NULL, target_id TEXT NOT NULL,
 PRIMARY KEY(source_provider,source_id,target_provider), UNIQUE(target_provider,target_id),
 FOREIGN KEY(source_provider,source_id) REFERENCES playlists(provider,id),
 FOREIGN KEY(target_provider,target_id) REFERENCES playlists(provider,id),
 CHECK(source_provider!=target_provider)
);
CREATE TABLE IF NOT EXISTS target_likes (
 provider TEXT NOT NULL, track_id TEXT NOT NULL, PRIMARY KEY(provider,track_id)
);
CREATE TABLE IF NOT EXISTS sync_runs (
 id TEXT PRIMARY KEY, target_provider TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('running','complete','blocked')),
 core_blocked INTEGER NOT NULL CHECK(core_blocked IN (0,1)), retry_at INTEGER,
 http_status INTEGER, reason TEXT NOT NULL
);
"""


class Repository:
    def __init__(self, path: str | Path = ":memory:"):
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.executescript(SCHEMA)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.db.close()

    def put_track(self, track: SourceTrack) -> None:
        with self.db:
            self.db.execute("INSERT INTO source_tracks VALUES (?,?,?,?,?,?,?) "
                "ON CONFLICT(provider,id) DO UPDATE SET title=excluded.title,artists=excluded.artists,"
                "duration_ms=excluded.duration_ms,isrc=excluded.isrc,liked=excluded.liked",
                (track.key.provider, track.key.id, track.title, json.dumps(track.artists),
                 track.duration_ms, track.isrc, track.liked))

    def tracks(self, provider: str, *, liked_only: bool = False) -> tuple[SourceTrack, ...]:
        sql = "SELECT * FROM source_tracks WHERE provider=?"
        if liked_only:
            sql += " AND liked=1"
        rows = self.db.execute(sql + " ORDER BY id", (provider,))
        return tuple(SourceTrack(TrackKey(r['provider'], r['id']), r['title'],
            tuple(json.loads(r['artists'])), r['duration_ms'], r['isrc'], bool(r['liked'])) for r in rows)

    def put_match(self, match: Match) -> None:
        with self.db:
            self.db.execute("INSERT INTO matches VALUES (?,?,?,?,?,?,?,?) "
                "ON CONFLICT(source_provider,source_id,target_provider) DO UPDATE SET "
                "status=excluded.status,target_id=excluded.target_id,reason=excluded.reason,"
                "candidates=excluded.candidates,retry_at=excluded.retry_at",
                (match.source.provider, match.source.id, match.target_provider, match.status,
                 match.target_id, match.reason, json.dumps(match.candidates), match.retry_at))

    def get_match(self, key: TrackKey, target_provider: str) -> Match | None:
        r = self.db.execute("SELECT * FROM matches WHERE source_provider=? AND source_id=? AND target_provider=?",
            (key.provider, key.id, target_provider)).fetchone()
        if r is None:
            return None
        return Match(key, target_provider, MatchStatus(r['status']), r['target_id'],
                     r['reason'], tuple(json.loads(r['candidates'])), r['retry_at'])

    def unresolved(self, source_provider: str, target_provider: str) -> tuple[Match, ...]:
        keys = self.db.execute("SELECT source_id FROM matches WHERE source_provider=? AND "
            "target_provider=? AND status!='matched' ORDER BY source_id", (source_provider, target_provider))
        return tuple(self.get_match(TrackKey(source_provider, r['source_id']), target_provider) for r in keys)

    def put_playlist(self, playlist: Playlist) -> None:
        if any(t.provider != playlist.key.provider for t in playlist.tracks):
            raise ValueError("Playlist items must belong to the playlist provider")
        with self.db:
            self.db.execute("INSERT INTO playlists VALUES (?,?,?) ON CONFLICT(provider,id) "
                "DO UPDATE SET name=excluded.name", (playlist.key.provider, playlist.key.id, playlist.name))
            self.db.execute("DELETE FROM playlist_membership WHERE provider=? AND playlist_id=?",
                (playlist.key.provider, playlist.key.id))
            self.db.executemany("INSERT INTO playlist_membership VALUES (?,?,?,?,?)",
                [(playlist.key.provider, playlist.key.id, i, t.provider, t.id)
                 for i, t in enumerate(playlist.tracks)])

    def playlists(self, provider: str) -> tuple[Playlist, ...]:
        result = []
        for r in self.db.execute("SELECT * FROM playlists WHERE provider=? ORDER BY id", (provider,)):
            members = self.db.execute("SELECT track_provider,track_id FROM playlist_membership "
                "WHERE provider=? AND playlist_id=? ORDER BY position", (provider, r['id']))
            result.append(Playlist(PlaylistKey(provider, r['id']), r['name'],
                                  tuple(TrackKey(t[0], t[1]) for t in members)))
        return tuple(result)

    def own_playlist(self, source: PlaylistKey, target: PlaylistKey) -> None:
        # Conflicting ownership is rejected, never reassigned silently.
        with self.db:
            self.db.execute("INSERT INTO playlist_ownership VALUES (?,?,?,?) ON CONFLICT DO NOTHING",
                (source.provider, source.id, target.provider, target.id))
            actual = self.owned_target(source, target.provider)
            if actual != target:
                raise ValueError("Conflicting playlist ownership")

    def owned_target(self, source: PlaylistKey, target_provider: str) -> PlaylistKey | None:
        r = self.db.execute("SELECT target_id FROM playlist_ownership WHERE source_provider=? "
            "AND source_id=? AND target_provider=?", (source.provider, source.id, target_provider)).fetchone()
        return PlaylistKey(target_provider, r[0]) if r else None

    def owns(self, source: PlaylistKey, target: PlaylistKey) -> bool:
        return self.owned_target(source, target.provider) == target

    def set_target_likes(self, provider: str, ids: tuple[str, ...]) -> None:
        """Replace observed state locally; never a remote unlike operation."""
        with self.db:
            self.db.execute("DELETE FROM target_likes WHERE provider=?", (provider,))
            self.db.executemany("INSERT OR IGNORE INTO target_likes VALUES (?,?)", [(provider, i) for i in ids])

    def target_likes(self, provider: str) -> frozenset[str]:
        return frozenset(r[0] for r in self.db.execute("SELECT track_id FROM target_likes WHERE provider=?", (provider,)))

    def start_run(self, run_id: str, target_provider: str) -> None:
        with self.db:
            self.db.execute("INSERT INTO sync_runs VALUES (?,?,'running',0,NULL,NULL,'')", (run_id, target_provider))

    def block_core(self, run_id: str, *, observed_at: int, retry_after: int, reason: str = "429") -> None:
        if retry_after < 0:
            raise ValueError("Retry-After must not be negative")
        with self.db:
            cursor = self.db.execute("UPDATE sync_runs SET status='blocked',core_blocked=1,http_status=429,"
                "retry_at=max(coalesce(retry_at,0),?),reason=? WHERE id=?",
                (observed_at + retry_after, reason, run_id))
            if not cursor.rowcount:
                raise ValueError("Unknown sync run")

    def run(self, run_id: str) -> dict:
        r = self.db.execute("SELECT * FROM sync_runs WHERE id=?", (run_id,)).fetchone()
        if r is None:
            raise ValueError("Unknown sync run")
        return dict(r)

    def retry_deadline(self, target_provider: str) -> int | None:
        return self.db.execute("SELECT max(retry_at) FROM sync_runs WHERE target_provider=?",
                               (target_provider,)).fetchone()[0]

    def finish_run(self, run_id: str) -> None:
        with self.db:
            cursor = self.db.execute("UPDATE sync_runs SET status='complete' WHERE id=? AND core_blocked=0", (run_id,))
            if not cursor.rowcount:
                raise ValueError("Cannot complete an unknown or blocked run")
