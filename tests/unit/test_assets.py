# Licensed under the Apache License, Version 2.0
"""Validation of the committed assets themselves.

These run the same checks CI runs, so a bad asset fails locally before it ever
reaches a pull request.
"""

from __future__ import annotations

import uuid as uuid_lib

import pytest

from scripts.lib.normalize import load_yaml
from scripts.validate import (
    _iter_assets,
    check_metrics,
    check_naming,
    check_placeholders,
    check_references,
    check_structure,
    check_uuids,
)

pytestmark = pytest.mark.unit


@pytest.fixture(scope="module")
def assets():
    return _iter_assets()


class TestCommittedAssets:
    def test_assets_exist(self, assets):
        assert assets, "no assets committed"

    def test_structure_is_valid(self, assets):
        assert check_structure(assets) == []

    def test_uuids_are_valid_and_unique(self, assets):
        assert check_uuids(assets) == []

    def test_names_follow_conventions(self, assets):
        assert check_naming(assets) == []

    def test_metrics_are_documented_and_executable(self, assets):
        assert check_metrics(assets) == []

    def test_all_references_resolve(self, assets):
        assert check_references(assets) == []

    def test_placeholders_resolve_in_every_environment(self, assets):
        assert check_placeholders(assets, None) == []


class TestUuidIdentity:
    def test_every_asset_carries_a_uuid(self, assets):
        """UUID is the cross-environment identity; a primary key is not."""
        for path, config in assets:
            if path in {"metadata.yaml", "tags.yaml"}:
                continue
            assert "uuid" in config, f"{path} has no uuid"
            uuid_lib.UUID(str(config["uuid"]))

    def test_dashboard_chart_nodes_keep_chart_id(self, assets_dir):
        """Superset's importer reads meta['chartId'] unconditionally."""
        for path in (assets_dir / "dashboards").glob("*.yaml"):
            config = load_yaml(path.read_bytes())
            for node in (config.get("position") or {}).values():
                if isinstance(node, dict) and node.get("type") == "CHART":
                    assert "chartId" in node["meta"], f"{path.name}:{node.get('id')}"
                    assert "uuid" in node["meta"]


class TestDeterminism:
    def test_no_asset_contains_an_export_timestamp(self, assets):
        for path, config in assets:
            assert "timestamp" not in config, f"{path} carries a volatile timestamp"

    def test_no_chart_carries_a_query_context(self, assets):
        for path, config in assets:
            if path.startswith("charts/"):
                assert not config.get("query_context"), \
                    f"{path} carries derived query_context"

    def test_no_chart_carries_a_numeric_datasource_pointer(self, assets):
        for path, config in assets:
            if path.startswith("charts/"):
                assert "datasource" not in (config.get("params") or {}), \
                    f"{path} carries an environment-specific datasource id"
