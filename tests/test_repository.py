import os
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

from zelenochka.models import Match, MatchStatus, Playlist, PlaylistKey, SourceTrack, TrackKey
from zelenochka.repository import Repository


def test_persistence_round_trip(tmp_path):
    path = tmp_path / 'state.sqlite'
    key = TrackKey('source', 'track')
    source = Playlist(PlaylistKey('source', 'playlist'), 'Source', (key, key))
    target = Playlist(PlaylistKey('target', 'mirror'), 'Mirror', (TrackKey('target', 'resolved'),))
    with Repository(path) as repo:
        repo.put_track(SourceTrack(key, 'Synthetic', ('Artist',), 100000, 'TEST', True))
        repo.put_match(Match(key, 'target', MatchStatus.RETRY, reason='network', candidates=('candidate',), retry_at=5000))
        repo.put_playlist(source)
        repo.put_playlist(target)
        repo.own_playlist(source.key, target.key)
        repo.set_target_likes('target', ('resolved',))
        repo.start_run('run', 'target')
        repo.block_core('run', observed_at=1000, retry_after=4000)
    with Repository(path) as repo:
        assert repo.tracks('source')[0].duration_ms == 100000
        assert repo.get_match(key, 'target').retry_at == 5000
        assert repo.playlists('source') == (source,)
        assert repo.owns(source.key, target.key)
        assert repo.target_likes('target') == frozenset({'resolved'})
        assert repo.run('run')['core_blocked']
        assert repo.retry_deadline('target') == 5000


def test_conflicting_ownership_is_rejected(repo):
    source = PlaylistKey('yandex', 'source')
    with pytest.raises(ValueError):
        repo.own_playlist(source, PlaylistKey('spotify', 'unrelated'))
    with pytest.raises(ValueError):
        repo.own_playlist(PlaylistKey('yandex', 'unowned-source'), PlaylistKey('spotify', 'mirror'))
    assert repo.owned_target(source, 'spotify') == PlaylistKey('spotify', 'mirror')


def test_foreign_keys_reject_dangling_ownership(repo):
    with pytest.raises(sqlite3.IntegrityError):
        repo.own_playlist(PlaylistKey('yandex', 'missing'), PlaylistKey('spotify', 'unrelated'))


def test_match_needs_existing_source(repo):
    with pytest.raises(sqlite3.IntegrityError):
        repo.put_match(Match(TrackKey('yandex', 'nonexistent'), 'spotify', MatchStatus.MATCHED, 'sp'))


@pytest.mark.parametrize('status,target', [(MatchStatus.MATCHED, None), (MatchStatus.RETRY, 'sp')])
def test_match_status_and_target_consistent(status, target):
    with pytest.raises(ValueError):
        Match(TrackKey('source', 'id'), 'target', status, target)


def test_playlist_update_is_atomic(repo):
    old = repo.playlists('yandex')
    with pytest.raises(ValueError):
        repo.put_playlist(Playlist(old[0].key, 'Changed', (TrackKey('wrong-provider', 'id'),)))
    assert repo.playlists('yandex') == old


def test_provider_state_is_scoped(repo):
    repo.start_run('different-target', 'other')
    repo.block_core('different-target', observed_at=1000, retry_after=9000)
    assert repo.retry_deadline('spotify') is None
    from zelenochka.planner import plan
    assert not plan(repo, 'fixture-basic', now=1000).suppressed_phases
    with pytest.raises(ValueError):
        plan(repo, 'different-target', now=1000)


def test_invalid_run_transitions(repo):
    with pytest.raises(ValueError):
        repo.block_core('unknown', observed_at=1000, retry_after=60)
    with pytest.raises(ValueError):
        repo.block_core('fixture-basic', observed_at=1000, retry_after=-1)
    with pytest.raises(ValueError):
        repo.finish_run('unknown')


def test_killing_process_keeps_committed_work_and_rolls_back_partial_change(tmp_path):
    path = tmp_path / 'crash.sqlite'
    script = '''
import sys
from zelenochka.repository import Repository
from zelenochka.models import SourceTrack, TrackKey, Match, MatchStatus
repo = Repository(sys.argv[1])
key = TrackKey('source', 'resolved')
repo.put_track(SourceTrack(key, 'Fixture'))
repo.put_match(Match(key, 'target', MatchStatus.MATCHED, 'saved'))
repo.db.execute('BEGIN')
repo.db.execute("UPDATE matches SET target_id='not-committed'")
print('ready', flush=True)
sys.stdin.read()
'''
    env = {**os.environ, 'PYTHONPATH': str(Path(__file__).parents[1] / 'src')}
    child = subprocess.Popen([sys.executable, '-c', script, str(path)], env=env,
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        assert child.stdout.readline().strip() == 'ready'
        child.kill()
        child.wait(timeout=5)
        with Repository(path) as repo:
            assert repo.get_match(TrackKey('source', 'resolved'), 'target').target_id == 'saved'
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=5)
        child.stdin.close()
        child.stdout.close()
        child.stderr.close()
