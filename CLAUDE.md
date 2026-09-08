# CLAUDE.md — operating rules for AI agents in this repository

This repository manages a production BI platform. Dashboards here are read by
people making decisions, and the assets in `assets/` are deployed to real
environments. Work accordingly.

---

## 1. The one rule everything else follows from

**Git is the source of truth. Superset is runtime state.**

The Superset metadata database holds whatever is currently deployed. It is not
authoritative and must never be edited directly (no `psql` into the metadata
DB, no `UPDATE slices SET ...`). Every durable change goes:

```
change in DEV → export → normalize → review → merge → promote
```

MCP is an *interface* for inspecting and developing. It is not a deployment
mechanism and must not be used to bypass review.

## 2. Before you change anything

1. Read the current Git state of the assets involved (`assets/`).
2. Understand the dependency chain: database → dataset → metric → chart → dashboard.
3. Check for drift before assuming Git matches reality:
   `python scripts/diff.py --env <env>`.
4. Make the **smallest** change that satisfies the request.

## 3. Never do these

| Prohibited | Why |
|---|---|
| Commit a secret of any kind | The repo is the deployment input; a leaked credential is deployed everywhere. |
| Write a literal connection string into `assets/` | Use `${WAREHOUSE_URI}`; the real value is resolved at import. |
| Edit the Superset metadata database directly | Bypasses review, versioning and rollback. |
| Deploy to PROD without explicit human authorization | Production changes are a human decision. |
| Enable MCP writes in PROD | `MCP_READ_ONLY=true` plus `MCP_ALLOW_PROD_WRITES` guard this; do not remove them. |
| Modify assets unrelated to the request | Silent collateral change is how trust is lost. |
| Hand-edit a file under `assets/` to "fix" a diff | Change it in Superset and re-export, or the next export reverts you. |
| Commit `.env`, `.deployments/`, or exported ZIPs | Already gitignored; do not force-add. |
| Delete `chartId` from dashboard position JSON | Superset's importer reads it unconditionally; import will fail. |
| Regenerate visual baselines to make a test pass | The baseline is the contract. Investigate the diff first. |

## 4. Making a BI change (the normal path)

```bash
set -a; source .env; set +a

# 1. Change it in DEV - through the UI, MCP, or scripts/bootstrap_dev.py
# 2. Bring it into Git
scripts/export.sh --env dev

# 3. Review what actually changed
git diff assets/

# 4. Validate offline
.venv/bin/python scripts/validate.py

# 5. Prove it works
.venv/bin/python scripts/smoke_test.py --env dev
npx playwright test

# 6. Commit and open a pull request
```

If `git diff` shows changes you did not intend, **stop and investigate** rather
than committing them. A noisy diff means either drift or a normalization
regression, and both matter.

## 5. Conventions that are enforced, not suggested

`scripts/validate.py` fails the build on any of these. The rules live as code in
`scripts/lib/conventions.py`; the prose is in `docs/naming-convention.md`.

| Asset | Pattern | Example |
|---|---|---|
| Database | `<DOMAIN>_<SYSTEM>_<ENV>` | `SALES_DWH_PROD` (in Git: `SALES_DWH_${ENV}`) |
| Dataset | `<DOMAIN>_<SUBJECT>_<GRAIN>` | `SALES_ORDER_DAILY` |
| Chart | `<DOMAIN>_<PURPOSE>_<VIZ>` | `SALES_REVENUE_TREND` |
| Dashboard | `<DOMAIN>_<BUSINESS_PURPOSE>` | `SALES_EXECUTIVE_OVERVIEW` |
| Metric | lowercase `snake_case`, semantic | `revenue`, `average_order_value` |

Metric names like `metric1`, `test_metric`, `tmp`, `aaa` are rejected outright.
Two metrics with identical SQL under different names are rejected: define a
business concept once.

## 6. Code conventions

**Python** (`scripts/`, `tests/`)
- Target 3.10+, `from __future__ import annotations`, full type hints.
- `ruff check scripts tests` must pass. Config in `pyproject.toml`.
- Fail loudly. Never default a missing credential or swallow an API error.
- Comments explain *why*, especially where the code works around a Superset
  behaviour — those are the comments that stop someone "simplifying" a fix back
  into a bug.

**SQL** (`sql/`)
- Uppercase keywords, explicit column lists, never `SELECT *`.
- CTEs over nested subqueries; alias every table; document non-obvious logic.
- Deterministic ordering wherever results are compared.

**Assets** (`assets/`)
- Machine-generated. Change Superset and re-export; do not hand-edit.

## 7. MCP rules

Use MCP for: discovery, inspection, querying, and development work in DEV.

Do **not** use MCP as a substitute for Git, CI/CD, code review, deployment
governance or secret management.

- Read-only is the default in every environment, enforced by
  `disable(tags={"mutate"})` in `docker/mcp_server.py`.
- Enabling writes is a deliberate, reviewed configuration change. In production
  it additionally requires `MCP_ALLOW_PROD_WRITES=true`, and the config refuses
  to start otherwise.
- Every MCP tool call is logged with actor, tool, target and result.
- After any MCP-driven change in DEV, **export it to Git** — otherwise it is
  drift and the next deployment reverts it.

## 8. Testing rules

- A change to `scripts/lib/normalize.py` requires a unit test naming the
  failure it prevents.
- A change affecting rendering requires an E2E assertion. API tests alone are
  not sufficient: a dashboard can pass every API check and still render as a
  grid of error tiles.
- Never weaken an assertion to make a test pass.
- Skip cleanly when a service is unavailable; never fake a pass.

Run before proposing a change:

```bash
.venv/bin/ruff check scripts tests
.venv/bin/python -m pytest tests/unit -q
.venv/bin/python scripts/validate.py
```

## 9. Superset behaviours worth knowing

These were established against a running Superset 6.1.0 and each one caused a
real bug during development:

- `/api/v1/dashboard/import/` imports charts and datasets with
  `overwrite=False` regardless of the `overwrite` form field, so it **cannot**
  correct drift. Deployment uses `/api/v1/assets/import/`
  (`ImportAssetsCommand`), which overwrites every model. Its upload field is
  `bundle`, not `formData`, and `metadata.yaml` must say `type: assets`.
- The dashboard exporter derives charts from `model.slices`, **not** from
  `position_json`. A chart must be linked via the chart-side `dashboards`
  field or it is silently missing from the export.
- The importer's `build_uuid_to_id_map()` reads `meta["chartId"]`
  unconditionally. Removing it raises `KeyError`; normalization rewrites it to
  a UUID-derived value instead.
- The importer runs `filter_chart_annotations()`, which adds
  `annotation_layers: []`. Normalization mirrors this, or every chart churns on
  its first round trip.
- FastMCP 3.x removed `exclude_tags` from the constructor; Superset still
  assigns `mcp.exclude_tags`, which is a silent no-op. Only
  `server.disable(tags=...)` works.
- The official image keeps Python in a uv venv at `/app/.venv` with **no pip
  module**. `pip install` silently targets the system interpreter; use
  `uv pip install --python /app/.venv`.

## 10. When something is not verified, say so

If an external dependency cannot be exercised here — a GitHub Actions run, a
STAGING environment, an identity provider — state `NOT VERIFIED` plainly rather
than implying it works. The known-limitations section of the README and
`docs/deployment.md` is the place for these.
