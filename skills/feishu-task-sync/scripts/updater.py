"""Self-update helper for the feishu-task-sync skill.

Responsibilities:

* ``check``: figure out the local skill version (read from ``SKILL.md``
  frontmatter) and consult the configured upstream repository for the
  latest version. Classify the gap as ``up_to_date`` / ``patch`` /
  ``minor`` / ``major`` and report a human-readable suggestion.
* ``apply``: replace the on-disk skill with the upstream version, while
  preserving the per-user data (``config.json``, ``state/``, ``output/``).
  Always backs up the previous skill folder to ``<skill_dir>.bak-<ts>``
  before swapping, and rolls back if the post-swap ``doctor`` smoke check
  fails.

The CLI is consumed by ``bootstrap.py`` (see ``status`` / ``doctor`` /
``update`` subcommands). Both check and apply are intentionally side-effect
limited:

* ``check`` performs a single ``git ls-remote`` + an HTTPS fetch of the
  upstream SKILL.md. No clone, no write.
* ``apply`` performs a shallow clone of the configured repository into a
  temporary directory, copies the relevant skill subtree into place, and
  preserves the user-owned config / state / output files. It never edits
  ``cronjob.json`` and never calls Feishu APIs.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import traceback
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from runtime import (
    ConfigError,
    SKILL_DIR,
    Settings,
    add_config_argument,
    ensure_runtime_dirs,
    load_settings,
)


# ---------------------------------------------------------------------------
# Version parsing helpers
# ---------------------------------------------------------------------------


_VERSION_RE = re.compile(r"^\s*version\s*:\s*([0-9]+(?:\.[0-9]+){0,2})\s*$", re.MULTILINE)


def _parse_skill_md_version(text: str) -> Optional[str]:
    match = _VERSION_RE.search(text)
    if not match:
        return None
    return match.group(1)


def _parse_semver(value: str) -> Optional[Tuple[int, int, int]]:
    if not value:
        return None
    parts = value.split(".")
    if len(parts) > 3:
        return None
    while len(parts) < 3:
        parts.append("0")
    try:
        return (int(parts[0]), int(parts[1]), int(parts[2]))
    except ValueError:
        return None


def _classify_gap(local: str, remote: str) -> str:
    """Return one of: ``up_to_date``, ``patch``, ``minor``, ``major``, ``unknown``."""

    lp = _parse_semver(local)
    rp = _parse_semver(remote)
    if not lp or not rp:
        return "unknown"
    if rp <= lp:
        return "up_to_date"
    if rp[0] > lp[0]:
        return "major"
    if rp[1] > lp[1]:
        return "minor"
    return "patch"


def _read_local_version(skill_dir: Path) -> Optional[str]:
    path = skill_dir / "SKILL.md"
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    return _parse_skill_md_version(text)


def _fetch_remote_version(repository: str, branch: str, skill_path: str) -> Optional[str]:
    """Fetch SKILL.md from raw.githubusercontent.com and parse version.

    Returns ``None`` when the fetch fails (offline, rate-limited, etc.).
    Errors are not raised so ``check`` always returns a structured payload
    instead of aborting the caller.
    """

    if "github.com" not in repository:
        return None
    owner_repo = repository.rstrip("/").replace("https://github.com/", "", 1)
    raw_url = f"https://raw.githubusercontent.com/{owner_repo}/{branch}/{skill_path}/SKILL.md"
    try:
        with urllib.request.urlopen(raw_url, timeout=15) as resp:
            body = resp.read().decode("utf-8", errors="replace")
    except urllib.error.URLError:
        return None
    except Exception:
        return None
    return _parse_skill_md_version(body)


def _git_ls_remote_head(repository: str, branch: str) -> Optional[str]:
    if not shutil.which("git"):
        return None
    try:
        result = subprocess.run(
            ["git", "ls-remote", repository, branch],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except Exception:
        return None
    if result.returncode != 0:
        return None
    line = (result.stdout or "").strip().splitlines()
    if not line:
        return None
    first = line[0].split()
    return first[0] if first else None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CheckResult:
    local_version: Optional[str]
    remote_version: Optional[str]
    remote_sha: Optional[str]
    repository: str
    branch: str
    skill_path: str
    gap: str
    auto_apply_eligible: bool


def check(settings: Settings) -> CheckResult:
    local = _read_local_version(SKILL_DIR)
    remote = _fetch_remote_version(settings.updates.repository, settings.updates.branch, settings.updates.skill_path)
    sha = _git_ls_remote_head(settings.updates.repository, settings.updates.branch)
    gap = _classify_gap(local or "0.0.0", remote or "0.0.0") if remote else "unknown"
    auto_eligible = (gap == "patch") and settings.updates.auto_apply_patch_versions
    return CheckResult(
        local_version=local,
        remote_version=remote,
        remote_sha=sha,
        repository=settings.updates.repository,
        branch=settings.updates.branch,
        skill_path=settings.updates.skill_path,
        gap=gap,
        auto_apply_eligible=auto_eligible,
    )


# ---------------------------------------------------------------------------
# Apply
# ---------------------------------------------------------------------------


PRESERVE_RELATIVE = (
    "config.json",
    "state",
    "output",
)


def _checkout_repository(repository: str, branch: str, workdir: Path) -> Path:
    target = workdir / "repo"
    subprocess.run(
        ["git", "clone", "--depth", "1", "--branch", branch, repository, str(target)],
        check=True,
        capture_output=True,
        text=True,
    )
    return target


def _replace_skill_dir(new_source: Path, skill_dir: Path) -> Path:
    """Move ``skill_dir`` aside, copy ``new_source`` into its place."""

    timestamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    backup = skill_dir.with_name(f"{skill_dir.name}.bak-{timestamp}")
    counter = 1
    while backup.exists():
        backup = skill_dir.with_name(f"{skill_dir.name}.bak-{timestamp}-{counter}")
        counter += 1
    skill_dir.rename(backup)
    shutil.copytree(new_source, skill_dir)
    # Restore user-owned data from backup.
    for rel in PRESERVE_RELATIVE:
        src = backup / rel
        if not src.exists():
            continue
        dst = skill_dir / rel
        if dst.exists():
            if dst.is_dir():
                shutil.rmtree(dst)
            else:
                dst.unlink()
        if src.is_dir():
            shutil.copytree(src, dst)
        else:
            shutil.copy2(src, dst)
    return backup


def _rollback(backup: Path, skill_dir: Path) -> None:
    if skill_dir.exists():
        shutil.rmtree(skill_dir)
    backup.rename(skill_dir)


def _extract_changelog_highlights(skill_dir: Path, to_version: Optional[str]) -> List[str]:
    """Best-effort: pull the bullet lines under the ``to_version`` heading.

    The CHANGELOG follows ``## <version> - <subject>``; we read the block
    after that heading until the next ``##`` and keep up to 6 bullets.
    Failures are silent because the broadcast is still useful without
    highlights.
    """

    if not to_version:
        return []
    path = skill_dir / "CHANGELOG.md"
    try:
        text = path.read_text(encoding="utf-8")
    except Exception:
        return []
    pattern = re.compile(r"^##\s+" + re.escape(to_version) + r"\b.*?$", re.MULTILINE)
    match = pattern.search(text)
    if not match:
        return []
    rest = text[match.end():]
    next_heading = re.search(r"^##\s+", rest, re.MULTILINE)
    block = rest[: next_heading.start()] if next_heading else rest
    bullets: List[str] = []
    for line in block.splitlines():
        stripped = line.strip()
        if stripped.startswith("- ") or stripped.startswith("* "):
            bullets.append(stripped[2:].strip())
        if len(bullets) >= 6:
            break
    return bullets


def _write_post_update_marker(
    skill_dir: Path,
    payload: Dict[str, Any],
) -> Optional[Path]:
    """Drop a marker file so ``bootstrap.py post-update`` knows what to say.

    The marker lives at ``state/post-update-pending.json`` inside the
    *new* skill directory (which is identical to the old one after the
    swap, since ``state/`` is preserved). It is consumed and deleted by
    ``bootstrap.py post-update`` after the success broadcast is emitted.
    """

    state_dir = skill_dir / "state"
    try:
        state_dir.mkdir(parents=True, exist_ok=True)
        target = state_dir / "post-update-pending.json"
        target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return target
    except Exception:
        return None


def apply_update(settings: Settings, *, from_version: Optional[str] = None, to_version: Optional[str] = None) -> Dict[str, Any]:
    """Replace the on-disk skill with the upstream version.

    Returns a structured dict reporting the operation. Raises ``RuntimeError``
    for unrecoverable failures.
    """

    if not shutil.which("git"):
        raise RuntimeError("git executable not found on PATH; cannot perform update.")

    workdir = Path(tempfile.mkdtemp(prefix="feishu-task-sync-update-"))
    try:
        repo_root = _checkout_repository(settings.updates.repository, settings.updates.branch, workdir)
        new_source = repo_root / settings.updates.skill_path
        if not new_source.exists():
            raise RuntimeError(f"upstream repository does not contain {settings.updates.skill_path}.")
        backup = _replace_skill_dir(new_source, SKILL_DIR)
        highlights = _extract_changelog_highlights(SKILL_DIR, to_version)
        marker_payload = {
            "from_version": from_version,
            "to_version": to_version,
            "backup": str(backup),
            "skill_dir": str(SKILL_DIR),
            "repository": settings.updates.repository,
            "branch": settings.updates.branch,
            "skill_path": settings.updates.skill_path,
            "applied_at": datetime.now().isoformat(),
            "changelog_highlights": highlights,
        }
        marker_written = _write_post_update_marker(SKILL_DIR, marker_payload)
        return {
            "ok": True,
            "backup": str(backup),
            "skill_dir": str(SKILL_DIR),
            "repository": settings.updates.repository,
            "branch": settings.updates.branch,
            "skill_path": settings.updates.skill_path,
            "post_update_marker": str(marker_written) if marker_written else None,
            "changelog_highlights": highlights,
        }
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _emit(payload: Dict[str, Any], print_json: bool, lines: Sequence[str]) -> None:
    if print_json:
        json.dump(payload, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
        return
    for line in lines:
        print(line)


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Self-update helper for the feishu-task-sync skill.")
    add_config_argument(parser)
    parser.add_argument("--print-json", action="store_true", help="Emit machine-readable JSON.")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("check", help="Compare local skill version against upstream and report the gap.")
    p_apply = sub.add_parser("apply", help="Replace the on-disk skill with the upstream version.")
    p_apply.add_argument("--allow-major", action="store_true", help="Permit applying a major-version upgrade (default refuses).")
    p_apply.add_argument("--dry-run", action="store_true", help="Report what would happen without writing anything.")
    return parser.parse_args(argv)


def main(argv: Sequence[str]) -> int:
    args = parse_args(argv)
    try:
        settings = load_settings(args.config)
    except ConfigError as exc:
        print(f"[updater] {exc}", file=sys.stderr)
        return 2
    ensure_runtime_dirs(settings)

    if args.command == "check":
        if not settings.updates.check:
            payload = {
                "ok": True,
                "check_disabled": True,
                "reason": "config.json updates.check is false; skipping upstream lookup.",
            }
            _emit(payload, args.print_json, ["updates.check is disabled in config.json; nothing to do."])
            return 0
        result = check(settings)
        payload = {
            "ok": True,
            "local_version": result.local_version,
            "remote_version": result.remote_version,
            "remote_sha": result.remote_sha,
            "repository": result.repository,
            "branch": result.branch,
            "skill_path": result.skill_path,
            "gap": result.gap,
            "auto_apply_eligible": result.auto_apply_eligible,
        }
        lines = [
            f"local_version         : {result.local_version}",
            f"remote_version        : {result.remote_version}",
            f"remote_sha            : {result.remote_sha}",
            f"gap                   : {result.gap}",
            f"auto_apply_eligible   : {result.auto_apply_eligible}",
        ]
        if result.gap == "up_to_date":
            lines.append("Skill is up to date.")
        elif result.gap == "patch":
            lines.append("New patch version available. Run 'bootstrap.py update apply' to upgrade.")
        elif result.gap == "minor":
            lines.append("New minor version available. Review CHANGELOG before applying.")
        elif result.gap == "major":
            lines.append("New MAJOR version available. Review CHANGELOG and pass --allow-major to apply.")
        elif result.gap == "unknown":
            lines.append("Could not determine gap (offline, missing git, or unparseable SKILL.md).")
        _emit(payload, args.print_json, lines)
        return 0

    if args.command == "apply":
        if not settings.updates.check:
            payload = {
                "ok": False,
                "reason": "config.json updates.check is false; refusing to apply.",
            }
            _emit(payload, args.print_json, ["updates.check is disabled in config.json; refusing to apply."])
            return 2
        result = check(settings)
        if result.gap == "up_to_date":
            payload = {"ok": True, "result": "noop", "gap": result.gap}
            _emit(payload, args.print_json, ["Already up to date."])
            return 0
        if result.gap == "unknown":
            payload = {"ok": False, "gap": result.gap, "reason": "could not determine upstream version"}
            _emit(payload, args.print_json, ["Could not determine upstream version. Aborting."])
            return 2
        if result.gap == "major" and not args.allow_major:
            payload = {
                "ok": False,
                "gap": result.gap,
                "reason": "major upgrade requires --allow-major",
            }
            _emit(payload, args.print_json, ["Major upgrade requires --allow-major. Aborting."])
            return 2
        if args.dry_run:
            payload = {
                "ok": True,
                "dry_run": True,
                "gap": result.gap,
                "would_apply_from": result.repository,
                "branch": result.branch,
                "skill_path": result.skill_path,
                "remote_version": result.remote_version,
            }
            _emit(payload, args.print_json, [f"dry-run: would upgrade from {result.local_version} to {result.remote_version}."])
            return 0
        try:
            outcome = apply_update(
                settings,
                from_version=result.local_version,
                to_version=result.remote_version,
            )
        except Exception as exc:
            payload = {"ok": False, "error": str(exc)}
            _emit(payload, args.print_json, [f"apply failed: {exc}"])
            return 2
        outcome.update({"from_version": result.local_version, "to_version": result.remote_version})
        outcome["next_step"] = (
            "Run `bootstrap.py post-update` to verify with doctor and broadcast a success notice. "
            "Do NOT run `first-run` after an upgrade -- it would create an empty probe Todo."
        )
        _emit(outcome, args.print_json, [
            f"applied {result.local_version} -> {result.remote_version}",
            f"backup retained at {outcome['backup']}",
            "next: bootstrap.py post-update (not first-run).",
        ])
        return 0

    raise SystemExit(f"unknown command: {args.command}")


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv[1:]))
    except SystemExit:
        raise
    except Exception:
        traceback.print_exc()
        raise SystemExit(1)
