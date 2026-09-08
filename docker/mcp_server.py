#!/usr/bin/env python
# Licensed under the Apache License, Version 2.0
"""Entrypoint for the Superset MCP service with enforceable read-only mode.

WHY THIS FILE EXISTS
--------------------
This is NOT a custom MCP server. Every tool, middleware and auth provider comes
from `superset.mcp_service`. The file exists only to close a wiring gap in
Superset 6.1.0:

  * `superset mcp run` calls `run_server(host, port, debug)` and that signature
    defaults `use_factory_config=False` (superset/mcp_service/server.py:572).
  * With that default the server builds itself through `init_fastmcp_server(...)`
    passing only `auth` and `middleware`, so `include_tags` / `exclude_tags` are
    never supplied and `MCP_FACTORY_CONFIG` is never consulted.
  * Even with `use_factory_config=True`, `get_mcp_factory_config()` returns the
    *module-level* `MCP_FACTORY_CONFIG` from superset/mcp_service/mcp_config.py
    - not the value an operator sets in `superset_config.py` - and it would also
    drop the Flask-derived auth provider and the response middleware.
  * `init_fastmcp_server(exclude_tags=...)` is itself a dead end on this stack:
    it assigns `mcp.exclude_tags`, but FastMCP 3.x REMOVED that constructor
    option. fastmcp 3.4.7 lists it under migration hints as
    "exclude_tags: Use `server.disable(tags=...)` after creating the server."
    The assignment therefore silently does nothing.

All three paths were tested against the running service before this file was
written. With `MCP_FACTORY_CONFIG["exclude_tags"] = {"mutate"}` set in
superset_config.py AND passed to `init_fastmcp_server`, `tools/list` still
advertised all 24 tools and `update_chart` still executed.

What actually works is FastMCP's visibility transform: `disable(tags={"mutate"})`.
Superset tags every mutating MCP tool `mutate` and every read tool `core`, so
disabling that tag removes the write surface from the served registry - the
tools are neither advertised nor invocable.

Read-only is the default. Writes require MCP_READ_ONLY=false, and in production
additionally MCP_ALLOW_PROD_WRITES=true (enforced in config/superset_config.py).

Superset RBAC remains the second, independent layer: MCP_RBAC_ENABLED=True
means every tool still executes as the authenticated user under their role and
row-level security.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("mcp-entrypoint")

#: Superset's tag for mutating MCP tools. Verified against
#: superset/mcp_service/*/tool/*.py in 6.1.0 (e.g. update_chart -> tags=["mutate"]).
MUTATE_TAG = "mutate"


def _bool_env(key: str, default: bool) -> bool:
    raw = os.environ.get(key)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default=os.environ.get("MCP_SERVICE_HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("MCP_SERVICE_PORT", 5008)))
    parser.add_argument("--debug", action="store_true", default=_bool_env("MCP_DEBUG", False))
    args = parser.parse_args()

    read_only = _bool_env("MCP_READ_ONLY", True)

    # Imports are deliberately late: superset.mcp_service.app registers every
    # tool at import time, and Superset's own server module warns that tools
    # must not be imported before this point.
    from superset.mcp_service.app import init_fastmcp_server
    from superset.mcp_service.caching import create_response_caching_middleware
    from superset.mcp_service.flask_singleton import get_flask_app
    from superset.mcp_service.mcp_config import MCP_TOOL_SEARCH_CONFIG
    from superset.mcp_service.server import (
        _apply_tool_search_transform,
        _create_auth_provider,
        _suppress_third_party_warnings,
        build_middleware_list,
        configure_logging,
        create_event_store,
        create_response_size_guard_middleware,
    )

    configure_logging(args.debug)
    _suppress_third_party_warnings()

    flask_app = get_flask_app()
    auth_provider = _create_auth_provider(flask_app)

    middleware_list = build_middleware_list()
    size_guard_middleware = create_response_size_guard_middleware()
    if size_guard_middleware:
        middleware_list.append(size_guard_middleware)
    if caching_middleware := create_response_caching_middleware():
        middleware_list.append(caching_middleware)

    mcp_instance = init_fastmcp_server(
        auth=auth_provider,
        middleware=middleware_list or None,
    )

    if read_only:
        # The enforcement point. Must run BEFORE the tool-search transform so
        # that any proxy built on top sees the already-filtered registry.
        mcp_instance.disable(tags={MUTATE_TAG})
        logger.info(
            "MCP READ-ONLY: disabled all tools tagged %r. Write tools are "
            "neither advertised nor invocable.", MUTATE_TAG,
        )
    else:
        logger.warning(
            "MCP WRITE ACCESS ENABLED (MCP_READ_ONLY=false). Mutating tools are "
            "exposed. Every call is audited by the MCP middleware and remains "
            "subject to Superset RBAC.",
        )

    tool_search_config = flask_app.config.get(
        "MCP_TOOL_SEARCH_CONFIG", MCP_TOOL_SEARCH_CONFIG
    )
    if tool_search_config.get("enabled", False):
        _apply_tool_search_transform(mcp_instance, tool_search_config)
        if size_guard_middleware:
            size_guard_middleware.excluded_tools.add(
                tool_search_config.get("search_tool_name", "search_tools")
            )

    event_store = create_event_store(None)
    logger.info(
        "Starting Superset MCP on %s:%s (read_only=%s, auth=%s)",
        args.host, args.port, read_only, bool(auth_provider),
    )

    if event_store is not None:
        import uvicorn

        app = mcp_instance.http_app(
            transport="streamable-http", event_store=event_store, stateless_http=True
        )
        uvicorn.run(app, host=args.host, port=args.port)
    else:
        mcp_instance.run(transport="streamable-http", host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    sys.exit(main())
