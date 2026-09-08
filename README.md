# Superset GitOps + MCP Platform

Production-grade management of Apache **Superset 6.1.0** where **Git is the source
of truth** for BI metadata, the Superset metadata database is treated as runtime
state, and Claude Code can drive Superset through the official **MCP** service
without bypassing review.

```
Claude Code ──MCP──► Superset MCP service (read-only by default)
                          │
                          ▼
                     Superset API ──► Superset UI
                          │
                          ▼
                 Superset metadata DB  ◄── runtime state, NOT the truth
                          ▲
                          │ export / import
                          ▼
                    Git repository  ◄── the source of truth
                          │
      validate · unit · integration · API · E2E · drift
                          │
                    DEV → STAGING → PROD
```

---

## Table of contents

- [Prerequisites](#prerequisites)
- [Local setup](#local-setup)
- [The GitOps workflows](#the-gitops-workflows)
- [Commands](#commands)
- [MCP setup](#mcp-setup)
- [Testing](#testing)
- [Deployment](#deployment)
- [Repository layout](#repository-layout)
- [Documentation](#documentation)
- [Known limitations](#known-limitations)

---

## Prerequisites

| Requirement | Version used | Notes |
|---|---|---|
| Docker + Compose | 29.7.2 / v5.4.0 | runs Superset, Postgres and Redis |
| Python | 3.12 | repo tooling only; Superset runs in Docker |
| Node.js | 20+ | Playwright E2E |
| Apache Superset | **6.1.0** | pinned via `SUPERSET_VERSION` |

The image is built from `apache/superset:6.1.0-py311`. The `-py311` variant is
required: the default `6.1.0` tag ships Python 3.10 and the MCP service needs
`fastmcp>=3.1.0`, which requires Python 3.11+.

## Local setup

```bash
# 1. Secrets. .env is gitignored and must never be committed.
cp .env.example .env
./scripts/gen_secrets.sh >> .env      # appends freshly generated values
$EDITOR .env                          # remove the CHANGE_ME placeholder lines

# 2. Repo tooling
python3.12 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
npm install && npx playwright install chromium

# 3. Start Superset, the warehouse, Redis and the MCP service
docker compose -f docker/docker-compose.yml --env-file .env up -d --build

# 4. Deploy the version-controlled assets
set -a; source .env; set +a
.venv/bin/python scripts/import.py --env dev

# 5. Confirm it works
.venv/bin/python scripts/smoke_test.py --env dev
```

Superset is then at <http://localhost:8088> and the MCP service at
<http://localhost:5008/mcp>.

The compose file refuses to start without `SUPERSET_SECRET_KEY`,
`METADATA_DB_PASSWORD`, `WAREHOUSE_DB_PASSWORD` and `SUPERSET_ADMIN_PASSWORD`.
That is deliberate: there is no insecure default to accidentally ship.

## The GitOps workflows

### A. Superset → Git (an analyst changed something)

```
Superset → export → normalize → git diff → commit → pull request
```

```bash
scripts/export.sh --env dev
git diff assets/            # review the semantic change
```

Export is **deterministic**: running it twice against an unchanged Superset
produces byte-identical files. Numeric primary keys, export timestamps,
credentials and derived query caches are all removed or rewritten - see
[docs/gitops.md](docs/gitops.md) for every rule and why it exists.

### B. Git → Superset (deploy a reviewed change)

```
git checkout → validate → resolve placeholders → import → smoke test
```

```bash
scripts/validate.py                        # offline, no Superset needed
scripts/import.sh --env dev --dry-run      # resolve without deploying
scripts/import.sh --env dev
scripts/smoke_test.py --env dev
```

Imports are **idempotent** - identity is the asset UUID, so deploying twice
updates rather than duplicates.

## Commands

| Task | Command |
|---|---|
| Start the stack | `docker compose -f docker/docker-compose.yml --env-file .env up -d --build` |
| Stop | `docker compose -f docker/docker-compose.yml --env-file .env down` |
| Reset completely | `docker compose -f docker/docker-compose.yml --env-file .env down -v` |
| Export Superset → Git | `scripts/export.sh --env dev` |
| Verify Git matches Superset | `scripts/export.sh --env dev --check` |
| Import Git → Superset | `scripts/import.sh --env dev` |
| Preview an import | `scripts/import.sh --env dev --dry-run` |
| Validate assets (offline) | `.venv/bin/python scripts/validate.py` |
| Detect drift | `.venv/bin/python scripts/diff.py --env prod --fail-on-drift` |
| Smoke test | `.venv/bin/python scripts/smoke_test.py --env dev` |
| Deployment history | `.venv/bin/python scripts/rollback.py --env prod --list` |
| Roll back | `.venv/bin/python scripts/rollback.py --env prod` |
| Seed a fresh DEV instance | `.venv/bin/python scripts/bootstrap_dev.py` |

Scripts read credentials from the environment. Locally, `scripts/*.sh` source
`.env` for you; when calling the Python entry points directly, run
`set -a; source .env; set +a` first.

## MCP setup

The project ships `.mcp.json`, so Claude Code discovers the server on its own:

```json
{ "mcpServers": { "superset": { "type": "http", "url": "http://localhost:5008/mcp" } } }
```

```bash
claude mcp list          # superset: http://localhost:5008/mcp (HTTP) - ✔ Connected
```

Project-scoped servers require a one-time approval prompt the first time you
start `claude` in this directory.

**MCP is read-only by default, including in DEV.** Superset tags every mutating
MCP tool `mutate`, and `docker/mcp_server.py` disables that tag, so the write
tools are not merely refused - they are never registered:

| `MCP_READ_ONLY` | Tools exposed | Mutating tools |
|---|---|---|
| `true` (default) | 16 | none; `update_chart` returns *Unknown tool* |
| `false` | 24 | all 8 exposed |

Read [docs/mcp.md](docs/mcp.md) before enabling writes anywhere.

## Testing

```bash
.venv/bin/python -m pytest tests/unit -q          # 125 tests, no services needed
.venv/bin/python -m pytest tests/api -q           # 42 tests, needs Superset + MCP
.venv/bin/python -m pytest tests/integration -q   # 12 tests, needs Superset
npx playwright test                               # 27 browser tests
npx playwright test --headed                      # watch them run
npm run test:e2e:update-snapshots                 # regenerate visual baselines
```

| Suite | Count | What it protects |
|---|---|---|
| unit | 125 | normalization determinism, naming rules, secret detection, asset integrity |
| API | 42 | auth, assets, SQL, import/export, MCP read-only enforcement |
| integration | 12 | dependency chain, idempotency, `export(import(x)) == x` |
| E2E | 27 | login, rendering, filters, navigation, visual regression |

See [docs/testing.md](docs/testing.md).

## Deployment

```
main → deploy-dev (automatic) → deploy-staging (on DEV success) → deploy-prod (manual approval)
```

Production additionally: refuses to deploy over unreviewed drift, backs up the
current state first, and rolls itself back automatically if verification fails.
See [docs/deployment.md](docs/deployment.md).

## Repository layout

```
assets/           version-controlled BI assets (the source of truth)
config/           superset_config.py + per-environment settings (no secrets)
docker/           Dockerfile, compose stack, MCP entrypoint
scripts/          export, import, validate, diff, smoke test, rollback
  lib/            normalization engine, REST/MCP clients, conventions
sql/              warehouse model, metric definitions, validation queries
tests/            unit, api, integration, e2e
docs/             architecture and standards
.github/workflows CI and the promotion pipeline
```

## Documentation

| Document | Contents |
|---|---|
| [CLAUDE.md](CLAUDE.md) | rules for AI agents working in this repository |
| [docs/architecture.md](docs/architecture.md) | components, data flow, design decisions |
| [docs/gitops.md](docs/gitops.md) | export, import, normalization, drift, rollback |
| [docs/mcp.md](docs/mcp.md) | MCP architecture, auth, the read-only model |
| [docs/testing.md](docs/testing.md) | the test strategy and how to extend it |
| [docs/deployment.md](docs/deployment.md) | promotion, rollback, production checklist |
| [docs/naming-convention.md](docs/naming-convention.md) | enforced naming rules |
| [docs/dashboard-standard.md](docs/dashboard-standard.md) | dashboard requirements |
| [docs/chart-standard.md](docs/chart-standard.md) | chart requirements |
| [docs/metric-standard.md](docs/metric-standard.md) | metric definitions |
| [docs/datasource-standard.md](docs/datasource-standard.md) | database and dataset rules |

## Known limitations

See [docs/deployment.md](docs/deployment.md#known-limitations) for the full list
with reasoning. In brief:

- **Not verified:** GitHub Actions workflows have not executed on GitHub (no
  remote is configured); STAGING and PROD environments do not exist here, so
  only DEV has been exercised end to end.
- MCP JWT authentication is implemented and configured but has been tested only
  in `MCP_AUTH_ENABLED=false` dev mode; no identity provider was available.
- Vault / AWS / GCP / Azure secret backends are designed for (the `env:`
  indirection seam) but only environment variables are implemented.
- Superset's `DBEventLogger` raises a `TypeError` when the MCP middleware logs a
  tool error; MCP auditing therefore relies on the middleware's structured
  stdout log. See [docs/mcp.md](docs/mcp.md#observability).
