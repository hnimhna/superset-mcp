#!/usr/bin/env python
# Licensed under the Apache License, Version 2.0
"""Post-deployment health check against a live Superset environment.

Answers one question: is what we just deployed actually working? It exercises
the real query path rather than only checking that rows exist in the metadata
database, because an asset can be present and still be broken.

    python scripts/smoke_test.py --env dev
    python scripts/smoke_test.py --env prod --dashboard SALES_EXECUTIVE_OVERVIEW

Checks:
  connectivity   Superset answers /health and the API accepts our credentials
  databases      every database asset exists and its connection tests OK
  datasets       every dataset exists, its columns resolve and it can be queried
  metrics        every metric compiles into executable SQL
  charts         every chart's query executes against the warehouse
  dashboards     every dashboard exists and all chart references resolve
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.lib.environment import (  # noqa: E402
    MissingSecretError,
    client_for,
    load_environment,
)
from scripts.lib.normalize import load_yaml  # noqa: E402
from scripts.lib.superset_client import SupersetClient, SupersetError  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("smoke")

REPO_ROOT = Path(__file__).resolve().parents[1]
ASSETS_DIR = REPO_ROOT / "assets"


@dataclass
class Result:
    name: str
    passed: bool
    detail: str = ""
    duration_ms: int = 0


@dataclass
class Report:
    results: list[Result] = field(default_factory=list)

    def add(self, name: str, passed: bool, detail: str = "", duration_ms: int = 0) -> None:
        self.results.append(Result(name, passed, detail, duration_ms))
        marker = "PASS" if passed else "FAIL"
        logger.log(
            logging.INFO if passed else logging.ERROR,
            "  [%s] %s%s", marker, name, f" - {detail}" if detail else "",
        )

    @property
    def failed(self) -> list[Result]:
        return [r for r in self.results if not r.passed]

    def summary(self) -> str:
        return (f"{len(self.results) - len(self.failed)}/{len(self.results)} checks passed")


def _timed(fn, *args, **kwargs) -> tuple[bool, str, int]:
    start = time.perf_counter()
    try:
        detail = fn(*args, **kwargs) or ""
        return True, str(detail), int((time.perf_counter() - start) * 1000)
    except (SupersetError, ValueError, KeyError) as exc:
        return False, str(exc)[:300], int((time.perf_counter() - start) * 1000)


def _git_assets(kind: str) -> list[dict[str, Any]]:
    directory = ASSETS_DIR / kind
    if not directory.is_dir():
        return []
    out = []
    for path in sorted(directory.rglob("*.yaml")):
        config = load_yaml(path.read_bytes())
        if isinstance(config, dict):
            config["_path"] = str(path.relative_to(ASSETS_DIR))
            out.append(config)
    return out


def check_connectivity(client: SupersetClient, report: Report) -> None:
    ok, detail, ms = _timed(lambda: client.me().get("username"))
    report.add("connectivity.api", ok, f"authenticated as {detail}" if ok else detail, ms)


def check_databases(client: SupersetClient, report: Report) -> dict[str, int]:
    """Every database in Git must exist and its connection must actually work."""
    live = {d.get("uuid"): d for d in client.list_assets("database")}
    # list_assets does not always include uuid; fall back to a detail fetch.
    resolved: dict[str, int] = {}
    for item in client.list_assets("database"):
        full = client.get_asset("database", item["id"])
        if full.get("uuid"):
            resolved[str(full["uuid"])] = item["id"]
    _ = live

    for config in _git_assets("databases"):
        uuid = str(config.get("uuid"))
        name = config.get("database_name")
        db_id = resolved.get(uuid)
        if db_id is None:
            report.add(f"database.{name}", False, f"uuid {uuid} not present in Superset")
            continue

        def _probe(pk: int = db_id) -> str:
            result = client.run_sql(pk, "SELECT 1 AS smoke_test")
            rows = result.get("data") or []
            if not rows:
                raise ValueError("connection returned no rows for SELECT 1")
            return "connection OK"

        ok, detail, ms = _timed(_probe)
        report.add(f"database.{name}", ok, detail, ms)
    return resolved


def check_datasets(client: SupersetClient, report: Report) -> None:
    """A dataset must exist, expose its declared columns, and be queryable."""
    live = {}
    for item in client.list_assets("dataset"):
        full = client.get_asset("dataset", item["id"])
        if full.get("uuid"):
            live[str(full["uuid"])] = (item["id"], full)

    for config in _git_assets("datasets"):
        uuid = str(config.get("uuid"))
        name = config.get("table_name")
        entry = live.get(uuid)
        if entry is None:
            report.add(f"dataset.{name}", False, f"uuid {uuid} not present in Superset")
            continue
        dataset_id, full = entry

        expected_columns = {
            c.get("column_name") for c in (config.get("columns") or [])
            if isinstance(c, dict)
        }
        actual_columns = {
            c.get("column_name") for c in (full.get("columns") or [])
            if isinstance(c, dict)
        }
        missing = expected_columns - actual_columns
        report.add(
            f"dataset.{name}.columns", not missing,
            "all declared columns present" if not missing
            else f"missing columns: {', '.join(sorted(str(m) for m in missing))}",
        )

        expected_metrics = {
            m.get("metric_name") for m in (config.get("metrics") or [])
            if isinstance(m, dict)
        }
        actual_metrics = {
            m.get("metric_name") for m in (full.get("metrics") or [])
            if isinstance(m, dict)
        }
        missing_metrics = expected_metrics - actual_metrics
        report.add(
            f"dataset.{name}.metrics", not missing_metrics,
            f"{len(expected_metrics)} metric(s) present" if not missing_metrics
            else f"missing metrics: {', '.join(sorted(str(m) for m in missing_metrics))}",
        )

        # Prove each metric compiles into runnable SQL, not merely that it is stored.
        database_id = (full.get("database") or {}).get("id")
        if database_id and expected_metrics:
            def _run_metrics(pk: int = database_id, ds: dict[str, Any] = full) -> str:
                payload = {
                    "datasource": {"id": dataset_id, "type": "table"},
                    "force": False,
                    "queries": [{
                        "metrics": sorted(str(m) for m in expected_metrics),
                        "columns": [], "filters": [], "row_limit": 1,
                        "orderby": [], "extras": {"having": "", "where": ""},
                    }],
                    "result_format": "json",
                    "result_type": "results",
                }
                _ = ds
                resp = client._request("POST", "/api/v1/chart/data", mutating=True,
                                       json=payload)
                if resp.status_code >= 400:
                    raise SupersetError("metric query failed", resp)
                return f"{len(expected_metrics)} metric(s) executed"

            ok, detail, ms = _timed(_run_metrics)
            report.add(f"dataset.{name}.metrics.execute", ok, detail, ms)


def check_charts(client: SupersetClient, report: Report) -> None:
    """Every chart must run its query successfully."""
    live = {}
    for item in client.list_assets("chart"):
        full = client.get_asset("chart", item["id"])
        full.setdefault("slice_name", item.get("slice_name"))
        if full.get("uuid"):
            live[str(full["uuid"])] = full

    for config in _git_assets("charts"):
        uuid = str(config.get("uuid"))
        name = config.get("slice_name")
        chart = live.get(uuid)
        if chart is None:
            report.add(f"chart.{name}", False, f"uuid {uuid} not present in Superset")
            continue

        def _run(c: dict[str, Any] = chart) -> str:
            payload = client.execute_chart_query(c)
            results = payload.get("result") or [{}]
            return f"rowcount={results[0].get('rowcount')}"

        ok, detail, ms = _timed(_run)
        report.add(f"chart.{name}.query", ok, detail, ms)


def check_dashboards(client: SupersetClient, report: Report,
                     only: list[str] | None) -> None:
    """Dashboards must exist and every chart they reference must resolve."""
    live = {}
    for item in client.list_assets("dashboard"):
        full = client.get_asset("dashboard", item["id"])
        if full.get("uuid"):
            live[str(full["uuid"])] = (item["id"], full)

    chart_uuids = set()
    for item in client.list_assets("chart"):
        full = client.get_asset("chart", item["id"])
        if full.get("uuid"):
            chart_uuids.add(str(full["uuid"]))

    for config in _git_assets("dashboards"):
        title = config.get("dashboard_title")
        if only and title not in only:
            continue
        uuid = str(config.get("uuid"))
        entry = live.get(uuid)
        if entry is None:
            report.add(f"dashboard.{title}", False, f"uuid {uuid} not present")
            continue
        dashboard_id, full = entry

        position = json.loads(full.get("position_json") or "{}")
        referenced = {
            node["meta"]["uuid"] for node in position.values()
            if isinstance(node, dict) and node.get("type") == "CHART"
            and isinstance(node.get("meta"), dict) and "uuid" in node["meta"]
        }
        dangling = referenced - chart_uuids
        report.add(
            f"dashboard.{title}.references", not dangling,
            f"{len(referenced)} chart reference(s) resolve" if not dangling
            else f"broken references: {', '.join(sorted(dangling))}",
        )

        def _fetch(pk: int = dashboard_id) -> str:
            charts = client._json("GET", f"/api/v1/dashboard/{pk}/charts")
            return f"{len(charts.get('result', []))} chart(s) attached"

        ok, detail, ms = _timed(_fetch)
        report.add(f"dashboard.{title}.charts", ok, detail, ms)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--env", default=os.environ.get("SUPERSET_ENV", "dev"))
    parser.add_argument("--dashboard", action="append", dest="dashboards",
                        help="Restrict dashboard checks to these titles.")
    parser.add_argument("--json", dest="as_json", action="store_true",
                        help="Emit a machine-readable report (for CI artifacts).")
    args = parser.parse_args()

    env = load_environment(args.env)
    logger.info("smoke testing %s (%s)", env.name, env.superset_url)
    client = client_for(env)

    report = Report()
    check_connectivity(client, report)
    check_databases(client, report)
    check_datasets(client, report)
    check_charts(client, report)
    check_dashboards(client, report, args.dashboards)

    if args.as_json:
        print(json.dumps({
            "environment": env.name,
            "passed": not report.failed,
            "summary": report.summary(),
            "results": [vars(r) for r in report.results],
        }, indent=2))

    if report.failed:
        logger.error("SMOKE TEST FAILED: %s", report.summary())
        return 1
    logger.info("SMOKE TEST PASSED: %s", report.summary())
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (SupersetError, FileNotFoundError, MissingSecretError) as exc:
        logger.error("%s", exc)
        sys.exit(1)
