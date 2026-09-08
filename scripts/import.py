#!/usr/bin/env python
# Licensed under the Apache License, Version 2.0
"""Deploy version-controlled assets into a Superset environment.

Workflow B of the GitOps model:

    git checkout -> validate -> resolve placeholders -> import -> smoke test

Import is idempotent. Identity is the UUID carried by every asset, so running
the same deployment twice updates the existing objects rather than creating
duplicates. This is verified by tests/integration/test_import_idempotency.py.

Credentials never travel inside an asset file. `assets/databases/*.yaml` holds
`sqlalchemy_uri: ${WAREHOUSE_URI}`; the real URI is resolved from environment
configuration here, and database passwords are passed to Superset through the
import endpoint's separate `passwords` parameter.

    python scripts/import.py --env dev
    python scripts/import.py --env dev --dry-run
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.lib.deploy import (  # noqa: E402
    build_passwords,
    deploy,
    git_commit,
    read_assets,
    record_deployment,
    resolve_bundle,
)
from scripts.lib.environment import (  # noqa: E402
    MissingSecretError,
    client_for,
    load_environment,
)
from scripts.lib.normalize import UnresolvedPlaceholderError  # noqa: E402
from scripts.lib.superset_client import SupersetError  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("import")

REPO_ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--env", default=os.environ.get("SUPERSET_ENV", "dev"))
    parser.add_argument("--dry-run", action="store_true",
                        help="Resolve and validate the bundle without importing.")
    parser.add_argument("--ignore-drift", action="store_true",
                        help="Deploy even when the environment requires a clean "
                             "drift check (overwrites whatever drifted).")
    args = parser.parse_args()

    env = load_environment(args.env)
    files = read_assets()
    resolved = resolve_bundle(files, env)
    passwords = build_passwords(files, env)
    commit = git_commit()

    logger.info("environment=%s commit=%s assets=%d", env.name, commit[:8], len(resolved))

    if args.dry_run:
        logger.info("dry run: bundle resolves cleanly, nothing was imported")
        for path in sorted(resolved):
            print(f"  {path}")
        if passwords:
            print(f"  ({len(passwords)} database credential(s) would be supplied "
                  "out-of-band)")
        return 0

    # Environments that declare `deployment.require_clean_drift` (PROD does)
    # refuse to deploy over changes made outside the GitOps workflow, so a
    # deployment never silently discards someone's unreviewed production edit.
    if env.require_clean_drift and not args.ignore_drift:
        from scripts.diff import compare, read_git_assets, read_superset_assets

        drifted = [
            change for change in compare(read_git_assets(),
                                         read_superset_assets(env.name))
            if change.status in {"DRIFT", "DELETED"}
        ]
        if drifted:
            for change in drifted:
                logger.error("  %s  %s", change.status, change.path)
            logger.error(
                "%s requires a clean drift check before deploying. Reconcile "
                "deliberately (scripts/export.sh --env %s to accept the change, "
                "or re-run with --ignore-drift to overwrite it).",
                env.name, env.name,
            )
            record_deployment(env, commit, "BLOCKED",
                              {"reason": "drift", "paths": [c.path for c in drifted]})
            return 1

    client = client_for(env)
    try:
        result = deploy(client, resolved, passwords)
    except SupersetError as exc:
        record_deployment(env, commit, "FAILED", {"error": str(exc)[:2000]})
        raise

    log_path = record_deployment(
        env, commit, "SUCCESS",
        {"files": sorted(resolved), "message": result.get("message")},
    )
    logger.info("deployment recorded in %s", log_path.relative_to(REPO_ROOT))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (SupersetError, FileNotFoundError, MissingSecretError,
            UnresolvedPlaceholderError) as exc:
        logger.error("%s", exc)
        sys.exit(1)
