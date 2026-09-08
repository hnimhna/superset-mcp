# Architecture

## Components

| Component | Image / source | Purpose |
|---|---|---|
| `superset` | `superset-gitops:6.1.0` (from `apache/superset:6.1.0-py311`) | the BI web application, port 8088 |
| `mcp` | same image, `docker/mcp_server.py` entrypoint | official Superset MCP service, port 5008 |
| `metadata-db` | `postgres:16-alpine` | Superset's own metadata — **runtime state** |
| `warehouse-db` | `postgres:16-alpine` | the analytical warehouse the BI assets read |
| `redis` | `redis:7-alpine` | query/result cache and MCP session store |
| `superset-init` | same image, one-shot | idempotent migrate + admin + role sync |

## Data flow

```
        ┌──────────────┐
        │ Claude Code  │
        └──────┬───────┘
               │ MCP (HTTP, streamable)
               ▼
    ┌──────────────────────┐        read-only by default:
    │ Superset MCP service │        write tools are not registered
    └──────────┬───────────┘
               │ in-process, shares config + DB with the web app
               ▼
    ┌──────────────────────┐        ┌────────────┐
    │    Superset API      │◄───────│ Superset UI│
    └──────────┬───────────┘        └────────────┘
               ▼
    ┌──────────────────────┐
    │  metadata database   │  runtime state — never edited directly
    └──────────┬───────────┘
               │  export ▲ │ import
               ▼         │ ▼
    ┌──────────────────────┐
    │   normalization      │  scripts/lib/normalize.py
    └──────────┬───────────┘
               ▼
    ┌──────────────────────┐
    │   Git repository     │  THE SOURCE OF TRUTH
    └──────────┬───────────┘
               ▼
     validate → unit → integration → API → E2E → drift
               ▼
        DEV → STAGING → PROD
```

## The central design decision

**The Superset metadata database is runtime state, not truth.**

Everything else follows:

- A change is only real once it is in Git. A change made only in Superset is
  *drift*, and `scripts/diff.py` reports it as such.
- Rollback is `git` + re-import, never a database restore. A database restore
  would also revert unrelated runtime state (query history, user preferences,
  permissions) and cannot be reviewed in a pull request.
- Identity is the **UUID**, never the primary key. The same dashboard is `id=1`
  in DEV and `id=57` in PROD; only the UUID is stable, and it is what both the
  exporter and the importer key on.

## Why normalization exists

Superset's raw export is unusable as a Git artifact:

| Raw export contains | Problem |
|---|---|
| `charts/NAME_2.yaml` | the `_2` is a primary key — different in every environment |
| `metadata.yaml: timestamp` | changes on every export |
| `sqlalchemy_uri: ...:XXXXXXXXXX@host` | environment-specific, and masked so it is useless anyway |
| `position: meta.chartId: 2` | a primary key embedded in the dashboard layout |
| `query_context: {...}` | a derived query cache, full of numeric datasource IDs |
| unordered `columns` / `metrics` | arbitrary ordering churns the diff |

Without normalization every export produces a large diff regardless of whether
anything changed, and reviewers stop reading diffs. `scripts/lib/normalize.py`
removes each of these, and each rule is justified against Superset's own
importer/exporter source — a rule that strips something the importer needs
produces a repository that cannot be deployed. `chartId` is the clearest case:
it looks like pure noise, but `build_uuid_to_id_map()` reads it
unconditionally, so it is *rewritten* to a UUID-derived value rather than
removed.

## Environment model

One asset tree serves every environment. Environment-specific values are
`${PLACEHOLDER}` references resolved at import time from
`config/environments/<env>.yaml`:

```yaml
# assets/databases/SALES_DWH.yaml  (committed)
database_name: SALES_DWH_${ENV}
sqlalchemy_uri: ${WAREHOUSE_URI}
uuid: 75c590eb-27e3-5327-b659-a6f31c462b14   # stable across environments
```

```yaml
# config/environments/prod.yaml  (committed — no secrets)
placeholders:
  ENV: PROD
  WAREHOUSE_URI: env:DATABASE_URI     # resolved from the process environment
```

An unresolved placeholder is a hard failure, never a silent pass-through: a
literal `${WAREHOUSE_URI}` deployed as a connection string would create a
broken database entry that fails later and confusingly.

## Security model

| Layer | Control |
|---|---|
| Secrets | never in Git; `env:` indirection; scanned in CI and by `validate.py` |
| Config | `superset_config.py` raises on a missing `SECRET_KEY` or DB URI |
| Transport | HTTPS + secure cookies + Talisman enabled outside local dev |
| CSRF | enabled, with no exemptions — the GitOps client sends a real token |
| MCP surface | mutating tools not registered unless explicitly enabled |
| MCP identity | JWT in production; RBAC and row-level security always applied |
| Production writes | require `MCP_READ_ONLY=false` **and** `MCP_ALLOW_PROD_WRITES=true` |
| Audit | every deployment and MCP tool call records actor, target and result |

## Extension points

- **Secret backends** — `scripts/lib/environment.py::_resolve_reference`
  handles the `env:` prefix. Adding `vault:`, `aws-sm:`, `gcp-sm:` or
  `azure-kv:` is a change to that one function; nothing else moves.
- **New domains** — add to `DOMAINS` in `scripts/lib/conventions.py`.
- **New asset types** — add a normalizer to `_NORMALIZERS` in
  `scripts/lib/normalize.py` and a directory under `assets/`.
