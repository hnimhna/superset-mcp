# Licensed under the Apache License, Version 2.0
"""MCP service tests, with emphasis on the read-only governance guarantee.

The read-only guarantee is the security property this platform makes, so it is
tested by observation rather than by trusting configuration: the tool registry
is enumerated and a mutating tool is actually invoked.

Superset 6.1 tags mutating MCP tools "mutate" and read tools "core"/"discovery".
docker/mcp_server.py turns MCP_READ_ONLY into `disable(tags={"mutate"})`, which
removes them from the served registry entirely.
"""

from __future__ import annotations

import os

import pytest

from scripts.lib.mcp_client import McpError

pytestmark = [pytest.mark.api, pytest.mark.integration]

#: Every mutating tool Superset 6.1 exposes, verified against the live registry
#: with MCP_READ_ONLY=false.
MUTATING_TOOLS = frozenset({
    "update_chart",
    "update_chart_preview",
    "generate_chart",
    "generate_dashboard",
    "add_chart_to_existing_dashboard",
    "create_virtual_dataset",
    "execute_sql",
    "save_sql_query",
})

#: Read-only tools that must remain available so agents can still inspect.
EXPECTED_READ_TOOLS = frozenset({
    "list_charts", "list_dashboards", "list_datasets", "list_databases",
    "get_chart_info", "get_dashboard_info", "get_dataset_info",
    "get_instance_info", "health_check",
})


def _read_only_expected() -> bool:
    return os.environ.get("MCP_READ_ONLY", "true").lower() not in {"false", "0", "no"}


class TestConnectivity:
    def test_initializes(self, mcp_client):
        info = mcp_client.initialize()
        assert info["serverInfo"]["name"]

    def test_advertises_tools(self, mcp_client):
        assert mcp_client.list_tools()

    def test_health_check_tool_runs(self, mcp_client):
        result = mcp_client.call_tool("health_check", {})
        assert not result.get("isError")


class TestReadCapabilities:
    def test_read_tools_are_available(self, mcp_client):
        names = {tool["name"] for tool in mcp_client.list_tools()}
        missing = EXPECTED_READ_TOOLS - names
        assert not missing, f"read tools unexpectedly absent: {sorted(missing)}"

    def test_can_list_dashboards(self, mcp_client):
        result = mcp_client.call_tool("list_dashboards", {"request": {}})
        assert not result.get("isError")

    def test_can_inspect_datasets(self, mcp_client):
        result = mcp_client.call_tool("list_datasets", {"request": {}})
        assert not result.get("isError")


@pytest.mark.skipif(not _read_only_expected(),
                    reason="MCP_READ_ONLY is disabled for this run")
class TestReadOnlyEnforcement:
    def test_no_mutating_tool_is_advertised(self, mcp_client):
        """Not merely refused when called - never offered in the first place."""
        names = {tool["name"] for tool in mcp_client.list_tools()}
        exposed = sorted(names & MUTATING_TOOLS)
        assert not exposed, f"mutating tools exposed in read-only mode: {exposed}"

    @pytest.mark.parametrize("tool", sorted(MUTATING_TOOLS))
    def test_mutating_tool_cannot_be_invoked(self, mcp_client, tool):
        """Enforcement must hold at call time, not only in the advertised list."""
        try:
            result = mcp_client.call_tool(tool, {"request": {"identifier": 1}})
        except McpError:
            return  # rejected at the protocol layer - acceptable
        text = "".join(
            block.get("text", "") for block in result.get("content", [])
        ).lower()
        assert result.get("isError") or "unknown tool" in text or "error" in text, (
            f"{tool} appears to have executed in read-only mode"
        )

    def test_tool_search_proxy_is_not_exposed(self, mcp_client):
        """Superset's tool-search proxy resolves tools from the underlying
        registry and would bypass tag filtering, so it is disabled here."""
        names = {tool["name"] for tool in mcp_client.list_tools()}
        assert "call_tool" not in names
        assert "search_tools" not in names


class TestGovernance:
    def test_production_environment_defaults_to_read_only(self):
        """Config-level guarantee, independent of any running server."""
        from scripts.lib.environment import load_environment

        assert load_environment("prod", resolve=False).raw["mcp"]["read_only"] is True

    def test_staging_environment_defaults_to_read_only(self):
        from scripts.lib.environment import load_environment

        assert load_environment("staging", resolve=False).raw["mcp"]["read_only"] is True
