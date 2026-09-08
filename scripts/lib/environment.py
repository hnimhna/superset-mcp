# Licensed under the Apache License, Version 2.0
"""Environment configuration loading with strict secret indirection.

`config/environments/*.yaml` is committed, so it must never contain a secret.
Every sensitive value is written as `env:VARNAME` and resolved here from the
process environment. Resolution fails loudly rather than defaulting, because a
silent default is how a development credential reaches production.

The `env:` indirection is also the seam for a real secret manager: swap
`_resolve_reference` for a Vault / AWS Secrets Manager / GCP Secret Manager
lookup and nothing else in the codebase changes.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
ENVIRONMENTS_DIR = REPO_ROOT / "config" / "environments"

#: Prefixes understood in environment YAML values. Additional backends
#: (vault:, aws-sm:, gcp-sm:, azure-kv:) plug in here without touching callers.
_ENV_PREFIX = "env:"


class MissingSecretError(RuntimeError):
    """Raised when an `env:` reference has no value in the environment."""


def _resolve_reference(value: Any, *, where: str) -> Any:
    """Resolve a single `env:NAME` reference; pass other values through."""
    if not isinstance(value, str) or not value.startswith(_ENV_PREFIX):
        return value
    name = value[len(_ENV_PREFIX):]
    resolved = os.environ.get(name)
    if resolved is None or resolved == "":
        raise MissingSecretError(
            f"{where}: environment variable {name!r} is required but not set. "
            "Provide it from your secret store; it is deliberately not "
            "committed to the repository."
        )
    return resolved


def _resolve_tree(value: Any, *, where: str) -> Any:
    if isinstance(value, dict):
        return {k: _resolve_tree(v, where=f"{where}.{k}") for k, v in value.items()}
    if isinstance(value, list):
        return [_resolve_tree(v, where=f"{where}[{i}]") for i, v in enumerate(value)]
    return _resolve_reference(value, where=where)


@dataclass
class Environment:
    """A resolved deployment target."""

    name: str
    superset_url: str
    username: str
    password: str = field(repr=False)
    placeholders: dict[str, str] = field(default_factory=dict, repr=False)
    database_passwords: dict[str, str] = field(default_factory=dict, repr=False)
    mcp_url: str | None = None
    mcp_read_only: bool = True
    rollback_on_failure: bool = True
    require_clean_drift: bool = False
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @property
    def is_production(self) -> bool:
        return self.name.lower() == "prod"


def available_environments() -> list[str]:
    if not ENVIRONMENTS_DIR.is_dir():
        return []
    return sorted(p.stem for p in ENVIRONMENTS_DIR.glob("*.yaml"))


def load_environment(name: str, *, resolve: bool = True) -> Environment:
    """Load `config/environments/<name>.yaml`.

    With `resolve=False` the `env:` references are left intact, which lets
    validation and linting run in CI without any secret being present.
    """
    path = ENVIRONMENTS_DIR / f"{name}.yaml"
    if not path.is_file():
        raise FileNotFoundError(
            f"Unknown environment {name!r}. Available: {', '.join(available_environments())}"
        )
    raw = yaml.safe_load(path.read_text()) or {}
    data = _resolve_tree(raw, where=str(path)) if resolve else raw

    auth = data.get("auth") or {}
    mcp = data.get("mcp") or {}
    deployment = data.get("deployment") or {}
    return Environment(
        name=data.get("name", name),
        superset_url=str(data.get("superset_url", "")).rstrip("/"),
        username=auth.get("username", ""),
        password=auth.get("password", ""),
        placeholders={str(k): str(v) for k, v in (data.get("placeholders") or {}).items()},
        database_passwords={
            str(k): str(v) for k, v in (data.get("database_passwords") or {}).items()
        },
        mcp_url=mcp.get("url"),
        mcp_read_only=bool(mcp.get("read_only", True)),
        rollback_on_failure=bool(deployment.get("rollback_on_failure", True)),
        require_clean_drift=bool(deployment.get("require_clean_drift", False)),
        raw=raw,
    )


def client_for(env: Environment):
    """Build an authenticated Superset client for an environment."""
    from scripts.lib.superset_client import SupersetClient

    return SupersetClient(
        base_url=env.superset_url,
        username=env.username,
        password=env.password,
        verify_tls=os.environ.get("SUPERSET_VERIFY_TLS", "true").lower() != "false",
    ).login()
