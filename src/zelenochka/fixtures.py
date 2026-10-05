"""Import synthetic provider snapshots into canonical SQLite state."""
import json
from pathlib import Path

from .models import Match, MatchStatus, Playlist, PlaylistKey, SourceTrack, TrackKey
from .repository import Repository


def load_fixture(directory: Path, repo: Repository) -> tuple[str, str, str, int]:
    data = json.loads((directory / "fixture.json").read_text(encoding="utf-8"))
    source = data['source_provider']
    target = data['target_provider']
    for t in data['tracks']:
        repo.put_track(SourceTrack(TrackKey(source, t['id']), t['title'],
            tuple(t.get('artists', [])), t.get('duration_ms'), t.get('isrc'), t.get('liked', False)))
    for m in data['matches']:
        repo.put_match(Match(TrackKey(source, m['source_id']), target, MatchStatus(m['status']),
            m.get('target_id'), m.get('reason', ''), tuple(m.get('candidates', [])), m.get('retry_at')))
    for provider, key in ((source, 'source_playlists'), (target, 'target_playlists')):
        for p in data[key]:
            repo.put_playlist(Playlist(PlaylistKey(provider, p['id']), p['name'],
                tuple(TrackKey(provider, t) for t in p['tracks'])))
    for p in data['ownership']:
        repo.own_playlist(PlaylistKey(source, p['source_id']), PlaylistKey(target, p['target_id']))
    repo.set_target_likes(target, tuple(data['target_likes']))
    run = data['run']
    repo.start_run(run['id'], target)
    if run.get('core_blocked', False):
        repo.block_core(run['id'], observed_at=run['observed_at'], retry_after=run['retry_after'])
    return run['id'], source, target, data['now']
