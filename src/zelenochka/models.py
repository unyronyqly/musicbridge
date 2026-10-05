"""Small provider-neutral boundaries; no credentials or network implementations."""
from dataclasses import asdict, dataclass
from enum import IntEnum, StrEnum
from typing import Protocol


@dataclass(frozen=True, order=True)
class TrackKey:
    provider: str
    id: str


@dataclass(frozen=True)
class SourceTrack:
    key: TrackKey
    title: str
    artists: tuple[str, ...] = ()
    duration_ms: int | None = None
    isrc: str | None = None
    liked: bool = False


@dataclass(frozen=True, order=True)
class PlaylistKey:
    provider: str
    id: str


@dataclass(frozen=True)
class Playlist:
    key: PlaylistKey
    name: str
    tracks: tuple[TrackKey, ...]


class MatchStatus(StrEnum):
    MATCHED = "matched"
    UNMATCHED = "unmatched"
    RETRY = "retry"


@dataclass(frozen=True)
class Match:
    source: TrackKey
    target_provider: str
    status: MatchStatus
    target_id: str | None = None
    reason: str = ""
    candidates: tuple[str, ...] = ()
    retry_at: int | None = None

    def __post_init__(self):
        if (self.status == MatchStatus.MATCHED) != bool(self.target_id):
            raise ValueError("Only matched rows must have a target ID")


class Phase(IntEnum):
    LIKES = 10
    PLAYLISTS = 20
    INBOX = 30
    EXTRAS = 40


class ActionKind(StrEnum):
    ADD_LIKE = "ADD_LIKE"
    ADD_PLAYLIST_ITEM = "ADD_PLAYLIST_ITEM"
    REMOVE_PLAYLIST_ITEM = "REMOVE_PLAYLIST_ITEM"
    REPLACE_PLAYLIST_ORDER = "REPLACE_PLAYLIST_ORDER"
    NOOP = "NOOP"


@dataclass(frozen=True)
class Action:
    kind: ActionKind
    phase: Phase
    target_provider: str
    target_playlist_id: str | None = None
    target_track_id: str | None = None
    position: int | None = None
    desired_order: tuple[str, ...] = ()
    source_playlist_id: str | None = None
    reason: str = ""

    @property
    def destructive(self) -> bool:
        return self.kind in (ActionKind.REMOVE_PLAYLIST_ITEM, ActionKind.REPLACE_PLAYLIST_ORDER)

    def to_dict(self) -> dict:
        result = asdict(self)
        result["kind"] = self.kind.value
        result["phase"] = self.phase.name
        return result


@dataclass(frozen=True)
class Plan:
    actions: tuple[Action, ...]
    unresolved: tuple[TrackKey, ...] = ()
    suppressed_phases: tuple[Phase, ...] = ()
    retry_at: int | None = None

    def to_dict(self) -> dict:
        return {"actions": [a.to_dict() for a in self.actions],
                "unresolved": [asdict(k) for k in self.unresolved],
                "suppressed_phases": [p.name for p in self.suppressed_phases],
                "retry_at": self.retry_at}


class SourceProvider(Protocol):
    def tracks(self) -> tuple[SourceTrack, ...]: ...
    def playlists(self) -> tuple[Playlist, ...]: ...


class TargetProvider(Protocol):
    def liked_tracks(self) -> frozenset[TrackKey]: ...
    def playlists(self) -> tuple[Playlist, ...]: ...
