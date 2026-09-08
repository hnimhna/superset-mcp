# Licensed under the Apache License, Version 2.0
"""Unit tests for the normalization engine.

These encode the rules that make `git diff` meaningful. Each test names the
failure it prevents, because a normalizer regression is silent: the repo keeps
working, the diffs just become useless or the import starts failing.
"""

from __future__ import annotations

import pytest

from scripts.lib.normalize import (
    UnresolvedPlaceholderError,
    canonical_filename,
    canonical_name,
    dump_yaml,
    find_placeholders,
    load_yaml,
    normalize_bundle,
    normalize_chart,
    normalize_dashboard,
    normalize_database,
    normalize_dataset,
    normalize_metadata,
    resolve_placeholders,
    stable_chart_id,
)

pytestmark = pytest.mark.unit


class TestDeterministicSerialization:
    def test_key_order_does_not_affect_output(self):
        """Two dicts differing only in insertion order must serialize identically."""
        a = {"b": 1, "a": 2, "c": {"z": 1, "y": 2}}
        b = {"a": 2, "c": {"y": 2, "z": 1}, "b": 1}
        assert dump_yaml(a) == dump_yaml(b)

    def test_round_trips_through_yaml(self):
        original = {"name": "x", "nested": {"list": [1, 2, 3]}, "flag": True}
        assert load_yaml(dump_yaml(original)) == original

    def test_no_yaml_aliases_emitted(self):
        """Repeated objects must be written out, not turned into *id references."""
        shared = {"k": "v"}
        text = dump_yaml({"one": shared, "two": shared})
        assert "&id" not in text and "*id" not in text


class TestMetadata:
    def test_timestamp_is_removed(self):
        """metadata.yaml gets a fresh timestamp on every export - pure diff noise."""
        result = normalize_metadata(
            {"version": "1.0.0", "type": "Dashboard",
             "timestamp": "2026-09-08T11:34:08.422175+00:00"}
        )
        assert "timestamp" not in result
        assert result["type"] == "Dashboard"


class TestDatabase:
    def test_credentials_are_replaced_with_a_placeholder(self):
        result = normalize_database({
            "database_name": "SALES_DWH_DEV",
            "sqlalchemy_uri": "postgresql+psycopg2://user:hunter2@host:5432/dwh",
            "uuid": "u",
        })
        assert result["sqlalchemy_uri"] == "${WAREHOUSE_URI}"
        assert "hunter2" not in dump_yaml(result)

    def test_masked_password_is_also_replaced(self):
        """Superset exports the password as XXXXXXXXXX, which is still env-specific."""
        result = normalize_database({
            "database_name": "SALES_DWH_DEV",
            "sqlalchemy_uri": "postgresql+psycopg2://u:XXXXXXXXXX@warehouse-db:5432/dwh",
        })
        assert result["sqlalchemy_uri"] == "${WAREHOUSE_URI}"

    @pytest.mark.parametrize(
        "name,expected",
        [("SALES_DWH_DEV", "SALES_DWH_${ENV}"),
         ("SALES_DWH_PROD", "SALES_DWH_${ENV}"),
         ("FINANCE_DWH_STAGING", "FINANCE_DWH_${ENV}")],
    )
    def test_environment_suffix_becomes_a_placeholder(self, name, expected):
        """One file must serve every environment; identity is the UUID."""
        assert normalize_database({"database_name": name})["database_name"] == expected

    def test_already_normalized_input_is_stable(self):
        once = normalize_database({
            "database_name": "SALES_DWH_DEV",
            "sqlalchemy_uri": "postgresql://u:p@h/d",
        })
        assert normalize_database(once) == once


