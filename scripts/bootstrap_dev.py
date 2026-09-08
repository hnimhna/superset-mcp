#!/usr/bin/env python
# Licensed under the Apache License, Version 2.0
"""Create the reference SALES assets in a DEV Superset instance.

This is the *seed* for the GitOps loop: it produces a realistic, convention-
compliant set of assets in DEV which are then exported to Git and promoted.
It is idempotent - every object is looked up before it is created, so running
it twice does not create duplicates.

It talks to the REST API only; the Superset metadata database is never touched
directly.

    python scripts/bootstrap_dev.py
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import uuid
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.lib.superset_client import SupersetClient, SupersetError  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("bootstrap")

DATABASE_NAME = "SALES_DWH_DEV"
DATASET_NAME = "SALES_ORDER_DAILY"
SCHEMA = "sales"
TABLE = "vw_order_daily"
DASHBOARD_TITLE = "SALES_EXECUTIVE_OVERVIEW"

# Deterministic UUIDs. Stable identity is the whole point of the GitOps model,
# so the seed assets get fixed UUIDs rather than whatever the DB assigns.
NS = uuid.UUID("6ba7b810-9dad-11d1-80b4-00c04fd430c8")
UUIDS = {name: str(uuid.uuid5(NS, f"superset-gitops/{name}")) for name in (
    DATABASE_NAME, DATASET_NAME, DASHBOARD_TITLE,
    "SALES_REVENUE_KPI", "SALES_REVENUE_TREND",
    "SALES_REVENUE_BY_REGION", "SALES_TOP_CUSTOMERS",
)}

# --------------------------------------------------------------------------
# Semantic layer: metrics defined once, on the dataset, never inline in charts.
# --------------------------------------------------------------------------
METRICS: list[dict[str, Any]] = [
    {
        "metric_name": "revenue",
        "verbose_name": "Revenue",
        "expression": "SUM(CASE WHEN order_status = 'completed' THEN net_amount ELSE 0 END)",
        "description": (
            "Sum of completed order revenue, net of discounts, excluding "
            "cancelled and pending orders."
        ),
        "d3format": "$,.2f",
        "metric_type": "sum",
    },
    {
        "metric_name": "order_count",
        "verbose_name": "Order Count",
        "expression": "COUNT(DISTINCT CASE WHEN order_status = 'completed' THEN order_id END)",
        "description": "Number of distinct completed orders.",
        "d3format": ",d",
        "metric_type": "count_distinct",
    },
    {
        "metric_name": "average_order_value",
        "verbose_name": "Average Order Value",
        "expression": (
            "SUM(CASE WHEN order_status = 'completed' THEN net_amount ELSE 0 END) "
            "/ NULLIF(COUNT(DISTINCT CASE WHEN order_status = 'completed' "
            "THEN order_id END), 0)"
        ),
        "description": "Completed revenue divided by completed order count.",
        "d3format": "$,.2f",
        "metric_type": "avg",
    },
    {
        "metric_name": "gross_margin",
        "verbose_name": "Gross Margin",
        "expression": "SUM(CASE WHEN order_status = 'completed' THEN margin_amount ELSE 0 END)",
        "description": "Completed net revenue less cost of goods sold.",
        "d3format": "$,.2f",
        "metric_type": "sum",
    },
]


def _get_or_create_database(client: SupersetClient, uri: str) -> int:
    for db in client.list_assets("database"):
        if db.get("database_name") == DATABASE_NAME:
            logger.info("database %s already exists (id=%s)", DATABASE_NAME, db["id"])
            return db["id"]
    payload = {
        "database_name": DATABASE_NAME,
        "sqlalchemy_uri": uri,
        "expose_in_sqllab": True,
        "allow_ctas": False,
        "allow_cvas": False,
        "allow_dml": False,
        "uuid": UUIDS[DATABASE_NAME],
    }
    result = client._json("POST", "/api/v1/database/", mutating=True, json=payload)
    logger.info("created database %s (id=%s)", DATABASE_NAME, result["id"])
    return result["id"]


def _get_or_create_dataset(client: SupersetClient, database_id: int) -> int:
    for ds in client.list_assets("dataset"):
        if ds.get("table_name") == DATASET_NAME:
            logger.info("dataset %s already exists (id=%s)", DATASET_NAME, ds["id"])
            return ds["id"]
    payload = {
        "database": database_id,
        "schema": SCHEMA,
        "table_name": DATASET_NAME,
        "sql": (
            "SELECT\n"
            "    order_id,\n"
            "    order_date,\n"
            "    order_status,\n"
            "    customer_id,\n"
            "    customer_name,\n"
            "    region,\n"
            "    segment,\n"
            "    quantity,\n"
            "    gross_amount,\n"
            "    discount_amount,\n"
            "    net_amount,\n"
            "    cost_amount,\n"
            "    margin_amount\n"
            f"FROM {SCHEMA}.{TABLE}"
        ),
        "uuid": UUIDS[DATASET_NAME],
    }
    result = client._json("POST", "/api/v1/dataset/", mutating=True, json=payload)
    logger.info("created dataset %s (id=%s)", DATASET_NAME, result["id"])
    return result["id"]


#: Fields the dataset PUT schema accepts for a metric. Server-managed fields
#: (created_on, changed_on, ...) are rejected with MARSHMALLOW_ERROR if echoed
#: back, so anything read from the API must be filtered through this.
_METRIC_PUT_FIELDS = frozenset({
    "id", "metric_name", "verbose_name", "expression", "description",
    "d3format", "currency", "metric_type", "extra", "warning_text",
})


def _sync_metrics(client: SupersetClient, dataset_id: int) -> None:
    """Attach the semantic metrics to the dataset, preserving existing columns."""
    current = client.get_asset("dataset", dataset_id)
    existing = {m["metric_name"]: m for m in current.get("metrics", [])}
    merged: list[dict[str, Any]] = []
    for spec in METRICS:
        entry = dict(spec)
        if spec["metric_name"] in existing:
            entry["id"] = existing[spec["metric_name"]]["id"]
        merged.append(entry)
    # Keep any metric already present that this script does not own, stripped
    # down to the fields the PUT schema will accept.
    for name, metric in existing.items():
        if name not in {m["metric_name"] for m in METRICS}:
            merged.append({k: v for k, v in metric.items() if k in _METRIC_PUT_FIELDS})
    client._json(
        "PUT", f"/api/v1/dataset/{dataset_id}", mutating=True,
        json={"metrics": merged},
        params={"override_columns": "false"},
    )
    logger.info("synced %d metrics onto dataset %s", len(METRICS), DATASET_NAME)


def _chart_specs(dataset_id: int) -> list[dict[str, Any]]:
    """Chart definitions. Each answers one explicit business question."""
    base = {"datasource": f"{dataset_id}__table", "granularity_sqla": "order_date"}
    return [
        {
            "slice_name": "SALES_REVENUE_KPI",
            "viz_type": "big_number_total",
            "uuid": UUIDS["SALES_REVENUE_KPI"],
            "description": "Business question: what is total completed revenue?",
            "params": {
                **base,
                "viz_type": "big_number_total",
                "metric": "revenue",
                "adhoc_filters": [],
                "y_axis_format": "$,.2f",
                "subheader": "Completed revenue, all time",
            },
        },
        {
            "slice_name": "SALES_REVENUE_TREND",
            "viz_type": "echarts_timeseries_line",
            "uuid": UUIDS["SALES_REVENUE_TREND"],
            "description": "Business question: how is revenue trending month over month?",
            "params": {
                **base,
                "viz_type": "echarts_timeseries_line",
                "x_axis": "order_date",
                "time_grain_sqla": "P1M",
                "metrics": ["revenue"],
                "groupby": [],
                "adhoc_filters": [],
                "row_limit": 1000,
                "y_axis_format": "$,.2f",
                "x_axis_sort_asc": True,
            },
        },
        {
            "slice_name": "SALES_REVENUE_BY_REGION",
            "viz_type": "echarts_timeseries_bar",
            "uuid": UUIDS["SALES_REVENUE_BY_REGION"],
            "description": "Business question: which regions generate the most revenue?",
            "params": {
                **base,
                "viz_type": "echarts_timeseries_bar",
                "x_axis": "region",
                "metrics": ["revenue"],
                "groupby": [],
                "adhoc_filters": [],
                "row_limit": 100,
                "y_axis_format": "$,.2f",
                "orientation": "vertical",
            },
        },
        {
            "slice_name": "SALES_TOP_CUSTOMERS",
            "viz_type": "table",
            "uuid": UUIDS["SALES_TOP_CUSTOMERS"],
            "description": "Business question: who are the highest-revenue customers?",
            "params": {
                **base,
                "viz_type": "table",
                "query_mode": "aggregate",
                "groupby": ["customer_name", "region"],
                "metrics": ["revenue", "order_count", "average_order_value"],
                "adhoc_filters": [],
                "row_limit": 25,
                "order_desc": True,
                "table_timestamp_format": "smart_date",
            },
        },
    ]


def _get_or_create_charts(client: SupersetClient, dataset_id: int) -> dict[str, dict[str, Any]]:
    existing = {c.get("slice_name"): c for c in client.list_assets("chart")}
    created: dict[str, dict[str, Any]] = {}
    for spec in _chart_specs(dataset_id):
        name = spec["slice_name"]
        if name in existing:
            logger.info("chart %s already exists (id=%s)", name, existing[name]["id"])
            created[name] = {"id": existing[name]["id"], "uuid": spec["uuid"]}
            continue
        payload = {
            "slice_name": name,
            "viz_type": spec["viz_type"],
            "datasource_id": dataset_id,
            "datasource_type": "table",
            "description": spec["description"],
            "params": json.dumps(spec["params"]),
            "uuid": spec["uuid"],
        }
        result = client._json("POST", "/api/v1/chart/", mutating=True, json=payload)
        logger.info("created chart %s (id=%s)", name, result["id"])
        created[name] = {"id": result["id"], "uuid": spec["uuid"]}
    return created


def _build_position(charts: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Dashboard layout following the house standard.

    Visual hierarchy: KPI summary -> trend -> breakdown -> detail table.
    """
    def chart_node(key: str, name: str, width: int, height: int, parent: str) -> dict[str, Any]:
        return {
            "type": "CHART",
            "id": key,
            "children": [],
            "parents": ["ROOT_ID", "GRID_ID", parent],
            "meta": {
                # Both keys are mandatory. Superset's exporter resolves chart
                # dependencies through build_uuid_to_id_map(), which reads
                # meta["uuid"] AND meta["chartId"]; omitting chartId makes
                # `GET /api/v1/dashboard/export/` fail with a 500 (KeyError).
                # chartId is runtime-local and is stripped during normalization.
                "uuid": charts[name]["uuid"],
                "chartId": charts[name]["id"],
                "sliceName": name,
                "width": width,
                "height": height,
            },
        }

    return {
        "DASHBOARD_VERSION_KEY": "v2",
        "ROOT_ID": {"type": "ROOT", "id": "ROOT_ID", "children": ["GRID_ID"]},
        "GRID_ID": {
            "type": "GRID",
            "id": "GRID_ID",
            "children": ["ROW-kpi", "ROW-trend", "ROW-detail"],
            "parents": ["ROOT_ID"],
        },
        "HEADER_ID": {
            "type": "HEADER",
            "id": "HEADER_ID",
            "meta": {"text": DASHBOARD_TITLE},
        },
        "ROW-kpi": {
            "type": "ROW",
            "id": "ROW-kpi",
            "children": ["CHART-kpi"],
            "parents": ["ROOT_ID", "GRID_ID"],
            "meta": {"background": "BACKGROUND_TRANSPARENT"},
        },
        "ROW-trend": {
            "type": "ROW",
            "id": "ROW-trend",
            "children": ["CHART-trend", "CHART-region"],
            "parents": ["ROOT_ID", "GRID_ID"],
            "meta": {"background": "BACKGROUND_TRANSPARENT"},
        },
        "ROW-detail": {
            "type": "ROW",
            "id": "ROW-detail",
            "children": ["CHART-customers"],
            "parents": ["ROOT_ID", "GRID_ID"],
            "meta": {"background": "BACKGROUND_TRANSPARENT"},
        },
        "CHART-kpi": chart_node("CHART-kpi", "SALES_REVENUE_KPI", 4, 50, "ROW-kpi"),
        "CHART-trend": chart_node("CHART-trend", "SALES_REVENUE_TREND", 6, 50, "ROW-trend"),
        "CHART-region": chart_node("CHART-region", "SALES_REVENUE_BY_REGION", 6, 50, "ROW-trend"),
        "CHART-customers": chart_node(
            "CHART-customers", "SALES_TOP_CUSTOMERS", 12, 60, "ROW-detail"
        ),
    }


