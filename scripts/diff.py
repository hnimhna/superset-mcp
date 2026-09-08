#!/usr/bin/env python
# Licensed under the Apache License, Version 2.0
"""Compare Git (desired state) against a live Superset (actual state).

    python scripts/diff.py --env prod
    python scripts/diff.py --env prod --fail-on-drift   # CI gate

Statuses reported per asset:

    ADDED       in Git, absent from Superset      -> deploy would create it
    DELETED     in Superset, absent from Git      -> made outside GitOps
    DRIFT       exists in both, contents differ   -> Superset was edited directly
    UNCHANGED   identical

MODIFIED is reported instead of DRIFT when the local working tree differs from
the committed assets: the change originated in Git and is on its way out, which
is a normal pull-request state rather than a governance problem.

Nothing here writes to Superset. Reconciling drift is a deliberate act: either
export it into Git (accept the change) or redeploy from Git (discard it).
"""

from __future__ import annotations

import argparse
import logging
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.lib.environment import (  # noqa: E402
    MissingSecretError,
    client_for,
    load_environment,
)
from scripts.lib.normalize import normalize_bundle  # noqa: E402
from scripts.lib.superset_client import SupersetError  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("diff")

REPO_ROOT = Path(__file__).resolve().parents[1]
ASSETS_DIR = REPO_ROOT / "assets"

ADDED, DELETED, DRIFT, MODIFIED, UNCHANGED = (
    "ADDED", "DELETED", "DRIFT", "MODIFIED", "UNCHANGED",
)

#: Files whose content is bookkeeping rather than asset definition.
IGNORED = {"metadata.yaml"}


@dataclass(frozen=True)
class Change:
    status: str
    path: str
    detail: str = ""


def _git_committed(path: str) -> bytes | None:
    """Content of `assets/<path>` at HEAD, or None when untracked."""
    try:
        return subprocess.run(
            ["git", "show", f"HEAD:assets/{path}"], cwd=REPO_ROOT,
            capture_output=True, check=True,
        ).stdout
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None


def read_git_assets() -> dict[str, bytes]:
    files: dict[str, bytes] = {}
    for path in sorted(ASSETS_DIR.rglob("*")):
        if path.is_file() and path.suffix in {".yaml", ".yml"}:
            files[str(path.relative_to(ASSETS_DIR))] = path.read_bytes()
    return files


def read_superset_assets(env_name: str) -> dict[str, bytes]:
    """Export the live instance and normalize it the same way Git assets are."""
    from scripts.export import collect_bundle

    env = load_environment(env_name)
    client = client_for(env)
    return normalize_bundle(collect_bundle(client, None))


def compare(git_files: dict[str, bytes],
            live_files: dict[str, bytes]) -> list[Change]:
    changes: list[Change] = []
    for path in sorted(set(git_files) | set(live_files)):
        if path in IGNORED:
            continue
        in_git, in_live = git_files.get(path), live_files.get(path)
        if in_git is None:
            changes.append(Change(DELETED, path,
                                  "present in Superset but not in Git"))
        elif in_live is None:
            changes.append(Change(ADDED, path,
                                  "present in Git but not in Superset"))
        elif in_git == in_live:
            changes.append(Change(UNCHANGED, path))
        else:
            committed = _git_committed(path)
            if committed is not None and committed == in_live:
                # Superset still matches HEAD; the difference is an uncommitted
                # local edit heading towards a pull request.
                changes.append(Change(MODIFIED, path,
                                      "local working tree differs from HEAD"))
            else:
                changes.append(Change(DRIFT, path,
                                      "Superset differs from Git; it was changed "
                                      "outside the GitOps workflow"))
    return changes


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--env", default=os.environ.get("SUPERSET_ENV", "dev"))
    parser.add_argument("--fail-on-drift", action="store_true",
                        help="Exit non-zero when DRIFT or DELETED is detected.")
    parser.add_argument("--quiet", action="store_true",
                        help="Only print assets that are not UNCHANGED.")
    args = parser.parse_args()

    git_files = read_git_assets()
    live_files = read_superset_assets(args.env)
    changes = compare(git_files, live_files)

    width = max((len(c.path) for c in changes), default=10)
    for change in changes:
        if args.quiet and change.status == UNCHANGED:
            continue
        suffix = f"  ({change.detail})" if change.detail else ""
        print(f"{change.path:<{width}}  {change.status}{suffix}")

    counts: dict[str, int] = {}
    for change in changes:
        counts[change.status] = counts.get(change.status, 0) + 1
    print("\n" + "  ".join(f"{status}={counts.get(status, 0)}" for status in
                           (ADDED, MODIFIED, DRIFT, DELETED, UNCHANGED)))

    drifted = counts.get(DRIFT, 0) + counts.get(DELETED, 0)
    if drifted:
        logger.warning("DRIFT DETECTED: %d asset(s) changed outside Git", drifted)
        if args.fail_on_drift:
            logger.error(
                "Refusing to continue. Reconcile deliberately: either "
                "`scripts/export.sh --env %s` to accept the change into Git, or "
                "`scripts/import.sh --env %s` to restore Git's state.",
                args.env, args.env,
            )
            return 1
    else:
        logger.info("no drift: %s matches Git", args.env)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (SupersetError, FileNotFoundError, MissingSecretError) as exc:
        logger.error("%s", exc)
        sys.exit(1)
