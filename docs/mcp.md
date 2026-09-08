# MCP integration

## What this is

Apache Superset 6.1 ships an **official MCP service** (`superset/mcp_service`,
built on FastMCP). This platform uses that service — it does **not** implement a
custom MCP server. Every tool, middleware and auth provider comes from Superset.

The only local addition is `docker/mcp_server.py`, an entrypoint that closes a
wiring gap in the shipped CLI. See [Read-only enforcement](#read-only-enforcement).

## Architecture

```
Claude Code ──HTTP (streamable)──► :5008/mcp
                                      │
                          ┌───────────┴───────────┐
                          │  FastMCP 3.4.7        │
                          │  middleware: auth,    │
                          │  audit, size guard    │
                          └───────────┬───────────┘
                                      │ g.user is set from the JWT subject
                                      ▼
                            Superset DAOs (RBAC + RLS applied)
                                      ▼
                              metadata database
```

The MCP service runs as a **separate process** from the web application, with
its own Flask app, but shares the same configuration and metadata database.

## Configuration

Everything is read from the environment via `config/superset_config.py`.

| Variable | Default | Purpose |
|---|---|---|
| `MCP_READ_ONLY` | `true` | when true, mutating tools are not registered |
| `MCP_AUTH_ENABLED` | `true` in prod, else `false` | JWT authentication |
| `MCP_RBAC_ENABLED` | `true` (always) | enforce the acting user's permissions |
| `MCP_DEV_USERNAME` | `admin` | fixed identity when auth is disabled (non-prod only) |
| `MCP_SERVICE_HOST` / `_PORT` | `0.0.0.0` / `5008` | bind address |
| `MCP_TOOL_SEARCH_ENABLED` | `false` | see [Tool search](#tool-search) |
| `MCP_ALLOW_PROD_WRITES` | unset | additionally required to write in production |

## Read-only enforcement

**This is the security property the platform makes, so it is worth being
precise about how it works and what does not work.**

Superset tags every MCP tool. Read tools carry `core` or `discovery`; mutating
tools carry `mutate`:

```python
# superset/mcp_service/chart/tool/update_chart.py
@tool(tags=["mutate"], class_permission_name="Chart",
      annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True))
async def update_chart(...)
```

Three plausible ways to act on that tag, **two of which silently do nothing**:

| Approach | Result |
|---|---|
| `MCP_FACTORY_CONFIG["exclude_tags"]` in `superset_config.py` | **No effect.** `superset mcp run` calls `run_server()` with `use_factory_config=False`, so the factory config is never read. And `get_mcp_factory_config()` returns the *module-level* dict from `mcp_config.py`, not the operator's override. |
| `init_fastmcp_server(exclude_tags={"mutate"})` | **No effect.** It assigns `mcp.exclude_tags`, but FastMCP 3.x removed that option: the library lists it under migration hints as *"exclude_tags: Use `server.disable(tags=...)` after creating the server."* The assignment is a silent no-op. |
| `mcp_instance.disable(tags={"mutate"})` | **Works.** |

All three were tested against the running service. With the first two in place,
`tools/list` still advertised all 24 tools and `update_chart` still executed.

`docker/mcp_server.py` reproduces Superset's own default startup path — same
auth provider, same middleware, same caching and size-guard configuration — and
adds the one call that actually enforces the policy.

### Verified behaviour

| `MCP_READ_ONLY` | Tools advertised | `update_chart` |
|---|---|---|
| `true` (default) | **16** | `NotFoundError: Unknown tool: 'update_chart'` |
| `false` | **24** | executes |

The eight tools removed in read-only mode: `update_chart`,
`update_chart_preview`, `generate_chart`, `generate_dashboard`,
`add_chart_to_existing_dashboard`, `create_virtual_dataset`, `execute_sql`,
`save_sql_query`.

This is *removal*, not refusal — the tools are never advertised and cannot be
invoked even by name. Superset RBAC remains a second, independent layer:
`MCP_RBAC_ENABLED=True` means every tool executes as the authenticated user
under their role and row-level security.

### Tool search

Superset 6.1 enables `MCP_TOOL_SEARCH_CONFIG` by default: `tools/list` returns
only `search_tools`, `call_tool` and two pinned tools, and the real registry is
reached through that proxy. **The proxy resolves tools from the underlying
registry and bypasses tag filtering**, so it defeats read-only mode.

A governance platform needs the advertised surface to *be* the permitted
surface, so tool search is disabled here (`MCP_TOOL_SEARCH_ENABLED=false`) and
the flat 16-tool list is served. `tests/api/test_mcp.py` asserts that neither
`call_tool` nor `search_tools` is exposed.

## Authentication

**Development** (`MCP_AUTH_ENABLED=false`, non-production only) uses a fixed
identity from `MCP_DEV_USERNAME`. `config/superset_config.py` raises at startup
if this is attempted with `SUPERSET_ENV=prod`.

**Production** (`MCP_AUTH_ENABLED=true`) validates a JWT:

```bash
MCP_AUTH_ENABLED=true
MCP_JWT_ALGORITHM=RS256
MCP_JWKS_URI=https://idp.example.com/.well-known/jwks.json
MCP_JWT_ISSUER=https://idp.example.com/
MCP_JWT_AUDIENCE=superset-prod
MCP_REQUIRED_SCOPES=superset:read
```

HS256 with `MCP_JWT_SECRET` is supported for simpler deployments. The `sub`
claim resolves to a Superset user, and that user's RBAC applies.

> **NOT VERIFIED** — JWT authentication is implemented and configured but has
> only been exercised in `MCP_AUTH_ENABLED=false` dev mode. No identity provider
> was available in this environment.

## Connecting Claude Code

`.mcp.json` is committed:

```json
{ "mcpServers": { "superset": { "type": "http", "url": "http://localhost:5008/mcp" } } }
```

```bash
claude mcp list
# superset: http://localhost:5008/mcp (HTTP) - ✔ Connected
```

Note the key is `"type": "http"`. Superset's published documentation shows
`"type": "url"`, which Claude Code does not accept — generate the file with
`claude mcp add --scope project --transport http superset <url>` rather than
hand-writing it.

Project-scoped servers need a one-time approval the first time `claude` starts
in the directory.

Raw protocol check:

```bash
curl -s -X POST http://localhost:5008/mcp \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","method":"initialize","id":1,
       "params":{"protocolVersion":"2025-06-18","capabilities":{},
                 "clientInfo":{"name":"probe","version":"1.0"}}}'
```

The service runs **stateful** streamable-HTTP: capture `Mcp-Session-Id` from the
initialize response and send it on every subsequent request, or you get
`Bad Request: Missing session ID`. `scripts/lib/mcp_client.py` handles this.

## The safety model

```
Claude → inspect → propose → modify DEV → export → git diff → tests
       → PR → human approval → STAGING → validation → PROD
```

MCP is an operational interface. It does not bypass GitOps governance:

- Production is read-only by default, and enabling writes requires **two**
  independent settings (`MCP_READ_ONLY=false` *and* `MCP_ALLOW_PROD_WRITES=true`);
  the config raises at startup otherwise.
- A change made through MCP is not deployed until it is exported to Git,
  reviewed and promoted. Until then `scripts/diff.py` reports it as drift.
- Destructive operations require explicit authorization; the tools carry
  `destructiveHint` annotations that clients surface to the user.

## Observability

The MCP middleware logs every tool call in a structured form:

```
MCP tool call: tool=update_chart, agent_id=None, user_id=None,
  method=tools/call, dashboard_id=None, slice_id=None, dataset_id=None,
  duration_ms=0, success=False
MCP tool error: tool=update_chart, error_type=NotFoundError,
  error=Unknown tool: 'update_chart'
```

That covers actor, operation, affected asset, result and duration.

> **Known upstream issue.** Superset's `DBEventLogger` raises
> `TypeError: DBEventLogger.log() missing 3 required positional arguments:
> 'dashboard_id', 'slice_id', and 'referrer'` when the MCP middleware tries to
> persist a tool-error event. The middleware catches it and continues, so the
> service is unaffected, but MCP audit events do **not** reach the metadata
> database. Auditing therefore relies on the structured stdout log above; ship
> those logs to your log aggregator. Set `SUPERSET_EVENT_LOGGER` to a custom
> class to change this.

## Available tools (read-only mode)

```
generate_explore_link   get_chart_data        get_chart_info
get_chart_preview       get_chart_type_schema get_dashboard_info
get_database_info       get_dataset_info      get_instance_info
get_schema              health_check          list_charts
list_dashboards         list_databases        list_datasets
open_sql_lab_with_context
```
