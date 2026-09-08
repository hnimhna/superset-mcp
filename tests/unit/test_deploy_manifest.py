# Licensed under the Apache License, Version 2.0
"""Import manifest generation and bundle resolution.

The manifest is easy to get wrong in a way nothing else catches: the import
endpoint rejects a bundle whose metadata.yaml declares the wrong `type`, and
the per-model endpoints silently do the wrong thing rather than erroring.
"""

from __future__ import annotations

import io
import zipfile
from typing import Any

import pytest

from scripts.lib.deploy import (
    ASSETS_METADATA_TYPE,
    build_passwords,
    deploy,
    resolve_bundle,
)
from scripts.lib.environment import Environment
from scripts.lib.normalize import UnresolvedPlaceholderError, dump_yaml, load_yaml

pytestmark = pytest.mark.unit


class _FakeClient:
    """Records what would have been sent, without touching a real Superset."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def import_assets(self, kind: str, files: dict[str, bytes], *,
                      passwords: dict[str, str] | None = None,
                      overwrite: bool = True, **_: Any) -> dict[str, Any]:
        self.calls.append({
            "kind": kind, "files": dict(files),
            "passwords": passwords, "overwrite": overwrite,
        })
        return {"message": "OK"}


@pytest.fixture
def env() -> Environment:
    return Environment(
        name="dev",
        superset_url="http://localhost:8088",
        username="admin",
        password="secret",  # pragma: allowlist secret - test fixture, not a credential
        placeholders={"ENV": "DEV", "WAREHOUSE_URI": "postgresql://u:p@h:5432/dwh"},
        database_passwords={"databases/SALES_DWH.yaml": "warehouse-password"},
    )


@pytest.fixture
def bundle() -> dict[str, bytes]:
    return {
        "databases/SALES_DWH.yaml": dump_yaml({
            "database_name": "SALES_DWH_${ENV}",
            "sqlalchemy_uri": "${WAREHOUSE_URI}",
            "uuid": "db-uuid",
        }).encode(),
        "charts/SALES_X_TREND.yaml": dump_yaml({
            "slice_name": "SALES_X_TREND", "uuid": "chart-uuid",
        }).encode(),
    }


class TestManifest:
    def test_metadata_declares_the_assets_type(self, bundle, env):
        """ImportAssetsCommand validates metadata["type"] and rejects anything else."""
        client = _FakeClient()
        deploy(client, resolve_bundle(bundle, env), {})
        sent = client.calls[0]["files"]
        assert "metadata.yaml" in sent
        assert load_yaml(sent["metadata.yaml"])["type"] == ASSETS_METADATA_TYPE == "assets"

    def test_manifest_is_generated_not_taken_from_git(self, bundle, env):
        """A stale metadata.yaml in assets/ must not override the manifest."""
        with_stale = dict(bundle)
        with_stale["metadata.yaml"] = dump_yaml(
            {"version": "1.0.0", "type": "Dashboard"}
        ).encode()
        client = _FakeClient()
        deploy(client, resolve_bundle(with_stale, env), {})
        assert load_yaml(client.calls[0]["files"]["metadata.yaml"])["type"] == "assets"

    def test_targets_the_overwriting_endpoint(self, bundle, env):
        """The per-model endpoints do not overwrite existing charts or datasets."""
        client = _FakeClient()
        deploy(client, resolve_bundle(bundle, env), {})
        assert client.calls[0]["kind"] == "assets"
        assert client.calls[0]["overwrite"] is True

    def test_bundle_is_a_valid_zip_with_a_single_root(self, bundle, env):
        """Sanity-check the shape the import endpoint expects."""
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            for path, content in resolve_bundle(bundle, env).items():
                archive.writestr(f"assets_import/{path}", content)
        buffer.seek(0)
        with zipfile.ZipFile(buffer) as archive:
            roots = {name.split("/")[0] for name in archive.namelist()}
        assert roots == {"assets_import"}


class TestResolution:
    def test_placeholders_are_substituted(self, bundle, env):
        resolved = load_yaml(resolve_bundle(bundle, env)["databases/SALES_DWH.yaml"])
        assert resolved["database_name"] == "SALES_DWH_DEV"
        assert resolved["sqlalchemy_uri"] == "postgresql://u:p@h:5432/dwh"

    def test_no_placeholder_survives(self, bundle, env):
        for content in resolve_bundle(bundle, env).values():
            assert b"${" not in content

    def test_undefined_placeholder_blocks_the_deployment(self, env):
        """Deploying a literal "${...}" would create a visibly broken object."""
        broken = {"databases/X.yaml": dump_yaml({"sqlalchemy_uri": "${NOPE}"}).encode()}
        with pytest.raises(UnresolvedPlaceholderError, match="NOPE"):
            resolve_bundle(broken, env)


class TestPasswords:
    def test_credentials_are_passed_out_of_band(self, bundle, env):
        """So a password never has to appear inside a version-controlled asset."""
        passwords = build_passwords(bundle, env)
        assert passwords == {"databases/SALES_DWH.yaml": "warehouse-password"}

    def test_no_password_is_written_into_the_bundle(self, bundle, env):
        resolved = resolve_bundle(bundle, env)
        assert b"warehouse-password" not in resolved["databases/SALES_DWH.yaml"]

    def test_only_database_assets_get_passwords(self, bundle, env):
        assert all(p.startswith("databases/") for p in build_passwords(bundle, env))
