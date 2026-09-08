#!/usr/bin/env python
# Licensed under the Apache License, Version 2.0
"""Roll an environment back to a previously deployed commit.

Git history is the rollback mechanism, not a metadata-database snapshot. A
database restore would also revert unrelated runtime state (query history,
user preferences, permissions) and cannot be reviewed; re-importing a known
commit reverts exactly the assets and nothing else.

    python scripts/rollback.py --env prod                    # previous deployment
    python scripts/rollback.py --env prod --to <git-sha>     # a specific commit
    python scripts/rollback.py --env prod --from-dir <path>  # a backup directory
    python scripts/rollback.py --env prod --list             # deployment history

The assets are read out of Git with `git show`, so the working tree is never
modified and a rollback can be performed from a dirty checkout.

`--from-dir` restores from a pre-deploy backup instead of from history. This is
what CI uses: `.deployments/<env>.jsonl` is gitignored, so a fresh CI checkout
has no deployment history at all and `--env <env>` alone would fail with "no
rollback target" precisely when the fail-safe is needed. The deploy workflows
take a backup with `export.py --output` before touching anything and point the
failure handler at that directory.
"""

from __future__ import annotations

import argparse
import logging
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.lib.deploy import (  # noqa: E402
    build_passwords,
    deploy,
    deployment_history,
    git_commit,
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


def assets_from_directory(directory: Path) -> dict[str, bytes]:
    """Read a previously exported bundle from disk.

    Used for backup-based rollback, where the target is a directory produced by
    `scripts/export.py --output <dir>` rather than a Git commit.
    """
    if not directory.is_dir():
        raise FileNotFoundError(f"No backup directory at {directory}")
    files: dict[str, bytes] = {}
    for path in sorted(directory.rglob("*")):
        if path.is_file() and path.suffix in {".yaml", ".yml"}:
            files[str(path.relative_to(directory))] = path.read_bytes()
    if not files:
        raise FileNotFoundError(f"No asset files found under {directory}")
    return files


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
    parser.add_argument("--from-dir", dest="from_dir", type=Path, metavar="DIR",
                        help="Restore from a backup directory produced by "
                             "`export.py --output DIR` instead of from Git history. "
                             "Required in CI, where .deployments/ is not checked out.")
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

    env = load_environment(args.env)

    # Backup-directory rollback: no Git history needed.
    if args.from_dir:
        files = assets_from_directory(args.from_dir)
        logger.info("restoring %s from backup %s (%d file(s))",
                    args.env, args.from_dir, len(files))
        resolved = resolve_bundle(files, env)
        if args.dry_run:
            logger.info("dry run: %d asset file(s) would be restored", len(resolved))
            for path in sorted(resolved):
                print(f"  {path}")
            return 0
        client = client_for(env)
        deploy(client, resolved, build_passwords(files, env))
        record_deployment(env, git_commit(), "SUCCESS",
                          {"rollback": True, "source": str(args.from_dir)})
        logger.info("restore complete. Run scripts/smoke_test.py --env %s to verify.",
                    args.env)
        return 0

    target = args.to or previous_successful_commit(args.env)
    if not target:
        logger.error(
            "No rollback target. %s has fewer than two successful deployments "
            "recorded in .deployments/ (which is gitignored, so a fresh CI "
            "checkout always starts empty). Pass --to <git-sha> or "
            "--from-dir <backup> explicitly.", args.env,
        )
        return 1

    try:
        subject = _git("log", "-1", "--format=%s", target).strip()
    except subprocess.CalledProcessError:
        logger.error("commit %s does not exist in this repository", target)
        return 1

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
