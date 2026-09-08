#!/usr/bin/env python
# Licensed under the Apache License, Version 2.0
"""Roll an environment back to a previously deployed commit.

Git history is the rollback mechanism, not a metadata-database snapshot. A
database restore would also revert unrelated runtime state (query history,
user preferences, permissions) and cannot be reviewed; re-importing a known
commit reverts exactly the assets and nothing else.

    python scripts/rollback.py --env prod                 # previous deployment
    python scripts/rollback.py --env prod --to <git-sha>  # a specific commit
    python scripts/rollback.py --env prod --list          # deployment history

The assets are read out of Git with `git show`, so the working tree is never
modified and a rollback can be performed from a dirty checkout.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.lib.deploy import (  # noqa: E402
    DEPLOY_LOG,
    build_passwords,
    deploy,
    deployment_history,
    record_deployment,
    resolve_bundle,
)
from scripts.lib.environment import (  # noqa: E402
    MissingSecretError,
    client_for,
    load_environment,
)
from scripts.lib.superset_client import SupersetError  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("rollback")

REPO_ROOT = Path(__file__).resolve().parents[1]


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=REPO_ROOT, capture_output=True, text=True, check=True
    ).stdout


def previous_successful_commit(env_name: str) -> str | None:
    """The commit deployed before the current one, ignoring failures."""
    successes = [e for e in deployment_history(env_name) if e.get("result") == "SUCCESS"]
    commits: list[str] = []
    for entry in successes:
        commit = entry.get("git_commit")
        if commit and commit != "unknown" and (not commits or commits[-1] != commit):
            commits.append(commit)
    return commits[-2] if len(commits) >= 2 else None


def assets_at_commit(commit: str) -> dict[str, bytes]:
    """Read assets/ as it existed at `commit`, without touching the work tree."""
    listing = _git("ls-tree", "-r", "--name-only", commit, "assets/")
    files: dict[str, bytes] = {}
    for line in listing.splitlines():
        line = line.strip()
        if not line.endswith((".yaml", ".yml")):
            continue
        content = subprocess.run(
            ["git", "show", f"{commit}:{line}"], cwd=REPO_ROOT,
            capture_output=True, check=True,
        ).stdout
        files[line[len("assets/"):]] = content
    return files


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--env", default=os.environ.get("SUPERSET_ENV", "dev"))
    parser.add_argument("--to", help="Commit SHA to roll back to.")
    parser.add_argument("--list", action="store_true",
                        help="Show the deployment history and exit.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Resolve the target bundle without importing it.")
    args = parser.parse_args()

    if args.list:
        history = deployment_history(args.env)
        if not history:
            logger.info("no recorded deployments for %s", args.env)
            return 0
        for entry in history:
            print(f"{entry.get('timestamp', '?'):32} "
                  f"{str(entry.get('git_commit', '?'))[:8]}  "
                  f"{entry.get('result', '?'):8} "
                  f"by {entry.get('actor', '?')}")
        return 0

    target = args.to or previous_successful_commit(args.env)
    if not target:
        logger.error(
            "No rollback target. %s has fewer than two successful deployments "
            "recorded; pass --to <git-sha> explicitly.", args.env,
        )
        return 1

    try:
        subject = _git("log", "-1", "--format=%s", target).strip()
    except subprocess.CalledProcessError:
        logger.error("commit %s does not exist in this repository", target)
        return 1

    env = load_environment(args.env)
    files = assets_at_commit(target)
    if not files:
        logger.error("commit %s contains no assets/ files", target[:8])
        return 1

    logger.info("rolling %s back to %s (%s)", args.env, target[:8], subject)
    resolved = resolve_bundle(files, env)

    if args.dry_run:
        logger.info("dry run: %d asset file(s) would be restored", len(resolved))
        for path in sorted(resolved):
            print(f"  {path}")
        return 0

    client = client_for(env)
    deploy(client, resolved, build_passwords(files, env))

    record_deployment(env, target, "SUCCESS",
                      {"rollback": True, "subject": subject})

    logger.info("rollback complete. Run scripts/smoke_test.py --env %s to verify.",
                args.env)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (SupersetError, FileNotFoundError, MissingSecretError) as exc:
        logger.error("%s", exc)
        sys.exit(1)
