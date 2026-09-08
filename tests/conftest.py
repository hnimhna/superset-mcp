# Licensed under the Apache License, Version 2.0
"""Shared pytest fixtures.

Unit tests never touch Superset. Integration and API tests need a live
instance; they skip cleanly (rather than fail) when one is not reachable, so
`pytest tests/unit` works on any machine with no services running.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


def _load_dotenv() -> None:
    """Load .env for local runs. In CI the variables come from the secret store."""
    env_file = REPO_ROOT / ".env"
    if not env_file.is_file():
        return
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


_load_dotenv()


@pytest.fixture(scope="session")
def repo_root() -> Path:
    return REPO_ROOT


@pytest.fixture(scope="session")
def assets_dir(repo_root: Path) -> Path:
    return repo_root / "assets"


@pytest.fixture(scope="session")
def superset_client():
    """Authenticated client, or skip when no Superset is reachable."""
    from scripts.lib.superset_client import SupersetClient, SupersetError

    try:
        client = SupersetClient.from_env()
    except SupersetError as exc:
        pytest.skip(f"Superset credentials not configured: {exc}")
    if not client.health():
        pytest.skip(f"No Superset reachable at {client.base_url}")
    try:
        return client.login()
    except SupersetError as exc:
        pytest.skip(f"Superset login failed: {exc}")


@pytest.fixture(scope="session")
def mcp_client():
    """MCP client, or skip when the MCP service is not reachable."""
    from scripts.lib.mcp_client import McpClient, McpError

    client = McpClient()
    try:
        client.initialize()
    except Exception as exc:  # noqa: BLE001 - any transport failure means skip
        pytest.skip(f"No MCP service reachable at {client.url}: {exc}")
    _ = McpError
    return client