def _build_metadata(
    charts: dict[str, dict[str, Any]], dataset_id: int, dataset_uuid: str
) -> dict[str, Any]:
    """json_metadata including a native filter on region."""
    return {
        "color_scheme": "supersetColors",
        "refresh_frequency": 0,
        "expanded_slices": {},
        "label_colors": {},
        "cross_filters_enabled": True,
        "native_filter_configuration": [
            {
                "id": "NATIVE_FILTER-region",
                "name": "Region",
                "filterType": "filter_select",
                # datasetUuid is what find_native_filter_datasets() reads to pull
                # the dataset into the export bundle; datasetId is the runtime
                # pointer the UI uses and is stripped during normalization.
                "targets": [{
                    "datasetId": dataset_id,
                    "datasetUuid": dataset_uuid,
                    "column": {"name": "region"},
                }],
                "defaultDataMask": {"extraFormData": {}, "filterState": {}, "ownState": {}},
                "controlValues": {
                    "multiSelect": True,
                    "enableEmptyFilter": False,
                    "searchAllOptions": False,
                },
                "scope": {"rootPath": ["ROOT_ID"], "excluded": []},
                "type": "NATIVE_FILTER",
                "description": "Restrict every chart to the selected sales regions.",
            }
        ],
    }


def _get_or_create_dashboard(
    client: SupersetClient, charts: dict[str, dict[str, Any]],
    dataset_id: int, dataset_uuid: str,
) -> int:
    existing = client.find_dashboard_by_title(DASHBOARD_TITLE)
    payload = {
        "dashboard_title": DASHBOARD_TITLE,
        "slug": "sales-executive-overview",
        "published": True,
        "position_json": json.dumps(_build_position(charts)),
        "json_metadata": json.dumps(_build_metadata(charts, dataset_id, dataset_uuid)),
        "certified_by": "Data Platform Team",
        "certification_details": "Validated against sales.vw_order_daily.",
    }
    if existing:
        client._json(
            "PUT", f"/api/v1/dashboard/{existing['id']}", mutating=True, json=payload
        )
        logger.info("updated dashboard %s (id=%s)", DASHBOARD_TITLE, existing["id"])
        return existing["id"]
    payload["uuid"] = UUIDS[DASHBOARD_TITLE]
    result = client._json("POST", "/api/v1/dashboard/", mutating=True, json=payload)
    logger.info("created dashboard %s (id=%s)", DASHBOARD_TITLE, result["id"])
    return result["id"]


