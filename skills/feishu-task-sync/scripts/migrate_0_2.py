#!/usr/bin/env python3
"""One-off migration helper for feishu-task-sync 0.1.x → 0.2.0.

The 0.2.0 layout stores state, output, cache, OAuth tokens, and cron logs
under the Skill directory (``<SKILL_DIR>/state`` and ``<SKILL_DIR>/output``).
Earlier versions stored everything under the maintainer's main-agent
workspace::

    <main-agent>/tools/feishu-task-sync/state/...
    <main-agent>/tools/feishu-task-sync/output/...

This script copies legacy data into the Skill-owned layout before flipping
``cronjob.json`` to point at the new paths.

Default behaviour is a *dry-run*: nothing is written unless ``--commit`` is
passed. When committing, every destination file already present is backed up
to ``<dest>.bak-<timestamp>`` first so the migration can be rolled back by
inspection.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Tuple

from runtime import (
    ConfigError,
    Settings,
    add_config_argument,
    ensure_runtime_dirs,
    load_settings,
)


@dataclass
class CopyPlan:
    label: str
    source: Path
    destination: Path
    kind: str  # "file" | "directory"


def _gather_legacy_root(settings: Settings, override: Optional[Path]) -> Path:
    if override is not None:
        return override
    return settings.paths.agent_root / "tools" / "feishu-task-sync"


def _make_plan(legacy_root: Path, settings: Settings) -> List[CopyPlan]:
    plans: List[CopyPlan] = []

    legacy_state = legacy_root / "state"
    new_state = settings.paths.state_dir
    if legacy_state.exists():
        for name in ("state.json", "sync-cursor.json", "user-auth.json"):
            src = legacy_state / name
            if src.exists():
                plans.append(
                    CopyPlan(
                        label=f"state/{name}",
                        source=src,
                        destination=new_state / name,
                        kind="file",
                    )
                )

    legacy_output = legacy_root / "output"
    new_output = settings.paths.output_dir
    if legacy_output.exists():
        for name in (
            "latest-report.json",
            "latest-report.md",
            "cron.log",
        ):
            src = legacy_output / name
            if src.exists():
                plans.append(
                    CopyPlan(
                        label=f"output/{name}",
                        source=src,
                        destination=new_output / name,
                        kind="file",
                    )
                )
        for name in ("collected", "feishu-chat-cache", "todos"):
            src = legacy_output / name
            if src.exists():
                plans.append(
                    CopyPlan(
                        label=f"output/{name}/",
                        source=src,
                        destination=new_output / name,
                        kind="directory",
                    )
                )

    return plans


def _ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def _backup_existing(destination: Path, timestamp: str) -> Optional[Path]:
    if not destination.exists():
        return None
    backup = destination.with_name(f"{destination.name}.bak-{timestamp}")
    counter = 1
    while backup.exists():
        backup = destination.with_name(f"{destination.name}.bak-{timestamp}-{counter}")
        counter += 1
    return backup


def _copy_file(plan: CopyPlan, timestamp: str) -> Tuple[str, Optional[Path]]:
    _ensure_parent(plan.destination)
    backup_target = _backup_existing(plan.destination, timestamp)
    if backup_target is not None:
        plan.destination.rename(backup_target)
    shutil.copy2(plan.source, plan.destination)
    return ("copied", backup_target)


def _copy_directory(plan: CopyPlan, timestamp: str) -> Tuple[str, Optional[Path]]:
    backup_target = _backup_existing(plan.destination, timestamp)
    if backup_target is not None:
        plan.destination.rename(backup_target)
    plan.destination.mkdir(parents=True, exist_ok=True)
    for entry in plan.source.iterdir():
        target = plan.destination / entry.name
        if entry.is_dir():
            if target.exists():
                shutil.rmtree(target)
            shutil.copytree(entry, target)
        else:
            shutil.copy2(entry, target)
    return ("copied", backup_target)


def _file_summary(path: Path) -> str:
    try:
        stat = path.stat()
    except FileNotFoundError:
        return "missing"
    if path.is_dir():
        files = sum(1 for _ in path.rglob("*") if _.is_file())
        return f"dir: {files} files"
    return f"{stat.st_size} bytes, mtime={datetime.fromtimestamp(stat.st_mtime).isoformat(timespec='seconds')}"


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Migrate feishu-task-sync 0.1.x state/output into the Skill directory.")
    add_config_argument(parser)
    parser.add_argument(
        "--legacy-root",
        default=None,
        help=(
            "Override the legacy root (default: "
            "<settings.paths.agent_root>/tools/feishu-task-sync)."
        ),
    )
    parser.add_argument(
        "--commit",
        action="store_true",
        help="Actually perform the migration. Default is dry-run (no writes).",
    )
    parser.add_argument(
        "--print-json",
        action="store_true",
        help="Print the migration plan/result as JSON instead of a human report.",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str]) -> int:
    args = parse_args(argv)
    settings = load_settings(args.config)
    ensure_runtime_dirs(settings)
    legacy_root = _gather_legacy_root(settings, Path(args.legacy_root) if args.legacy_root else None)
    plans = _make_plan(legacy_root, settings)
    timestamp = datetime.now().strftime("%Y%m%dT%H%M%S")

    summary: List[dict] = []
    for plan in plans:
        record = {
            "label": plan.label,
            "source": str(plan.source),
            "source_info": _file_summary(plan.source),
            "destination": str(plan.destination),
            "destination_existed": plan.destination.exists(),
        }
        if args.commit:
            if plan.kind == "file":
                outcome, backup = _copy_file(plan, timestamp)
            else:
                outcome, backup = _copy_directory(plan, timestamp)
            record["outcome"] = outcome
            record["backup"] = str(backup) if backup else None
        summary.append(record)

    payload = {
        "legacy_root": str(legacy_root),
        "skill_state_dir": str(settings.paths.state_dir),
        "skill_output_dir": str(settings.paths.output_dir),
        "config_path": str(settings.config_path),
        "config_source": settings.config_source,
        "commit": bool(args.commit),
        "timestamp": timestamp,
        "plans": summary,
    }

    if args.print_json:
        json.dump(payload, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
    else:
        print(f"legacy_root         : {payload['legacy_root']}")
        print(f"skill_state_dir     : {payload['skill_state_dir']}")
        print(f"skill_output_dir    : {payload['skill_output_dir']}")
        print(f"config_path         : {payload['config_path']} (source={payload['config_source']})")
        print(f"commit              : {payload['commit']}")
        print(f"timestamp           : {payload['timestamp']}")
        if not plans:
            print("nothing to migrate.")
        for item in summary:
            print()
            print(f"- {item['label']}")
            print(f"    source     : {item['source']} ({item['source_info']})")
            print(f"    destination: {item['destination']} (existed={item['destination_existed']})")
            if 'outcome' in item:
                print(f"    outcome    : {item['outcome']}")
                if item.get('backup'):
                    print(f"    backup     : {item['backup']}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv[1:]))
    except SystemExit:
        raise
    except ConfigError as exc:
        print(f"[migrate_0_2] config error: {exc}", file=sys.stderr)
        raise SystemExit(2)
    except Exception:
        import traceback

        traceback.print_exc()
        raise SystemExit(1)
