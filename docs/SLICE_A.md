# Slice A: SQLite state and deterministic planning

## Reproduce

After the README setup, run:

```sh
zelenochka plan --fixture tests/fixtures/basic
```

Expected summary: one `ADD_LIKE`, one `ADD_PLAYLIST_ITEM`, one
`REMOVE_PLAYLIST_ITEM`, one `REPLACE_PLAYLIST_ORDER`, one `NOOP`, and one
unresolved track. The JSON section includes ordered action fields and unresolved
source keys. No action is executed.

Other synthetic cases:

```sh
zelenochka plan --fixture tests/fixtures/settled
zelenochka plan --fixture tests/fixtures/blocked
```

`settled` represents the target **after confirmed execution** of the basic plan:
all actions are `NOOP`. `blocked` represents a core 429: no mutation actions,
lower-priority Spotify phases suppressed, retry timestamp **8190** (990 + 7200).

Running `plan` twice against an unchanged, unsatisfied target returns the same
plan, not a fictitious NOOP. Planning never marks proposed remote writes as done.
The idempotency test confirms a first plan into a local fake target and then
replans an identical source against that confirmed target.

## Public surface

- `models.py`: provider-neutral keys, tracks, playlists, match/retry state,
  read protocols, actions, phases and plans.
- `Repository(path)`: stdlib SQLite state. Omit the path for an in-memory store;
  pass a caller-selected local path for persistence. No default user folder.
- `plan(repo, run_id, source_provider=..., target_provider=..., now=...)`: a pure
  state read. The caller supplies observation time; the planner does not read the
  clock or call providers. IDs are sorted to make output independent of import order.
- `zelenochka plan --fixture <directory>`: import synthetic snapshots into
  in-memory SQLite and render the plan. There is intentionally no `apply` command.

SQLite stores source metadata, explicit match decisions/candidates, retry state,
observed target likes, ordered playlist occurrences, source→target ownership and
sync runs. Repository writes commit atomically per operation. A process-kill test
proves committed mappings survive and an interrupted uncommitted change rolls back.
The JSON fixtures are inputs, not canonical runtime state. The JSON plan is a report.

Unmatched/retry rows can be inspected with `Repository.unresolved(...)` and
corrected using `put_match(...)`; replacing a decision clears stale candidate/retry
fields supplied by the old row. No fuzzy matcher or manual-correction UI is built
in this slice. Unknown/unmatched tracks are reported and excluded from desired
matched playlist membership.

## Safety and action semantics

Likes have only `ADD_LIKE` and `NOOP`. Existing target-only likes remain untouched.
Several source tracks resolving to one target produce one like addition.

An owned mirror is a **whole playlist copy**, identified by its SQLite ownership
record, never by name. Source→target ownership is unique in both directions and
conflicting registration fails. Playlists without ownership yield a `NOOP`; this
slice does not create or adopt mirrors. Missing or mismatched ownership cannot
produce a removal or order replacement. Source playlists absent from the snapshot
are not automatically deleted; full provider snapshot lifecycle is a later slice.

The planner emits playlist removals in descending current occurrence positions,
then additions in ascending desired positions. Repeated tracks are preserved.
If this sequence still leaves a different order, `REPLACE_PLAYLIST_ORDER` carries
the entire desired sequence, including duplicates. Actions execute sequentially
within each playlist if a future executor is added. Positions are provider-neutral
occurrence positions; conversion to provider API payloads is not implemented here.
Any future executor must revalidate ownership and observation freshness before
performing destructive operations. The planner does not claim remote atomicity.

## Priority and Retry-After

`LIKES < PLAYLISTS < INBOX < EXTRAS`. Only likes and mirrors have planning logic in
Slice A. Inbox and extras are phase identifiers, not implementations.

`block_core(run_id, observed_at=..., retry_after=...)` stores the full deadline,
marks that run blocked, and prevents all later Spotify work in that run even after
the timer elapses. Repeated blocks never shorten a recorded deadline. A new run
still cannot plan writes before the provider's persisted deadline; it may plan at
or after it. A different provider's cooldown does not block Spotify. Nothing sleeps
or sends retries in this offline slice.

## Fixture format

Each directory contains one `fixture.json` with:

- `source_provider`, `target_provider`, fixed integer `now`;
- `tracks`: source `id`, `title`, optional `artists`, `liked`, `duration_ms`, `isrc`;
- `matches`: `source_id`, `status` (`matched`, `unmatched`, `retry`), optional
  `target_id`, `reason`, candidate IDs and `retry_at`;
- `source_playlists` / `target_playlists`: `id`, `name`, ordered track IDs;
- `ownership`: explicit `source_id`, `target_id` pairs;
- `target_likes`: observed target track IDs;
- `run`: `id`; for a core block, `core_blocked`, `observed_at`, `retry_after`.

The prior-art decision is already recorded in
[issue #2](https://github.com/unyronyqly/zelenochka/issues/2#issuecomment-5993708999).
This slice adapts the source-scoped SQLite ownership/mapping approach inspected in
[pkarpovich/playlist-synchronizer](https://github.com/pkarpovich/playlist-synchronizer),
with the stricter whole-owned-mirror boundary required by our architecture.
No upstream implementation is copied. Deadline behavior follows Spotify's
[rate-limit contract](https://developer.spotify.com/documentation/web-api/concepts/rate-limits).
There are no live Spotify/Yandex adapters to verify in this slice.
