# Licensed under the Apache License, Version 2.0
"""Integration tests for the GitOps loop against a live Superset.

These are the tests that would have caught the two real bugs found while
building this platform:
  * `/api/v1/dashboard/import/` silently not overwriting existing charts, so a
    redeploy did not correct drift;
  * the importer adding `annotation_layers: []`, so export(import(x)) != x.

Requires a running Superset with the reference assets deployed.
"""

from __future__ import annotations

import pytest

from scripts.export import collect_bundle
from scripts.lib.deploy import build_passwords, deploy, read_assets, resolve_bundle
from scripts.lib.environment import client_for, load_environment
from scripts.lib.normalize import load_yaml, normalize_bundle

pytestmark = pytest.mark.integration

ENV_NAME = "dev"


@pytest.fixture(scope="module")
def env():
    return load_environment(ENV_NAME)


@pytest.fixture(scope="module")
def client(env, superset_client):
    _ = superset_client  # ensures the skip-if-unreachable guard runs first
    return client_for(env)


@pytest.fixture(scope="module")
def live_bundle(client):
    return normalize_bundle(collect_bundle(client, None))


class TestDependencyChain:
    """database -> dataset -> metric -> chart -> dashboard must fully resolve."""

    def test_database_exists(self, live_bundle):
        assert any(p.startswith("databases/") for p in live_bundle)

    def test_dataset_references_a_real_database(self, live_bundle):
        database_uuids = {
            str(load_yaml(c)["uuid"])
            for p, c in live_bundle.items() if p.startswith("databases/")
        }
        datasets = [load_yaml(c) for p, c in live_bundle.items()
                    if p.startswith("datasets/")]
        assert datasets
        for dataset in datasets:
            assert str(dataset["database_uuid"]) in database_uuids

    def test_dataset_defines_the_semantic_metrics(self, live_bundle):
        datasets = [load_yaml(c) for p, c in live_bundle.items()
                    if p.startswith("datasets/")]
        names = {m["metric_name"] for d in datasets for m in d.get("metrics") or []}
        assert {"revenue", "order_count", "average_order_value", "gross_margin"} <= names

    def test_chart_references_a_real_dataset(self, live_bundle):
        dataset_uuids = {
            str(load_yaml(c)["uuid"])
            for p, c in live_bundle.items() if p.startswith("datasets/")
        }
        charts = [load_yaml(c) for p, c in live_bundle.items()
                  if p.startswith("charts/")]
        assert charts
        for chart in charts:
            assert str(chart["dataset_uuid"]) in dataset_uuids

    def test_dashboard_references_real_charts(self, live_bundle):
        chart_uuids = {
            str(load_yaml(c)["uuid"])
            for p, c in live_bundle.items() if p.startswith("charts/")
        }
        dashboards = [load_yaml(c) for p, c in live_bundle.items()
                      if p.startswith("dashboards/")]
        assert dashboards
        for dashboard in dashboards:
            for node in (dashboard.get("position") or {}).values():
                if isinstance(node, dict) and node.get("type") == "CHART":
                    assert str(node["meta"]["uuid"]) in chart_uuids


class TestExportDeterminism:
    def test_two_consecutive_exports_are_byte_identical(self, client):
        """Non-determinism here makes every pull request unreadable."""
        first = normalize_bundle(collect_bundle(client, None))
        second = normalize_bundle(collect_bundle(client, None))
        assert first == second

    def test_export_matches_committed_assets(self, live_bundle):
        """Superset and Git agree - i.e. no drift and normalization is stable."""
        committed = read_assets()
        for path, content in live_bundle.items():
            if path == "metadata.yaml":
                continue
            assert path in committed, f"{path} exported but not committed"
            assert committed[path] == content, f"{path} differs from Git"


class TestImportIdempotency:
    def test_repeated_import_creates_no_duplicates(self, client, env):
        """Identity is the UUID; re-deploying must update, never insert."""
        before = {k: len(client.list_assets(k))
                  for k in ("database", "dataset", "chart", "dashboard")}
        files = read_assets()
        for _ in range(2):
            deploy(client, resolve_bundle(files, env), build_passwords(files, env))
        after = {k: len(client.list_assets(k))
                 for k in ("database", "dataset", "chart", "dashboard")}
        assert before == after

    def test_uuids_survive_reimport(self, client, env):
        """A changed UUID would break every dashboard reference to the chart."""
        before = {
            str(client.get_asset("chart", c["id"])["uuid"])
            for c in client.list_assets("chart")
        }
        files = read_assets()
        deploy(client, resolve_bundle(files, env), build_passwords(files, env))
        after = {
            str(client.get_asset("chart", c["id"])["uuid"])
            for c in client.list_assets("chart")
        }
        assert before == after


class TestRoundTrip:
    def test_export_after_import_equals_export_before(self, client, env):
        """The core GitOps invariant: export(import(x)) == x.

        This is what caught the importer's filter_chart_annotations() rewrite.
        """
        before = normalize_bundle(collect_bundle(client, None))
        files = read_assets()
        deploy(client, resolve_bundle(files, env), build_passwords(files, env))
        after = normalize_bundle(collect_bundle(client, None))
        assert {k: v for k, v in before.items() if k != "metadata.yaml"} == \
               {k: v for k, v in after.items() if k != "metadata.yaml"}


class TestPlaceholderResolution:
    def test_no_placeholder_survives_into_the_deployed_instance(self, client):
        """A literal "${WAREHOUSE_URI}" in Superset means a broken connection."""
        for item in client.list_assets("database"):
            database = client.get_asset("database", item["id"])
            assert "${" not in str(database.get("sqlalchemy_uri", ""))
            assert "${" not in str(database.get("database_name", ""))

    def test_environment_suffix_is_applied(self, client):
        """assets/ holds SALES_DWH_${ENV}; DEV must materialise SALES_DWH_DEV."""
        names = {d.get("database_name") for d in client.list_assets("database")}
        assert "SALES_DWH_DEV" in names
