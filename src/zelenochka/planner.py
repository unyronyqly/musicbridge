"""Deterministic planning only. No provider mutation or optimistic acknowledgments."""
from collections import Counter

from .models import Action, ActionKind as Kind, Phase, Plan, PlaylistKey
from .repository import Repository


def plan(repo: Repository, run_id: str, *, source_provider: str = "yandex",
         target_provider: str = "spotify", now: int = 0) -> Plan:
    run = repo.run(run_id)
    if run['target_provider'] != target_provider:
        raise ValueError("Run belongs to a different target provider")
    retry_at = repo.retry_deadline(target_provider)
    if run['core_blocked'] or (retry_at is not None and now < retry_at):
        return Plan((Action(Kind.NOOP, Phase.LIKES, target_provider, reason="core_rate_limited"),),
                    suppressed_phases=(Phase.PLAYLISTS, Phase.INBOX, Phase.EXTRAS), retry_at=retry_at)

    actions = []
    unresolved = set()
    likes = repo.target_likes(target_provider)
    scheduled_likes = set()
    for track in repo.tracks(source_provider, liked_only=True):
        match = repo.get_match(track.key, target_provider)
        if not match or not match.target_id:
            unresolved.add(track.key)
            continue
        if match.target_id not in likes and match.target_id not in scheduled_likes:
            actions.append(Action(Kind.ADD_LIKE, Phase.LIKES, target_provider, target_track_id=match.target_id))
            scheduled_likes.add(match.target_id)
    if not scheduled_likes:
        actions.append(Action(Kind.NOOP, Phase.LIKES, target_provider, reason="no_new_resolved_likes"))

    targets = {p.key: p for p in repo.playlists(target_provider)}
    for source in repo.playlists(source_provider):
        target = repo.owned_target(source.key, target_provider)
        if target is None:
            actions.append(Action(Kind.NOOP, Phase.PLAYLISTS, target_provider,
                source_playlist_id=source.key.id, reason="no_ownership_record"))
            continue
        desired = []
        for track in source.tracks:
            match = repo.get_match(track, target_provider)
            if match and match.target_id:
                desired.append(match.target_id)
            else:
                unresolved.add(track)
        current = [t.id for t in targets[target].tracks]
        common = dict(phase=Phase.PLAYLISTS, target_provider=target_provider,
                      source_playlist_id=source.key.id, target_playlist_id=target.id)
        if current == desired:
            actions.append(Action(Kind.NOOP, **common, reason="mirror_already_exact"))
            continue
        # Positions identify occurrences, preserving duplicates. Removals are descending
        # so offsets remain valid; additions use desired positions in ascending order.
        excess = Counter(current) - Counter(desired)
        removals = []
        kept = []
        for position, track_id in enumerate(current):
            if excess[track_id]:
                excess[track_id] -= 1
                removals.append(Action(Kind.REMOVE_PLAYLIST_ITEM, **common,
                                      target_track_id=track_id, position=position))
            else:
                kept.append(track_id)
        actions.extend(reversed(removals))
        available = Counter(kept)
        for position, track_id in enumerate(desired):
            if available[track_id]:
                available[track_id] -= 1
            else:
                actions.append(Action(Kind.ADD_PLAYLIST_ITEM, **common, target_track_id=track_id, position=position))
                kept.insert(position, track_id)
        if kept != desired:
            actions.append(Action(Kind.REPLACE_PLAYLIST_ORDER, **common, desired_order=tuple(desired)))
    # Fail closed on destructive actions; ownership is part of every mirror action.
    for action in actions:
        if action.destructive and not repo.owns(
            PlaylistKey(source_provider, action.source_playlist_id),
            PlaylistKey(target_provider, action.target_playlist_id)):
            raise ValueError("Destructive action without playlist ownership")
    return Plan(tuple(actions), tuple(sorted(unresolved)))
