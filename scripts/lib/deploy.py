# Licensed under the Apache License, Version 2.0
"""Shared deployment primitives used by import, rollback and the CI workflows.

Kept in `scripts/lib` rather than in `scripts/import.py` because `import` is a
Python keyword and `from scripts.import import ...` is a syntax error - the
rollback path needs exactly the same bundle resolution and deploy logic, and
duplicating it would let the two drift apart.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from scripts.lib.environment import Environment
from scripts.lib.normalize import (
    UnresolvedPlaceholderError,
    dump_yaml,
    find_placeholders,
    load_yaml,
    resolve_placeholders,
)
from scripts.lib.superset_client import SupersetClient

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[2]
ASSETS_DIR = REPO_ROOT / "assets"
DEPLOY_LOG = REPO_ROOT / ".deployments"

#: metadata.yaml must declare the type the target importer expects. For the
#: per-model endpoints that is the DAO model class name
#: (superset/commands/importers/v1/__init__.py:106); ImportAssetsCommand is the
#: exception and expects the literal "assets".
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
        resolved[path] = dump_yaml(
            resolve_placeholders(config, context, path=path)
        ).encode()
    return resolved


def build_passwords(files: dict[str, bytes], env: Environment) -> dict[str, str]:
    """Map each database asset to its credential for the import endpoint.

    Superset accepts passwords out-of-band precisely so they never have to be
    written into the bundle.
    """
    return {
        path: env.database_passwords[path]
        for path in files
        if path.startswith("databases/") and path in env.database_passwords
    }


def deploy(client: SupersetClient, files: dict[str, bytes],
           passwords: dict[str, str]) -> dict[str, Any]:
    """Import a bundle through /api/v1/assets/import/.

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
    result = client.import_assets("assets", files, passwords=passwords, overwrite=True)
    logger.info("import accepted: %s", result.get("message", "OK"))
    return result


def git_commit() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=REPO_ROOT,
            capture_output=True, text=True, check=True,
        ).stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return "unknown"


def record_deployment(env: Environment, commit: str, result: str,
                      detail: dict[str, Any]) -> Path:
    """Append an auditable record: what was deployed, by whom, and the outcome."""
    DEPLOY_LOG.mkdir(exist_ok=True)
    entry = {
        "deployment_id": datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ"),
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


def deployment_history(env_name: str) -> list[dict[str, Any]]:
    path = DEPLOY_LOG / f"{env_name}.jsonl"
    if not path.is_file():
        return []
    entries: list[dict[str, Any]] = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if line:
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return entries
