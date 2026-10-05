import json
from pathlib import Path
import random

import pytest

from zelenochka.fixtures import load_fixture
from zelenochka.models import ActionKind as Kind, Match, MatchStatus, Phase, Playlist, PlaylistKey, TrackKey
from zelenochka.planner import plan
from zelenochka.repository import Repository

FIXTURES = Path(__file__).parent / 'fixtures'


def apply_locally(repo, result):
    """Test-only observed target update. Production has no action executor."""
    likes = set(repo.target_likes('spotify'))
    playlists = {p.key.id: p for p in repo.playlists('spotify')}
    tracks = {key: list(p.tracks) for key, p in playlists.items()}
    for a in result.actions:
        if a.kind == Kind.NOOP:
            continue
        if a.kind == Kind.ADD_LIKE:
            likes.add(a.target_track_id)
            continue
        assert repo.owns(PlaylistKey('yandex', a.source_playlist_id), PlaylistKey('spotify', a.target_playlist_id))
        members = tracks[a.target_playlist_id]
        if a.kind == Kind.REMOVE_PLAYLIST_ITEM:
            assert members[a.position].id == a.target_track_id
            members.pop(a.position)
        elif a.kind == Kind.ADD_PLAYLIST_ITEM:
            members.insert(a.position, TrackKey('spotify', a.target_track_id))
        elif a.kind == Kind.REPLACE_PLAYLIST_ORDER:
            tracks[a.target_playlist_id] = [TrackKey('spotify', i) for i in a.desired_order]
    repo.set_target_likes('spotify', tuple(sorted(likes)))
    for key, p in playlists.items():
        repo.put_playlist(Playlist(p.key, p.name, tuple(tracks[key])))


def test_second_identical_run_is_noop_after_confirmed_target_snapshot(repo):
    first = plan(repo, 'fixture-basic', now=1000)
    assert any(a.kind != Kind.NOOP for a in first.actions)
    apply_locally(repo, first)
    repo.finish_run('fixture-basic')
    repo.start_run('second', 'spotify')
    second = plan(repo, 'second', now=1001)
    assert all(a.kind == Kind.NOOP for a in second.actions)


def test_planning_does_not_optimistically_apply_work(repo):
    before = repo.db.total_changes
    first = plan(repo, 'fixture-basic', now=1000)
    assert repo.db.total_changes == before
    assert plan(repo, 'fixture-basic', now=1000).to_dict() == first.to_dict()


def test_unrelated_playlist_never_mutated_or_adopted_by_name(repo):
    before = repo.playlists('spotify')[1]
    result = plan(repo, 'fixture-basic', now=1000)
    assert not any(a.target_playlist_id == 'unrelated' for a in result.actions)
    assert any(a.reason == 'no_ownership_record' for a in result.actions)
    apply_locally(repo, result)
    assert repo.playlists('spotify')[1] == before


def test_removal_only_from_owned_mirror(repo):
    result = plan(repo, 'fixture-basic', now=1000)
    removals = [a for a in result.actions if a.kind == Kind.REMOVE_PLAYLIST_ITEM]
    assert [(a.target_playlist_id, a.target_track_id, a.position) for a in removals] == [('mirror', 'stale', 2)]
    apply_locally(repo, result)
    source = repo.playlists('yandex')[0]
    repo.put_playlist(Playlist(source.key, source.name, (TrackKey('yandex', 'a'), TrackKey('yandex', 'b'))))
    second = plan(repo, 'fixture-basic', now=1000)
    assert [(a.target_playlist_id, a.target_track_id) for a in second.actions
            if a.kind == Kind.REMOVE_PLAYLIST_ITEM] == [('mirror', 'sp-c')]


def test_order_change_is_deterministic_and_exact(repo):
    result = plan(repo, 'fixture-basic', now=1000)
    reorder = [a for a in result.actions if a.kind == Kind.REPLACE_PLAYLIST_ORDER]
    assert [a.desired_order for a in reorder] == [('sp-a', 'sp-c', 'sp-b')]
    assert result.to_dict() == plan(repo, 'fixture-basic', now=1000).to_dict()
    apply_locally(repo, result)
    assert [t.id for t in repo.playlists('spotify')[0].tracks] == ['sp-a', 'sp-c', 'sp-b']


def test_existing_likes_never_scheduled_for_removal(repo):
    result = plan(repo, 'fixture-basic', now=1000)
    assert [a.target_track_id for a in result.actions if a.kind == Kind.ADD_LIKE] == ['sp-a']
    assert all(a.kind in (Kind.ADD_LIKE, Kind.NOOP) for a in result.actions if a.phase == Phase.LIKES)
    apply_locally(repo, result)
    assert {'sp-b', 'spotify-only'} <= repo.target_likes('spotify')


