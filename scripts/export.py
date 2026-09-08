#!/usr/bin/env python
# Licensed under the Apache License, Version 2.0
"""Export Superset assets into the version-controlled `assets/` tree.

Workflow A of the GitOps model:

    Superset -> export -> normalize -> git diff -> commit -> pull request

The export is deterministic: running it twice against an unchanged Superset
produces byte-identical files, so `git status` stays clean unless something
really changed. See scripts/lib/normalize.py for the rules and why each exists.

    python scripts/export.py --env dev                     # everything
    python scripts/export.py --env dev --dashboard SALES_EXECUTIVE_OVERVIEW
    python scripts/export.py --env dev --check             # fail if drifted
"""

from __future__ import annotations

import argparse
import logging
import os
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.lib.environment import (  # noqa: E402
    MissingSecretError,
    client_for,
    load_environment,
)
from scripts.lib.normalize import normalize_bundle  # noqa: E402
from scripts.lib.superset_client import SupersetClient, SupersetError  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("export")

REPO_ROOT = Path(__file__).resolve().parents[1]
ASSETS_DIR = REPO_ROOT / "assets"

#: Directories owned by the exporter. Anything else under assets/ is left alone.
MANAGED_DIRS = ("databases", "datasets", "charts", "dashboards", "saved_queries", "tags")


def collect_bundle(
    client: SupersetClient, dashboards: list[str] | None
) -> dict[str, bytes]:
    """Export dashboards (with their full dependency closure) plus stragglers.

    A dashboard export already pulls in its charts, datasets and databases.
    Charts and datasets not attached to any dashboard would otherwise be
    invisible to Git, so they are exported separately and merged in.
    """
    files: dict[str, bytes] = {}

    all_dashboards = client.list_assets("dashboard")
    if dashboards:
        wanted = set(dashboards)
        selected = [d for d in all_dashboards if d.get("dashboard_title") in wanted]
        missing = wanted - {d.get("dashboard_title") for d in selected}
        if missing:
            raise SupersetError(f"No such dashboard(s): {', '.join(sorted(missing))}")
    else:
        selected = all_dashboards

    if selected:
        ids = [d["id"] for d in selected]
        logger.info("exporting %d dashboard(s)", len(ids))
        files.update(client.unpack_zip(client.export_assets("dashboard", ids)))

    if not dashboards:
        exported_chart_uuids = {
            path for path in files if path.startswith("charts/")
        }
        orphan_charts = [
            c["id"] for c in client.list_assets("chart")
            if not any(c.get("slice_name", "") in p for p in exported_chart_uuids)
        ]
        if orphan_charts:
            logger.info("exporting %d chart(s) not on any dashboard", len(orphan_charts))
            extra = client.unpack_zip(client.export_assets("chart", orphan_charts))
            for path, content in extra.items():
                files.setdefault(path, content)

        saved_queries = [q["id"] for q in client.list_assets("saved_query")]
        if saved_queries:
            logger.info("exporting %d saved query/queries", len(saved_queries))
            extra = client.unpack_zip(client.export_assets("saved_query", saved_queries))
            for path, content in extra.items():
                files.setdefault(path, content)

    if not files:
        raise SupersetError("Nothing to export: this Superset instance has no assets.")
    return files


def write_assets(files: dict[str, bytes], *, prune: bool) -> list[Path]:
    """Write the normalized bundle into assets/, optionally pruning removals."""
    if prune:
        for directory in MANAGED_DIRS:
            target = ASSETS_DIR / directory
            if target.is_dir():
                shutil.rmtree(target)

    written: list[Path] = []
    for path in sorted(files):
        destination = ASSETS_DIR / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(files[path])
        written.append(destination)
    return written


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--env", default=os.environ.get("SUPERSET_ENV", "dev"),
                        help="Environment from config/environments (default: dev).")
    parser.add_argument("--dashboard", action="append", dest="dashboards",
                        help="Export only this dashboard (repeatable).")
    parser.add_argument("--no-prune", action="store_true",
                        help="Keep asset files that no longer exist in Superset.")
    parser.add_argument("--check", action="store_true",
                        help="Do not write; exit 1 if the export differs from Git.")
    args = parser.parse_args()

    env = load_environment(args.env)
    logger.info("exporting from %s (%s)", env.name, env.superset_url)
    client = client_for(env)

    raw = collect_bundle(client, args.dashboards)
    normalized = normalize_bundle(raw)
    logger.info("normalized %d file(s)", len(normalized))

    if args.check:
        differences: list[str] = []
        for path, content in sorted(normalized.items()):
            existing = ASSETS_DIR / path
            if not existing.is_file():
                differences.append(f"MISSING IN GIT   {path}")
            elif existing.read_bytes() != content:
                differences.append(f"DIFFERS FROM GIT {path}")
        if differences:
            print("\n".join(differences))
            logger.error("%d file(s) differ from the committed assets", len(differences))
            return 1
        logger.info("assets/ matches %s exactly", env.name)
        return 0

    written = write_assets(normalized, prune=not args.no_prune)
    logger.info("wrote %d file(s) under %s", len(written), ASSETS_DIR)
    for path in sorted(p.relative_to(REPO_ROOT) for p in written):
        print(f"  {path}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (SupersetError, FileNotFoundError, MissingSecretError) as exc:
        logger.error("%s", exc)
        sys.exit(1)
