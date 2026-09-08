# Licensed under the Apache License, Version 2.0
"""Environment configuration and secret indirection."""

from __future__ import annotations

import pytest

from scripts.lib.environment import (
    MissingSecretError,
    _resolve_tree,
    available_environments,
    load_environment,
)

pytestmark = pytest.mark.unit


class TestSecretIndirection:
    def test_resolves_env_reference(self, monkeypatch):
        monkeypatch.setenv("SOME_SECRET", "resolved-value")
        assert _resolve_tree({"k": "env:SOME_SECRET"}, where="t") == {"k": "resolved-value"}

    def test_missing_variable_fails_loudly(self, monkeypatch):
        """A silent default is how a dev credential reaches production."""
        monkeypatch.delenv("DEFINITELY_NOT_SET", raising=False)
        with pytest.raises(MissingSecretError, match="DEFINITELY_NOT_SET"):
            _resolve_tree({"k": "env:DEFINITELY_NOT_SET"}, where="t")

    def test_empty_variable_is_treated_as_missing(self, monkeypatch):
        monkeypatch.setenv("EMPTY_SECRET", "")
        with pytest.raises(MissingSecretError):
            _resolve_tree({"k": "env:EMPTY_SECRET"}, where="t")

    def test_plain_values_pass_through(self):
        assert _resolve_tree({"a": "plain", "b": 5, "c": True}, where="t") == \
               {"a": "plain", "b": 5, "c": True}

    def test_resolves_nested_structures(self, monkeypatch):
        monkeypatch.setenv("NESTED", "v")
        result = _resolve_tree({"a": {"b": ["env:NESTED"]}}, where="t")
        assert result == {"a": {"b": ["v"]}}


class TestEnvironmentFiles:
    def test_all_three_environments_exist(self):
        assert set(available_environments()) >= {"dev", "staging", "prod"}

    @pytest.mark.parametrize("name", ["dev", "staging", "prod"])
    def test_parses_without_secrets_present(self, name):
        """CI must be able to lint every environment with no credentials set."""
        env = load_environment(name, resolve=False)
        assert env.raw.get("name") == name

    @pytest.mark.parametrize("name", ["dev", "staging", "prod"])
    def test_declares_required_placeholders(self, name):
        placeholders = load_environment(name, resolve=False).raw.get("placeholders", {})
        assert "ENV" in placeholders
        assert "WAREHOUSE_URI" in placeholders

    @pytest.mark.parametrize("name", ["dev", "staging", "prod"])
    def test_contains_no_literal_credentials(self, name):
        """Every sensitive value must be an `env:` reference."""
        raw = load_environment(name, resolve=False).raw
        for key in ("username", "password"):
            value = (raw.get("auth") or {}).get(key, "")
            assert str(value).startswith("env:"), f"{name}.auth.{key} is not indirected"
        assert str(raw["placeholders"]["WAREHOUSE_URI"]).startswith("env:")

    def test_production_defaults_to_read_only_mcp(self):
        """Production must never expose mutating MCP tools by default."""
        assert load_environment("prod", resolve=False).raw["mcp"]["read_only"] is True

    def test_production_requires_clean_drift_before_promotion(self):
        deployment = load_environment("prod", resolve=False).raw["deployment"]
        assert deployment["require_clean_drift"] is True
        assert deployment["rollback_on_failure"] is True

    def test_unknown_environment_raises(self):
        with pytest.raises(FileNotFoundError):
            load_environment("nonexistent")
