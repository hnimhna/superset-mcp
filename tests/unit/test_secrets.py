# Licensed under the Apache License, Version 2.0
"""Secret detection.

The single most damaging failure this repository could have is committing a
production credential, so the scanner is tested against realistic values as
well as against the indirection forms that must NOT trip it.
"""

from __future__ import annotations

import pytest

from scripts.validate import SECRET_PATTERNS, check_secrets

pytestmark = pytest.mark.unit


def _scan(text: str) -> list[str]:
    """Run every pattern over a snippet and return the rule names that matched."""
    return [name for name, pattern in SECRET_PATTERNS if pattern.search(text)]


class TestDetectsRealCredentials:
    @pytest.mark.parametrize("snippet,expected_rule", [
        ("sqlalchemy_uri: postgresql+psycopg2://user:hunter2pass@host:5432/dwh",
         "sqlalchemy-uri-with-password"),
        ("key: AKIAIOSFODNN7EXAMPLE", "aws-access-key"),
        ("-----BEGIN RSA PRIVATE KEY-----", "private-key-block"),
        ("token: ghp_" + "a" * 36, "github-token"),
        ("slack: xoxb-123456789012-abcdefghijkl", "slack-token"),
        ("api_key: AIza" + "b" * 35, "google-api-key"),
        ("Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9", "bearer-token"),
        ("password: SuperSecret123", "password-assignment"),
    ])
    def test_flags_credential(self, snippet, expected_rule):
        assert expected_rule in _scan(snippet)


class TestIgnoresIndirection:
    @pytest.mark.parametrize("snippet", [
        "sqlalchemy_uri: ${WAREHOUSE_URI}",
        "password: env:SUPERSET_API_PASSWORD",
        "password: ${DB_PASSWORD}",
        "password: null",
        "secret: ''",
        # Superset's own export mask - env-specific but not a usable credential.
        "sqlalchemy_uri: postgresql+psycopg2://user:XXXXXXXXXX@host:5432/dwh",
    ])
    def test_does_not_flag(self, snippet):
        """Indirection is the prescribed pattern; flagging it would train people
        to ignore the scanner."""
        assert _scan(snippet) == []


class TestRepositoryIsClean:
    def test_committed_assets_contain_no_secrets(self, assets_dir):
        files = [p for p in assets_dir.rglob("*") if p.is_file()]
        assert files, "no assets found to scan"
        assert check_secrets(files) == []

    def test_environment_configs_contain_no_secrets(self, repo_root):
        files = list((repo_root / "config").rglob("*.yaml"))
        assert files, "no environment configs found"
        assert check_secrets(files) == []

    def test_planted_credential_is_caught(self, tmp_path):
        """Proves the repo-clean assertions above are meaningful and not vacuous."""
        planted = tmp_path / "leak.yaml"
        planted.write_text(
            "sqlalchemy_uri: postgresql+psycopg2://warehouse:realpassword@db:5432/dwh\n"
        )
        import scripts.validate as validate

        original = validate.REPO_ROOT
        validate.REPO_ROOT = tmp_path
        try:
            assert check_secrets([planted]) != []
        finally:
            validate.REPO_ROOT = original
