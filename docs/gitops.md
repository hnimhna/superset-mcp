# GitOps workflows

## Workflow A — Superset → Git

Used when an analyst builds or changes something in the Superset UI (or through
MCP in DEV).

```
Superset → export → normalize → git diff → commit → pull request
```

```bash
scripts/export.sh --env dev                       # everything
scripts/export.sh --env dev --dashboard SALES_EXECUTIVE_OVERVIEW
scripts/export.sh --env dev --check               # fail if Git is out of date
```

`--check` writes nothing and exits non-zero when the live instance differs from
the committed assets. It is the CI gate that keeps Git honest.

### Determinism

Two consecutive exports of an unchanged instance produce a byte-identical tree.
This is verified by `tests/integration/test_gitops_roundtrip.py`.

### Normalization rules

Every rule below was derived from Superset 6.1.0's own source, not guessed.

| File | Rule | Why |
|---|---|---|
| `metadata.yaml` | drop `timestamp` | regenerated on every export |
| all | rename `NAME_<pk>.yaml` → `NAME.yaml` | the pk differs per environment; import ignores filenames |
| all | sort keys, no YAML aliases | map ordering must never cause a diff |
| `datasets/` | canonicalise the `<database>/` directory | `datasets/SALES_DWH_DEV/` → `datasets/SALES_DWH/` |
| `databases/` | `sqlalchemy_uri` → `${WAREHOUSE_URI}` | exported masked *and* environment-specific |
| `databases/` | `SALES_DWH_DEV` → `SALES_DWH_${ENV}` | one file serves every environment; identity is the UUID |
| `datasets/` | sort `columns` and `metrics` | Superset returns them unordered |
| `charts/` | drop `query_context` | derived cache of numeric datasource IDs |
| `charts/` | drop `params.datasource` | importer always rewrites it from `dataset_uuid` |
| `charts/` | normalise `annotation_layers` | mirrors the importer's `filter_chart_annotations()` |
| `dashboards/` | rewrite `meta.chartId` to a UUID-derived integer | see below |
| `dashboards/` | remap every chart-ID reference in `metadata` | keeps the file internally consistent |
| `dashboards/` | drop `targets[].datasetId` when `datasetUuid` exists | the UUID is the portable reference |

**Why `chartId` is rewritten and not removed.** It is a primary key and
obviously environment-specific, so the instinct is to delete it. But
`superset/commands/dashboard/importers/v1/utils.py::build_uuid_to_id_map()`
reads `child["meta"]["chartId"]` for every CHART node without checking, so
removing it makes import fail with `KeyError`. Normalization therefore replaces
it with `stable_chart_id(uuid)` — deterministic, distinct per chart, identical
in every environment — and rewrites `expanded_slices`, `filter_scopes`,
`timed_refresh_immune_slices`, `default_filters` and `scope.excluded` through
the same mapping.

## Workflow B — Git → Superset

```
git checkout → validate → resolve placeholders → import → smoke test
```

```bash
.venv/bin/python scripts/validate.py          # offline; no Superset, no secrets
scripts/import.sh --env dev --dry-run         # resolve without deploying
scripts/import.sh --env dev
.venv/bin/python scripts/smoke_test.py --env dev
```

### Which import endpoint, and why it matters

Deployment uses **`/api/v1/assets/import/`** (`ImportAssetsCommand`).

The per-model endpoints are not interchangeable.
`/api/v1/dashboard/import/` calls `import_chart(config, overwrite=False)` and
`import_dataset(config, overwrite=False)` regardless of the `overwrite` form
field — that flag applies only to the dashboard itself. It therefore creates
missing dependencies but never updates existing ones, which means **a redeploy
does not correct drift** and Git is not actually authoritative.

`ImportAssetsCommand` passes `overwrite=True` to every model. Two API details:
its upload field is `bundle` (not `formData`), and `metadata.yaml` must declare
`type: assets`.

### Idempotency

Identity is the UUID, so repeated deployments update in place. Verified: three
consecutive imports leave object counts and UUIDs unchanged.

### Credentials

`assets/databases/*.yaml` holds `${WAREHOUSE_URI}`. At import:

1. `${WAREHOUSE_URI}` is resolved from `config/environments/<env>.yaml`, whose
   value is an `env:` reference resolved from the process environment.
2. Database passwords are additionally passed through the import endpoint's
   separate `passwords` parameter, keyed by asset path — so a credential never
   has to appear inside the bundle at all.

## Drift detection

```bash
.venv/bin/python scripts/diff.py --env prod
.venv/bin/python scripts/diff.py --env prod --quiet --fail-on-drift
```

| Status | Meaning |
|---|---|
| `ADDED` | in Git, not in Superset — a deploy would create it |
| `DELETED` | in Superset, not in Git — created outside GitOps |
| `DRIFT` | exists in both, differs, and Superset differs from `HEAD` |
| `MODIFIED` | differs, but Superset still matches `HEAD` — an uncommitted local edit |
| `UNCHANGED` | identical |

The `MODIFIED` / `DRIFT` distinction matters: an uncommitted local edit is a
normal pull-request state, while `DRIFT` means production was changed behind
the process. Only `DRIFT` and `DELETED` fail `--fail-on-drift`.

Drift is **never silently overwritten**. Reconcile deliberately:

```bash
scripts/export.sh --env prod    # accept the change into Git, then review it
scripts/import.sh --env prod    # discard it and restore Git's state
```

A nightly `drift-detection.yml` workflow checks STAGING and PROD, because
deploy-time checks only see drift that existed at deploy time.

## Rollback

Git history is the rollback mechanism.

```bash
.venv/bin/python scripts/rollback.py --env prod --list      # deployment history
.venv/bin/python scripts/rollback.py --env prod             # previous successful commit
.venv/bin/python scripts/rollback.py --env prod --to <sha>  # a specific commit
.venv/bin/python scripts/rollback.py --env prod --dry-run
```

Assets are read straight out of Git with `git show`, so the working tree is
never touched and a rollback works from a dirty checkout.

Deployments are recorded to `.deployments/<env>.jsonl` (gitignored):

```json
{"deployment_id":"20260908T114730123456Z","environment":"dev",
 "git_commit":"8ee0cef...","timestamp":"2026-09-08T11:47:30+00:00",
 "actor":"minhtla","result":"SUCCESS","detail":{"files":[...]}}
```

**Verified end to end:** a chart change was committed and deployed, the live
value confirmed changed, `rollback.py --env dev` restored the previous commit,
the live value returned to its original, and the smoke test passed 11/11.
