"""Offline fixture planner; intentionally no apply command."""
import argparse
from collections import Counter
import json
from pathlib import Path
import sqlite3

from .fixtures import load_fixture
from .planner import plan
from .repository import Repository


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="zelenochka", description="Offline sync planning; no remote writes")
    commands = parser.add_subparsers(dest="command", required=True)
    planner = commands.add_parser("plan", help="Plan a synthetic fixture snapshot")
    planner.add_argument("--fixture", required=True, type=Path, help="Directory containing fixture.json")
    args = parser.parse_args(argv)
    try:
        with Repository() as repo:
            run, source, target, now = load_fixture(args.fixture, repo)
            result = plan(repo, run, source_provider=source, target_provider=target, now=now)
    except (OSError, ValueError, KeyError, TypeError, sqlite3.Error) as exc:
        parser.exit(2, f"Fixture error: {exc}\n")
    print(f"Зелёночка — offline plan: {source} -> {target}")
    print("No remote writes. Planning does not mark work as applied.")
    for kind, count in sorted(Counter(a.kind.value for a in result.actions).items()):
        print(f"  {kind}: {count}")
    print(f"Unresolved tracks: {len(result.unresolved)}")
    if result.suppressed_phases:
        print("Suppressed phases: " + ", ".join(p.name for p in result.suppressed_phases))
        print(f"Retry not before: {result.retry_at}")
    print("\nJSON plan")
    print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2, sort_keys=True))
    return 0