def test_core_429_suppresses_later_phases_and_preserves_full_retry_after(repo):
    repo.block_core('fixture-basic', observed_at=1000, retry_after=7200)
    result = plan(repo, 'fixture-basic', now=1001)
    assert [(a.kind, a.phase) for a in result.actions] == [(Kind.NOOP, Phase.LIKES)]
    assert result.suppressed_phases == (Phase.PLAYLISTS, Phase.INBOX, Phase.EXTRAS)
    assert result.retry_at == 8200
    # A core block ends this run, even once the wait has elapsed.
    assert plan(repo, 'fixture-basic', now=9000).suppressed_phases
    repo.start_run('next', 'spotify')
    assert plan(repo, 'next', now=8199).suppressed_phases
    assert not plan(repo, 'next', now=8200).suppressed_phases


def test_shorter_repeated_retry_does_not_shorten_deadline(repo):
    repo.block_core('fixture-basic', observed_at=1000, retry_after=7200)
    repo.block_core('fixture-basic', observed_at=1001, retry_after=1)
    assert repo.run('fixture-basic')['retry_at'] == 8200
    with pytest.raises(ValueError):
        repo.finish_run('fixture-basic')


def test_every_destructive_action_has_persisted_ownership(repo):
    for a in plan(repo, 'fixture-basic', now=1000).actions:
        if a.destructive:
            assert repo.owns(PlaylistKey('yandex', a.source_playlist_id), PlaylistKey('spotify', a.target_playlist_id))


def test_no_destructive_action_when_ownership_absent(repo):
    with repo.db:
        repo.db.execute('DELETE FROM playlist_ownership')
    assert not any(a.destructive for a in plan(repo, 'fixture-basic', now=1000).actions)


def test_empty_owned_source_clears_only_mirror(repo):
    source = repo.playlists('yandex')[0]
    repo.put_playlist(Playlist(source.key, source.name, ()))
    result = plan(repo, 'fixture-basic', now=1000)
    removals = [a for a in result.actions if a.kind == Kind.REMOVE_PLAYLIST_ITEM]
    assert [a.position for a in removals] == [2, 1, 0]
    assert all(a.target_playlist_id == 'mirror' for a in removals)
    apply_locally(repo, result)
    assert not repo.playlists('spotify')[0].tracks
    assert repo.playlists('spotify')[1].tracks == (TrackKey('spotify', 'manual'),)


def test_unmatched_is_inspectable_and_correctable(repo):
    match = repo.unresolved('yandex', 'spotify')[0]
    assert match.reason == 'ambiguous'
    assert match.candidates == ('candidate-1', 'candidate-2')
    assert match.source in plan(repo, 'fixture-basic', now=1000).unresolved
    repo.put_match(Match(match.source, 'spotify', MatchStatus.MATCHED, 'verified-manual'))
    assert not repo.unresolved('yandex', 'spotify')
    assert any(a.target_track_id == 'verified-manual' for a in plan(repo, 'fixture-basic', now=1000).actions)


def test_retry_match_is_not_silently_accepted(repo):
    repo.put_match(Match(TrackKey('yandex', 'a'), 'spotify', MatchStatus.RETRY,
                         reason='transient search error', retry_at=2000))
    result = plan(repo, 'fixture-basic', now=1000)
    assert TrackKey('yandex', 'a') in result.unresolved
    assert not any(a.kind == Kind.ADD_LIKE and a.target_track_id == 'sp-a' for a in result.actions)


@pytest.mark.parametrize('name', ['basic', 'settled', 'blocked'])
def test_documented_fixtures_are_deterministic(name):
    plans = []
    for _ in range(2):
        with Repository() as repo:
            run, source, target, now = load_fixture(FIXTURES / name, repo)
            plans.append(plan(repo, run, source_provider=source, target_provider=target, now=now).to_dict())
    assert plans[0] == plans[1]
    if name == 'settled':
        assert all(a['kind'] == 'NOOP' for a in plans[0]['actions'])


def test_reconciliation_preserves_duplicate_occurrences(repo):
    source = repo.playlists('yandex')[0]
    target = repo.playlists('spotify')[0]
    rng = random.Random(42)
    # Synthetic transition cases check semantics, not just implementation shape.
    for _ in range(80):
        desired = [rng.choice('abc') for _ in range(rng.randrange(7))]
        current = [rng.choice(['sp-a', 'sp-b', 'sp-c', 'extra']) for _ in range(rng.randrange(7))]
        repo.put_playlist(Playlist(source.key, source.name, tuple(TrackKey('yandex', i) for i in desired)))
        repo.put_playlist(Playlist(target.key, target.name, tuple(TrackKey('spotify', i) for i in current)))
        result = plan(repo, 'fixture-basic', now=1000)
        apply_locally(repo, result)
        assert [t.id for t in repo.playlists('spotify')[0].tracks] == ['sp-' + i for i in desired]
        assert all(a.kind == Kind.NOOP for a in plan(repo, 'fixture-basic', now=1000).actions)