class TestChart:
    def test_query_context_is_dropped(self):
        """query_context is a derived cache full of numeric datasource IDs."""
        result = normalize_chart({
            "slice_name": "SALES_X_TREND",
            "query_context": '{"datasource":{"id":1,"type":"table"}}',
            "params": {},
        })
        assert result["query_context"] is None

    def test_numeric_datasource_pointer_is_removed(self):
        result = normalize_chart({"params": {"datasource": "17__table", "metrics": []}})
        assert "datasource" not in result["params"]

    def test_annotation_layers_key_is_always_present(self):
        """Mirrors the importer's filter_chart_annotations().

        Without this, every chart gains a spurious `annotation_layers: []` on
        its first round trip through import.
        """
        assert normalize_chart({"params": {}})["params"]["annotation_layers"] == []

    def test_non_formula_annotation_layers_are_filtered(self):
        """The importer keeps only FORMULA layers, so normalization must too."""
        result = normalize_chart({"params": {"annotation_layers": [
            {"name": "keep", "annotationType": "FORMULA"},
            {"name": "drop", "annotationType": "INTERVAL"},
        ]}})
        assert [layer["name"] for layer in result["params"]["annotation_layers"]] == ["keep"]

    def test_is_idempotent(self):
        once = normalize_chart({"params": {"datasource": "1__table"}, "query_context": "{}"})
        assert normalize_chart(once) == once


class TestDashboard:
    @staticmethod
    def _dashboard(chart_id: int = 42):
        return {
            "dashboard_title": "SALES_EXECUTIVE_OVERVIEW",
            "position": {
                "GRID_ID": {"type": "GRID", "id": "GRID_ID", "children": ["CHART-a"]},
                "CHART-a": {
                    "type": "CHART", "id": "CHART-a",
                    "meta": {"uuid": "aaaaaaaa-0000-0000-0000-000000000001",
                             "chartId": chart_id, "sliceName": "SALES_X_TREND"},
                },
            },
            "metadata": {
                "expanded_slices": {str(chart_id): True},
                "timed_refresh_immune_slices": [chart_id],
                "native_filter_configuration": [{
                    "id": "NATIVE_FILTER-1",
                    "targets": [{"datasetId": 7, "datasetUuid": "dddd-uuid"}],
                    "scope": {"excluded": [chart_id]},
                }],
            },
        }

    def test_chart_id_is_replaced_with_a_uuid_derived_value(self):
        result = normalize_dashboard(self._dashboard(42))
        new_id = result["position"]["CHART-a"]["meta"]["chartId"]
        assert new_id != 42
        assert new_id == stable_chart_id("aaaaaaaa-0000-0000-0000-000000000001")

    def test_chart_id_is_never_removed(self):
        """The importer's build_uuid_to_id_map() reads meta['chartId'] directly.

        Deleting the key makes `POST /api/v1/assets/import/` raise KeyError.
        """
        result = normalize_dashboard(self._dashboard())
        assert "chartId" in result["position"]["CHART-a"]["meta"]

    def test_same_dashboard_from_two_environments_normalizes_identically(self):
        """The whole point: DEV primary key 42 and PROD primary key 991 converge."""
        assert normalize_dashboard(self._dashboard(42)) == \
               normalize_dashboard(self._dashboard(991))

    def test_metadata_chart_references_are_remapped_consistently(self):
        result = normalize_dashboard(self._dashboard(42))
        expected = stable_chart_id("aaaaaaaa-0000-0000-0000-000000000001")
        metadata = result["metadata"]
        assert list(metadata["expanded_slices"]) == [str(expected)]
        assert metadata["timed_refresh_immune_slices"] == [expected]
        assert metadata["native_filter_configuration"][0]["scope"]["excluded"] == [expected]

    def test_runtime_dataset_id_is_dropped_when_uuid_is_present(self):
        result = normalize_dashboard(self._dashboard())
        target = result["metadata"]["native_filter_configuration"][0]["targets"][0]
        assert "datasetId" not in target
        assert target["datasetUuid"] == "dddd-uuid"

    def test_is_idempotent(self):
        once = normalize_dashboard(self._dashboard())
        assert normalize_dashboard(once) == once


