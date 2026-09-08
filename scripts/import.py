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
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.lib.environment import (  # noqa: E402
    Environment,
    MissingSecretError,
    client_for,
    load_environment,
)
from scripts.lib.normalize import (  # noqa: E402
    UnresolvedPlaceholderError,
    dump_yaml,
    find_placeholders,
    load_yaml,
    resolve_placeholders,
)
from scripts.lib.superset_client import SupersetClient, SupersetError  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("import")

REPO_ROOT = Path(__file__).resolve().parents[1]
ASSETS_DIR = REPO_ROOT / "assets"
DEPLOY_LOG = REPO_ROOT / ".deployments"

#: metadata.yaml must declare the type the target importer expects; the value
#: is the DAO model class name (superset/commands/importers/v1/__init__.py:106).
#: ImportAssetsCommand is the exception and expects the literal "assets".
ASSETS_METADATA_TYPE = "assets"


def read_assets() -> dict[str, bytes]:
    """Load every committed asset file, keyed by bundle-relative path."""
    if not ASSETS_DIR.is_dir():
        raise FileNotFoundError(f"No assets directory at {ASSETS_DIR}")
    files: dict[str, bytes] = {}
    for path in sorted(ASSETS_DIR.rglob("*")):
        if path.is_file() and path.suffix in {".yaml", ".yml"}:
            files[str(path.relative_to(ASSETS_DIR))] = path.read_bytes()
    if not files:
        raise FileNotFoundError(f"No asset files found under {ASSETS_DIR}")
    return files


def resolve_bundle(files: dict[str, bytes], env: Environment) -> dict[str, bytes]:
    """Substitute every ${PLACEHOLDER} for this environment.

    Raises rather than deploying an asset that still contains an unresolved
    reference, which would otherwise create a visibly broken Superset object.
    """
    context = dict(env.placeholders)
    resolved: dict[str, bytes] = {}
    for path, raw in files.items():
        config = load_yaml(raw)
        if not isinstance(config, dict):
            resolved[path] = raw
            continue
        missing = find_placeholders(config) - set(context)
        if missing:
            raise UnresolvedPlaceholderError(
                f"{path}: {', '.join(sorted(missing))} not defined for "
                f"environment {env.name!r}. Add it to "
                f"config/environments/{env.name}.yaml."
            )
        resolved[path] = dump_yaml(resolve_placeholders(config, context, path=path)).encode()
    return resolved


def build_passwords(files: dict[str, bytes], env: Environment) -> dict[str, str]:
    """Map each database asset to its credential for the import endpoint.

    Superset accepts passwords out-of-band precisely so they never have to be
    written into the bundle. Any database file without a configured password is
    left to whatever the resolved URI already contains.
    """
    passwords: dict[str, str] = {}
    for path in files:
        if path.startswith("databases/") and path in env.database_passwords:
            passwords[path] = env.database_passwords[path]
    return passwords


def _record_deployment(env: Environment, commit: str, result: str,
                       detail: dict[str, Any]) -> Path:
    """Append an auditable record of what was deployed, by whom, and whether it worked."""
    DEPLOY_LOG.mkdir(exist_ok=True)
    entry = {
        "deployment_id": datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
        "environment": env.name,
        "git_commit": commit,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "actor": os.environ.get("GITOPS_ACTOR")
        or os.environ.get("GITHUB_ACTOR")
        or os.environ.get("USER", "unknown"),
        "result": result,
        "detail": detail,
    }
    path = DEPLOY_LOG / f"{env.name}.jsonl"
    with path.open("a") as handle:
        handle.write(json.dumps(entry) + "\n")
    return path


def _git_commit() -> str:
    import subprocess

    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=REPO_ROOT,
            capture_output=True, text=True, check=True,
        ).stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return "unknown"


def deploy(client: SupersetClient, files: dict[str, bytes],
           passwords: dict[str, str]) -> dict[str, Any]:
    """Import the bundle through /api/v1/assets/import/.

    This endpoint - and only this endpoint - makes Git authoritative. The
    per-model importers create missing objects but leave existing ones alone
    (see SupersetClient.import_assets), so a redeploy through them would not
    correct drift. ImportAssetsCommand passes overwrite=True to every model.
    """
    files = dict(files)
    files["metadata.yaml"] = dump_yaml(
        {"version": "1.0.0", "type": ASSETS_METADATA_TYPE}
    ).encode()
    logger.info("importing %d asset file(s) via /api/v1/assets/import/", len(files))
    result = client.import_assets(
        "assets", files, passwords=passwords, overwrite=True
    )
    logger.info("import accepted: %s", result.get("message", "OK"))
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--env", default=os.environ.get("SUPERSET_ENV", "dev"))
    parser.add_argument("--dry-run", action="store_true",
                        help="Resolve and validate the bundle without importing.")
    args = parser.parse_args()

    env = load_environment(args.env)
    files = read_assets()
    resolved = resolve_bundle(files, env)
    passwords = build_passwords(files, env)
    commit = _git_commit()

    logger.info("environment=%s commit=%s assets=%d", env.name, commit[:8], len(resolved))

    if args.dry_run:
        logger.info("dry run: bundle resolves cleanly, nothing was imported")
        for path in sorted(resolved):
            print(f"  {path}")
        if passwords:
            print(f"  ({len(passwords)} database credential(s) would be supplied "
                  "out-of-band)")
        return 0

    client = client_for(env)
    try:
        result = deploy(client, resolved, passwords)
    except SupersetError as exc:
        _record_deployment(env, commit, "FAILED", {"error": str(exc)[:2000]})
        raise

    log_path = _record_deployment(
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
