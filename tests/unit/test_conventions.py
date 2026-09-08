# Licensed under the Apache License, Version 2.0
"""Naming convention enforcement.

The conventions are documented in docs/naming-convention.md. These tests are
what stop them from being aspirational.
"""

from __future__ import annotations

import pytest

from scripts.lib.conventions import (
    check_chart_name,
    check_dashboard_name,
    check_database_name,
    check_dataset_name,
    check_metric_name,
)

pytestmark = pytest.mark.unit


class TestDatabaseNames:
    @pytest.mark.parametrize("name", [
        "SALES_DWH_PROD", "FINANCE_DWH_DEV", "SALES_DWH_${ENV}",
        "MARKETING_LAKEHOUSE_STAGING",
    ])
    def test_accepts_conforming_names(self, name):
        assert check_database_name(name, "p") == []

    @pytest.mark.parametrize("name", [
        "sales_dwh_prod",     # lowercase
        "SALES_DWH",          # no environment
        "UNKNOWNDOMAIN_DWH_PROD",
        "SALES_DWH_QA",       # not a known environment
        "",
    ])
    def test_rejects_non_conforming_names(self, name):
        assert check_database_name(name, "p") != []


class TestDatasetNames:
    @pytest.mark.parametrize("name", ["SALES_ORDER_DAILY", "FINANCE_REVENUE_MONTHLY"])
    def test_accepts_conforming_names(self, name):
        assert check_dataset_name(name, "p") == []

    @pytest.mark.parametrize("name", ["orders", "SALES", "sales_order_daily", "TMP_TABLE"])
    def test_rejects_non_conforming_names(self, name):
        assert check_dataset_name(name, "p") != []


class TestChartNames:
    @pytest.mark.parametrize("name", [
        "SALES_REVENUE_TREND", "SALES_REVENUE_BY_REGION", "SALES_TOP_CUSTOMERS",
    ])
    def test_accepts_conforming_names(self, name):
        assert check_chart_name(name, "p") == []

    @pytest.mark.parametrize("name", ["Untitled", "new chart", "copy of X", "chart1"])
    def test_rejects_placeholder_and_ad_hoc_names(self, name):
        assert check_chart_name(name, "p") != []

    def test_placeholder_names_are_reported_distinctly(self):
        """A reviewer should see "this is a leftover default", not a regex dump."""
        issues = check_chart_name("Untitled", "p")
        assert issues[0].rule.endswith("placeholder")


class TestDashboardNames:
    @pytest.mark.parametrize("name", ["SALES_EXECUTIVE_OVERVIEW", "FINANCE_PERFORMANCE"])
    def test_accepts_conforming_names(self, name):
        assert check_dashboard_name(name, "p") == []

    @pytest.mark.parametrize("name", ["Untitled Dashboard", "my dash", "test"])
    def test_rejects_non_conforming_names(self, name):
        assert check_dashboard_name(name, "p") != []


class TestMetricNames:
    @pytest.mark.parametrize("name", [
        "revenue", "gross_margin", "order_count", "average_order_value",
    ])
    def test_accepts_semantic_names(self, name):
        assert check_metric_name(name, "p") == []

    @pytest.mark.parametrize("name", [
        "metric1", "test_metric", "aaa", "temp_metric", "foo", "untitled",
    ])
    def test_rejects_throwaway_names(self, name):
        """These are the names the spec explicitly forbids."""
        assert check_metric_name(name, "p") != []

    @pytest.mark.parametrize("name", ["Revenue", "GROSS_MARGIN", "order-count", "1metric"])
    def test_rejects_non_snake_case(self, name):
        assert check_metric_name(name, "p") != []