class TestStableChartId:
    def test_is_deterministic(self):
        assert stable_chart_id("abc") == stable_chart_id("abc")

    def test_differs_per_uuid(self):
        assert stable_chart_id("abc") != stable_chart_id("abd")

    def test_is_a_positive_32_bit_safe_integer(self):
        value = stable_chart_id("some-uuid")
        assert 0 < value < 2**31


class TestDataset:
    def test_columns_and_metrics_are_sorted(self):
        """Superset returns these in arbitrary order; unsorted output churns diffs."""
        result = normalize_dataset({
            "columns": [{"column_name": "b"}, {"column_name": "a"}],
            "metrics": [{"metric_name": "z"}, {"metric_name": "y"}],
        })
        assert [c["column_name"] for c in result["columns"]] == ["a", "b"]
        assert [m["metric_name"] for m in result["metrics"]] == ["y", "z"]


class TestCanonicalPaths:
    @pytest.mark.parametrize(
        "raw,expected",
        [("SALES_DWH_${ENV}", "SALES_DWH"),
         ("SALES_DWH_DEV", "SALES_DWH"),
         ("SALES_DWH_PROD", "SALES_DWH"),
         ("SALES_ORDER_DAILY", "SALES_ORDER_DAILY")],
    )
    def test_environment_independent_names(self, raw, expected):
        assert canonical_name(raw) == expected

    def test_numeric_primary_key_is_stripped_from_filenames(self):
        assert canonical_filename(
            "charts/SALES_REVENUE_TREND_2.yaml", {"slice_name": "SALES_REVENUE_TREND"}
        ) == "charts/SALES_REVENUE_TREND.yaml"

    def test_dataset_directory_is_environment_independent(self):
        """Otherwise the same dataset lands in a different path per environment."""
        assert canonical_filename(
            "datasets/SALES_DWH_DEV/SALES_ORDER_DAILY_1.yaml",
            {"table_name": "SALES_ORDER_DAILY"},
        ) == "datasets/SALES_DWH/SALES_ORDER_DAILY.yaml"


class TestPlaceholders:
    def test_finds_nested_placeholders(self):
        found = find_placeholders(
            {"a": "${ONE}", "b": ["${TWO}"], "c": {"d": "x${THREE}y"}}
        )
        assert found == {"ONE", "TWO", "THREE"}

    def test_resolves_from_context(self):
        result = resolve_placeholders(
            {"uri": "${WAREHOUSE_URI}", "name": "DB_${ENV}"},
            {"WAREHOUSE_URI": "postgresql://h/d", "ENV": "PROD"},
        )
        assert result == {"uri": "postgresql://h/d", "name": "DB_PROD"}

    def test_unknown_placeholder_raises_rather_than_deploying_broken_assets(self):
        with pytest.raises(UnresolvedPlaceholderError, match="MISSING"):
            resolve_placeholders({"uri": "${MISSING}"}, {"ENV": "DEV"})


class TestBundle:
    def test_normalize_bundle_is_idempotent(self):
        """export -> normalize -> normalize must equal export -> normalize."""
        bundle = {
            "metadata.yaml": b"version: 1.0.0\ntype: Dashboard\ntimestamp: '2026-01-01'\n",
            "charts/SALES_X_TREND_9.yaml": dump_yaml({
                "slice_name": "SALES_X_TREND",
                "params": {"datasource": "9__table"},
                "query_context": "{}",
                "uuid": "c-uuid",
            }).encode(),
        }
        once = normalize_bundle(bundle)
        assert normalize_bundle(once) == once

    def test_filenames_are_canonicalized(self):
        bundle = {
            "charts/SALES_X_TREND_9.yaml": dump_yaml(
                {"slice_name": "SALES_X_TREND", "params": {}, "uuid": "u"}
            ).encode(),
        }
        assert set(normalize_bundle(bundle)) == {"charts/SALES_X_TREND.yaml"}
