# Licensed under the Apache License, Version 2.0
"""Superset REST API tests: authentication, assets, SQL, import/export.

Every test covers both the happy path and the corresponding failure path,
because an API client that only works when everything is fine will fail
silently in CI.

Requires a running Superset; skips cleanly otherwise (see tests/conftest.py).
"""

from __future__ import annotations

import uuid as uuid_lib
import zipfile
import io

import pytest

from scripts.lib.superset_client import SupersetClient, SupersetError

pytestmark = [pytest.mark.api, pytest.mark.integration]


class TestAuthentication:
    def test_login_succeeds_with_valid_credentials(self, superset_client):
        assert superset_client.me().get("username")

    def test_login_fails_with_invalid_credentials(self, superset_client):
        bad = SupersetClient(
            base_url=superset_client.base_url,
            username="definitely-not-a-user",
            password="definitely-not-a-password",
        )
        with pytest.raises(SupersetError, match="Login failed"):
            bad.login()

    def test_unauthenticated_request_is_rejected(self, superset_client):
        anonymous = SupersetClient(
            base_url=superset_client.base_url, username="x", password="x"
        )
        response = anonymous._request("GET", "/api/v1/chart/")
        assert response.status_code in (401, 403)

    def test_csrf_token_is_obtained(self, superset_client):
        """Mutating calls need it; its absence would surface as confusing 400s."""
        assert superset_client._csrf_token


class TestAssetEndpoints:
    @pytest.mark.parametrize("kind", ["database", "dataset", "chart", "dashboard"])
    def test_list_returns_results(self, superset_client, kind):
        assert superset_client.list_assets(kind), f"no {kind}s found"

    @pytest.mark.parametrize("kind", ["database", "dataset", "chart", "dashboard"])
    def test_every_object_exposes_a_uuid(self, superset_client, kind):
        """UUID is the identity the GitOps model depends on."""
        for item in superset_client.list_assets(kind):
            full = superset_client.get_asset(kind, item["id"])
            uuid_lib.UUID(str(full["uuid"]))

    def test_get_missing_object_returns_404(self, superset_client):
        with pytest.raises(SupersetError):
            superset_client.get_asset("chart", 99_999_999)


class TestSqlExecution:
    def test_executes_sql_successfully(self, superset_client):
        database = superset_client.list_assets("database")[0]
        result = superset_client.run_sql(database["id"], "SELECT 1 AS one")
        assert result["data"][0]["one"] == 1

    def test_invalid_sql_is_reported(self, superset_client):
        database = superset_client.list_assets("database")[0]
        with pytest.raises(SupersetError):
            superset_client.run_sql(database["id"], "SELECT * FROM no_such_table_xyz")

    def test_query_against_missing_database_fails(self, superset_client):
        with pytest.raises(SupersetError):
            superset_client.run_sql(99_999_999, "SELECT 1")


class TestChartExecution:
    def test_every_chart_query_runs(self, superset_client):
        """A chart can exist and still be broken; this proves the SQL executes."""
        charts = superset_client.list_assets("chart")
        assert charts
        for meta in charts:
            chart = superset_client.get_asset("chart", meta["id"])
            chart.setdefault("slice_name", meta.get("slice_name"))
            payload = superset_client.execute_chart_query(chart)
            assert payload["result"][0]["rowcount"] >= 0


class TestExport:
    def test_export_returns_a_zip_bundle(self, superset_client):
        dashboard = superset_client.list_assets("dashboard")[0]
        data = superset_client.export_assets("dashboard", [dashboard["id"]])
        assert data.startswith(b"PK")
        with zipfile.ZipFile(io.BytesIO(data)) as bundle:
            assert bundle.namelist()

    def test_export_includes_the_full_dependency_closure(self, superset_client):
        dashboard = superset_client.list_assets("dashboard")[0]
        files = superset_client.unpack_zip(
            superset_client.export_assets("dashboard", [dashboard["id"]])
        )
        prefixes = {path.split("/")[0] for path in files}
        assert {"dashboards", "charts", "datasets", "databases"} <= prefixes

    def test_export_strips_the_timestamped_wrapper_directory(self, superset_client):
        """unpack_zip must remove `dashboard_export_<ts>/` or every path churns."""
        dashboard = superset_client.list_assets("dashboard")[0]
        files = superset_client.unpack_zip(
            superset_client.export_assets("dashboard", [dashboard["id"]])
        )
        assert all(not path.startswith("dashboard_export") for path in files)

    def test_exported_database_uri_is_masked(self, superset_client):
        """Superset never exports a usable password; normalization then removes it."""
        dashboard = superset_client.list_assets("dashboard")[0]
        files = superset_client.unpack_zip(
            superset_client.export_assets("dashboard", [dashboard["id"]])
        )
        for path, content in files.items():
            if path.startswith("databases/"):
                assert b"XXXXXXXXXX" in content or b"${" in content

    def test_exporting_nothing_is_rejected(self, superset_client):
        with pytest.raises(SupersetError, match="zero"):
            superset_client.export_assets("dashboard", [])


class TestImport:
    def test_rejects_a_bundle_that_is_not_a_zip(self, superset_client):
        response = superset_client._request(
            "POST", "/api/v1/assets/import/", mutating=True,
            files={"bundle": ("x.zip", b"not a zip", "application/zip")},
        )
        assert response.status_code >= 400

    def test_rejects_a_bundle_with_no_metadata(self, superset_client):
        with pytest.raises(SupersetError):
            superset_client.import_assets(
                "assets", {"charts/x.yaml": b"slice_name: X\n"}
            )
