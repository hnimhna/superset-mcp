# Licensed under the Apache License, Version 2.0
"""Thin, dependency-light client for the Superset REST API.

Only the endpoints the GitOps workflow needs are wrapped. The client is used
by export/import/diff/smoke-test and by the API test-suite, so it deliberately
surfaces raw responses rather than hiding failures.

Authentication follows Superset's documented flow:
    POST /api/v1/security/login   -> access_token
    GET  /api/v1/security/csrf_token/ -> csrf token (+ session cookie)
Both the bearer token and the CSRF token are required for mutating calls.
"""

from __future__ import annotations

import io
import logging
import os
import zipfile
from dataclasses import dataclass, field
from typing import Any, Iterable

import requests

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 120


class SupersetError(RuntimeError):
    """Raised when the Superset API returns an unexpected response."""

    def __init__(self, message: str, response: requests.Response | None = None) -> None:
        self.response = response
        if response is not None:
            body = (response.text or "")[:2000]
            message = f"{message} [HTTP {response.status_code}] {body}"
        super().__init__(message)


@dataclass
class SupersetClient:
    """Authenticated session against one Superset instance."""

    base_url: str
    username: str
    password: str
    provider: str = "db"
    timeout: int = DEFAULT_TIMEOUT
    verify_tls: bool = True

    session: requests.Session = field(default_factory=requests.Session, repr=False)
    _access_token: str | None = field(default=None, repr=False)
    _csrf_token: str | None = field(default=None, repr=False)

    # -- construction -------------------------------------------------------
    @classmethod
    def from_env(cls, prefix: str = "SUPERSET") -> "SupersetClient":
        """Build a client from environment variables.

        Credentials are never read from version-controlled files.
        """
        base_url = os.environ.get(f"{prefix}_URL", "http://localhost:8088")
        username = os.environ.get(f"{prefix}_API_USERNAME")
        password = os.environ.get(f"{prefix}_API_PASSWORD")
        if not username or not password:
            raise SupersetError(
                f"{prefix}_API_USERNAME and {prefix}_API_PASSWORD must be set. "
                "Credentials are supplied by the environment, never by a file in Git."
            )
        return cls(
            base_url=base_url.rstrip("/"),
            username=username,
            password=password,
            verify_tls=os.environ.get(f"{prefix}_VERIFY_TLS", "true").lower() != "false",
        )

    # -- internals ----------------------------------------------------------
    def _url(self, path: str) -> str:
        return f"{self.base_url}/{path.lstrip('/')}"

    def _headers(self, mutating: bool = False) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        if self._access_token:
            headers["Authorization"] = f"Bearer {self._access_token}"
        if mutating and self._csrf_token:
            headers["X-CSRFToken"] = self._csrf_token
            headers["Referer"] = self.base_url
        return headers

    def login(self) -> "SupersetClient":
        """Obtain a bearer token and a CSRF token. Idempotent."""
        resp = self.session.post(
            self._url("/api/v1/security/login"),
            json={
                "username": self.username,
                "password": self.password,
                "provider": self.provider,
                "refresh": True,
            },
            timeout=self.timeout,
            verify=self.verify_tls,
        )
        if resp.status_code != 200:
            raise SupersetError("Login failed", resp)
        self._access_token = resp.json()["access_token"]

        csrf = self.session.get(
            self._url("/api/v1/security/csrf_token/"),
            headers=self._headers(),
            timeout=self.timeout,
            verify=self.verify_tls,
        )
        if csrf.status_code == 200:
            self._csrf_token = csrf.json().get("result")
        else:  # CSRF may be disabled; mutating calls will fail loudly if needed
            logger.warning("Could not fetch CSRF token: HTTP %s", csrf.status_code)
        return self

    def _request(
        self, method: str, path: str, *, mutating: bool = False, **kwargs: Any
    ) -> requests.Response:
        kwargs.setdefault("timeout", self.timeout)
        kwargs.setdefault("verify", self.verify_tls)
        headers = self._headers(mutating=mutating)
        headers.update(kwargs.pop("headers", {}) or {})
        return self.session.request(method, self._url(path), headers=headers, **kwargs)

    def _json(self, method: str, path: str, *, mutating: bool = False, **kwargs: Any) -> Any:
        resp = self._request(method, path, mutating=mutating, **kwargs)
        if resp.status_code >= 400:
            raise SupersetError(f"{method} {path} failed", resp)
        return resp.json() if resp.content else None

    # -- health / metadata --------------------------------------------------
    def health(self) -> bool:
        try:
            return self.session.get(
                self._url("/health"), timeout=10, verify=self.verify_tls
            ).status_code == 200
        except requests.RequestException:
            return False

    def version(self) -> str:
        """Superset version string, from the unauthenticated /health-adjacent API."""
        data = self._json("GET", "/api/v1/me/")
        # /api/v1/me/ does not carry a version; fall back to the menu endpoint.
        _ = data
        try:
            info = self._json("GET", "/api/v1/security/roles/?q=(page_size:1)")
            _ = info
        except SupersetError:
            pass
        return os.environ.get("SUPERSET_VERSION", "unknown")

    def me(self) -> dict[str, Any]:
        return self._json("GET", "/api/v1/me/")["result"]

    # -- generic asset listing ---------------------------------------------
    #: REST collection names keyed by the asset kind used across this repo.
    ASSET_ENDPOINTS: dict[str, str] = field(
        default_factory=lambda: {
            "database": "database",
            "dataset": "dataset",
            "chart": "chart",
            "dashboard": "dashboard",
            "saved_query": "saved_query",
            "assets": "assets",
        },
        repr=False,
    )

    def list_assets(
        self, kind: str, columns: Iterable[str] | None = None, page_size: int = 100
    ) -> list[dict[str, Any]]:
        """Return every object of `kind`, following pagination."""
        endpoint = self.ASSET_ENDPOINTS.get(kind, kind)
        results: list[dict[str, Any]] = []
        page = 0
        while True:
            query = f"(page:{page},page_size:{page_size}"
            if columns:
                cols = ",".join(f"{c}" for c in columns)
                query += f",columns:!({cols})"
            query += ")"
            payload = self._json("GET", f"/api/v1/{endpoint}/", params={"q": query})
            batch = payload.get("result", [])
            results.extend(batch)
            if len(results) >= payload.get("count", len(results)) or not batch:
                break
            page += 1
        return results

    def get_asset(self, kind: str, pk: int) -> dict[str, Any]:
        endpoint = self.ASSET_ENDPOINTS.get(kind, kind)
        return self._json("GET", f"/api/v1/{endpoint}/{pk}")["result"]

    # -- export -------------------------------------------------------------
    def export_assets(self, kind: str, ids: list[int]) -> bytes:
        """Export objects as a ZIP bundle. Returns raw ZIP bytes.

        Superset's export endpoint takes a Rison list of primary keys and
        returns the full dependency closure (dashboard -> charts -> datasets
        -> database), each identified by a stable UUID.
        """
        if not ids:
            raise SupersetError(f"Refusing to export zero {kind}s")
        endpoint = self.ASSET_ENDPOINTS.get(kind, kind)
        rison = "!(" + ",".join(str(i) for i in ids) + ")"
        resp = self._request("GET", f"/api/v1/{endpoint}/export/", params={"q": rison})
        if resp.status_code != 200:
            raise SupersetError(f"Export of {kind} {ids} failed", resp)
        if not resp.content.startswith(b"PK"):
            raise SupersetError(f"Export of {kind} did not return a ZIP archive", resp)
        return resp.content

    @staticmethod
    def unpack_zip(data: bytes) -> dict[str, bytes]:
        """Flatten an export ZIP into {relative_path: content}.

        Superset wraps everything in a timestamped top-level directory
        (e.g. `dashboard_export_20260908T101500/`). That prefix is stripped so
        the result is stable across exports.
        """
        out: dict[str, bytes] = {}
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            for info in zf.infolist():
                if info.is_dir():
                    continue
                parts = info.filename.split("/")
                relative = "/".join(parts[1:]) if len(parts) > 1 else parts[0]
                if relative:
                    out[relative] = zf.read(info)
        return out

    # -- import -------------------------------------------------------------
    def import_assets(
        self,
        kind: str,
        files: dict[str, bytes],
        *,
        passwords: dict[str, str] | None = None,
        overwrite: bool = True,
        ssh_tunnel_passwords: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Import a bundle. Idempotent when `overwrite` is true.

        Identity is by UUID: re-importing the same bundle updates the existing
        objects instead of creating duplicates.

        Use kind="assets" for GitOps deployments. The per-model endpoints are
        NOT interchangeable: `/api/v1/dashboard/import/` calls
        `import_chart(config, overwrite=False)` and
        `import_dataset(config, overwrite=False)`
        (superset/commands/dashboard/importers/v1/__init__.py:128,147), so it
        only ever creates missing charts and datasets and silently leaves
        existing ones untouched - a redeploy would not correct drift.
        `/api/v1/assets/import/` (ImportAssetsCommand, "will overwrite
        everything") passes overwrite=True to every model and is the endpoint
        that makes Git authoritative.

        `passwords` maps the database file path inside the bundle to the real
        credential, so version-controlled YAML never carries a secret.
        """
        endpoint = self.ASSET_ENDPOINTS.get(kind, kind)
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
            root = f"{kind}_import"
            for path, content in sorted(files.items()):
                zf.writestr(f"{root}/{path}", content)
        buffer.seek(0)

        # The per-model endpoints take the upload as `formData`;
        # /api/v1/assets/import/ takes it as `bundle` and returns HTTP 400
        # "Arguments are not correct" for anything else.
        upload_field = "bundle" if endpoint == "assets" else "formData"

        data: dict[str, str] = {"overwrite": "true" if overwrite else "false"}
        if passwords:
            import json as _json
            data["passwords"] = _json.dumps(passwords)
        if ssh_tunnel_passwords:
            import json as _json
            data["ssh_tunnel_passwords"] = _json.dumps(ssh_tunnel_passwords)

        resp = self._request(
            "POST",
            f"/api/v1/{endpoint}/import/",
            mutating=True,
            files={upload_field: ("bundle.zip", buffer.getvalue(), "application/zip")},
            data=data,
        )
        if resp.status_code >= 400:
            raise SupersetError(f"Import of {kind} bundle failed", resp)
        return resp.json() if resp.content else {}

    # -- SQL execution ------------------------------------------------------
    def run_sql(self, database_id: int, sql: str, schema: str | None = None,
                limit: int = 1000) -> dict[str, Any]:
        """Execute SQL synchronously through SQL Lab."""
        payload = {
            "database_id": database_id,
            "sql": sql,
            "schema": schema,
            "runAsync": False,
            "select_as_cta": False,
            "queryLimit": limit,
            "expand_data": True,
        }
        resp = self._request("POST", "/api/v1/sqllab/execute/", mutating=True, json=payload)
        if resp.status_code >= 400:
            raise SupersetError("SQL execution failed", resp)
        return resp.json()

    # -- chart data ---------------------------------------------------------
    def chart_data(self, chart_id: int) -> dict[str, Any]:
        """Execute a chart's *saved* query context.

        Only works for charts saved from Explore, which persist a
        `query_context`. That blob is full of numeric IDs and is deliberately
        stripped before assets are committed, so `execute_chart_query` is the
        validation path used by the smoke tests.
        """
        resp = self._request("GET", f"/api/v1/chart/{chart_id}/data/")
        if resp.status_code >= 400:
            raise SupersetError(f"Chart {chart_id} data query failed", resp)
        return resp.json()

    def execute_chart_query(self, chart: dict[str, Any]) -> dict[str, Any]:
        """Run a chart's query by rebuilding its query context from `params`.

        This is what actually proves a chart works: it compiles the chart's
        metrics and dimensions into SQL and runs them against the warehouse.
        It deliberately does not depend on a saved `query_context`, because
        that field is excluded from version control as non-deterministic.
        """
        params = chart.get("params")
        if isinstance(params, str):
            import json as _json
            params = _json.loads(params or "{}")
        params = params or {}

        datasource = params.get("datasource", "")
        try:
            ds_id = int(str(datasource).split("__")[0])
        except (ValueError, IndexError) as exc:
            raise SupersetError(
                f"Chart {chart.get('slice_name')!r} has no resolvable datasource"
            ) from exc

        # Metrics may be given as `metrics` (list) or `metric` (single).
        metrics = params.get("metrics") or ([params["metric"]] if params.get("metric") else [])
        # Dimensions come from groupby and, for cartesian charts, the x axis.
        columns = list(params.get("groupby") or [])
        x_axis = params.get("x_axis")
        if x_axis and x_axis not in columns and params.get("viz_type") != "big_number_total":
            columns.insert(0, x_axis)

        query: dict[str, Any] = {
            "metrics": metrics,
            "columns": columns,
            "filters": [],
            "row_limit": params.get("row_limit", 1000),
            "orderby": [],
            "extras": {"having": "", "where": ""},
        }
        payload = {
            "datasource": {"id": ds_id, "type": "table"},
            "force": False,
            "queries": [query],
            "result_format": "json",
            "result_type": "results",
        }
        resp = self._request("POST", "/api/v1/chart/data", mutating=True, json=payload)
        if resp.status_code >= 400:
            raise SupersetError(
                f"Chart {chart.get('slice_name')!r} query execution failed", resp
            )
        return resp.json()

    # -- lookup helpers -----------------------------------------------------
    def find_by_uuid(self, kind: str, uuid: str) -> dict[str, Any] | None:
        for item in self.list_assets(kind):
            if item.get("uuid") == uuid:
                return item
        return None

    def find_dashboard_by_title(self, title: str) -> dict[str, Any] | None:
        for item in self.list_assets("dashboard"):
            if item.get("dashboard_title") == title:
                return item
        return None

    def find_chart_by_name(self, name: str) -> dict[str, Any] | None:
        for item in self.list_assets("chart"):
            if item.get("slice_name") == name:
                return item
        return None
