# Licensed under the Apache License, Version 2.0
"""Minimal JSON-RPC client for the Superset MCP service (streamable-http).

Used by the MCP verification script and the API test-suite. Deliberately tiny -
it speaks just enough of the protocol to initialize, enumerate tools and invoke
them, which is all the governance checks require.
"""

from __future__ import annotations

import json
import os
from typing import Any

import requests

PROTOCOL_VERSION = "2025-06-18"


class McpError(RuntimeError):
    pass


class McpClient:
    """Stateless streamable-http MCP client."""

    def __init__(self, url: str | None = None, token: str | None = None,
                 timeout: int = 60) -> None:
        self.url = (url or os.environ.get("MCP_URL", "http://localhost:5008/mcp")).rstrip("/")
        self.token = token or os.environ.get("MCP_TOKEN")
        self.timeout = timeout
        self._id = 0
        #: Set from the Mcp-Session-Id response header during initialize().
        #: Required in stateful streamable-http mode; harmless when stateless.
        self.session_id: str | None = None

    def _headers(self) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": PROTOCOL_VERSION,
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        return headers

    @staticmethod
    def _parse(body: str) -> dict[str, Any]:
        """Extract the JSON payload from an SSE or plain-JSON response."""
        for line in body.splitlines():
            line = line.strip()
            if line.startswith("data:"):
                line = line[5:].strip()
            if line.startswith("{"):
                return json.loads(line)
        raise McpError(f"No JSON-RPC payload in response: {body[:500]}")

    def _post(self, body: dict[str, Any]) -> requests.Response:
        return requests.post(
            self.url, headers=self._headers(), json=body, timeout=self.timeout
        )

    def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        """Send a JSON-RPC notification (no id, no response body expected)."""
        self._post({"jsonrpc": "2.0", "method": method, "params": params or {}})

    def call(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self._id += 1
        resp = self._post(
            {"jsonrpc": "2.0", "method": method, "id": self._id, "params": params or {}}
        )
        # The server assigns a session on initialize; reuse it for every
        # subsequent call or a stateful server rejects us with "Missing session ID".
        if not self.session_id:
            self.session_id = resp.headers.get("mcp-session-id") or resp.headers.get(
                "Mcp-Session-Id"
            )
        if resp.status_code >= 400:
            raise McpError(f"{method} -> HTTP {resp.status_code}: {resp.text[:500]}")
        payload = self._parse(resp.text)
        if "error" in payload:
            raise McpError(f"{method} -> {payload['error']}")
        return payload.get("result", {})

    # -- convenience --------------------------------------------------------
    def initialize(self) -> dict[str, Any]:
        result = self.call("initialize", {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "superset-gitops", "version": "1.0"},
        })
        # Completing the handshake is mandatory before tools/* in stateful mode.
        self.notify("notifications/initialized")
        return result

    def list_tools(self) -> list[dict[str, Any]]:
        return self.call("tools/list").get("tools", [])

    def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        return self.call("tools/call", {"name": name, "arguments": arguments or {}})

    def search_tools(self, query: str, max_results: int = 25) -> list[str]:
        """Enumerate tools through the tool-search proxy.

        Superset 6.1 enables MCP_TOOL_SEARCH_CONFIG by default, so tools/list
        returns only the pinned entries plus search_tools/call_tool. The real
        registry is reachable through search_tools.
        """
        result = self.call_tool("search_tools", {"query": query, "max_results": max_results})
        names: list[str] = []
        for block in result.get("content", []):
            text = block.get("text", "")
            for token in text.replace(",", " ").split():
                cleaned = token.strip("\"'`*-:()[]{}")
                if cleaned.isidentifier():
                    names.append(cleaned)
        struct = result.get("structuredContent") or {}
        for entry in struct.get("tools", []) or []:
            if isinstance(entry, dict) and entry.get("name"):
                names.append(entry["name"])
        return sorted(set(names))

    @staticmethod
    def is_error(result: dict[str, Any]) -> bool:
        return bool(result.get("isError"))