def _link_charts_to_dashboard(
    client: SupersetClient, charts: dict[str, dict[str, Any]], dashboard_id: int
) -> None:
    """Associate charts with the dashboard through the chart -> dashboards m2m.

    This is required, not cosmetic: ExportDashboardsCommand derives the charts
    to bundle from `model.slices` (superset/commands/dashboard/export.py:188),
    NOT from position_json. A dashboard whose position references charts that
    are not in `slices` exports with no charts/ directory at all.

    The dashboard PUT schema has no `slices` field, so the link is made from
    the chart side, which is also what the Superset UI does.
    """
    for name, info in charts.items():
        current = client.get_asset("chart", info["id"])
        linked = {d["id"] for d in current.get("dashboards", []) if isinstance(d, dict)}
        if dashboard_id in linked:
            continue
        client._json(
            "PUT", f"/api/v1/chart/{info['id']}", mutating=True,
            json={"dashboards": sorted(linked | {dashboard_id})},
        )
        logger.info("linked chart %s to dashboard %s", name, dashboard_id)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database-uri",
        default=os.environ.get("DATABASE_URI"),
        help="SQLAlchemy URI for the warehouse (defaults to $DATABASE_URI).",
    )
    args = parser.parse_args()
    if not args.database_uri:
        logger.error("DATABASE_URI is not set; cannot create the warehouse connection.")
        return 2

    client = SupersetClient.from_env().login()
    logger.info("authenticated as %s", client.me().get("username"))

    database_id = _get_or_create_database(client, args.database_uri)
    dataset_id = _get_or_create_dataset(client, database_id)
    _sync_metrics(client, dataset_id)
    charts = _get_or_create_charts(client, dataset_id)
    dataset_uuid = client.get_asset("dataset", dataset_id).get("uuid") or UUIDS[DATASET_NAME]
    dashboard_id = _get_or_create_dashboard(client, charts, dataset_id, dataset_uuid)
    _link_charts_to_dashboard(client, charts, dashboard_id)

    print(json.dumps({
        "database_id": database_id,
        "dataset_id": dataset_id,
        "chart_ids": {k: v["id"] for k, v in charts.items()},
        "dashboard_id": dashboard_id,
    }, indent=2))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SupersetError as exc:
        logger.error("%s", exc)
        sys.exit(1)
