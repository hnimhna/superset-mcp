# Deployment

## Promotion path

```
pull request ──► validate.yml (must pass to merge)
       │
     merge to main
       │
       ├─► deploy-dev        automatic
       │        │
       │     success
       │        ▼
       ├─► deploy-staging    automatic, gated on DEV success
       │        │
       │   manual approval (GitHub Environment reviewers)
       │        ▼
       └─► deploy-prod       manual dispatch, confirmation text "DEPLOY"
```

## What each environment does differently

| | DEV | STAGING | PROD |
|---|---|---|---|
| Trigger | merge to `main` | DEV success | manual + approval |
| MCP writes | allowed | read-only | read-only, double-guarded |
| Drift blocks deploy | no | reported only | **yes** |
| Pre-deploy backup | best effort | no | **yes, retained 90 days** |
| Auto-rollback | no | yes | **yes** |
| E2E after deploy | in CI | full suite | render suite |

Analysts experiment in DEV and STAGING; drift there is expected. Production
drift is a governance failure and blocks the deployment.

## Production deployment sequence

`deploy-prod.yml` implements exactly the required sequence:

1. **Backup** — export current PROD and archive it, *before* anything changes,
   so a rollback target exists even if PROD had drifted.
2. **Drift gate** — `diff.py --fail-on-drift`. Refuses to proceed if someone
   changed production outside the workflow. Overridable only with an explicit
   `ignore_drift=true` input.
3. **Record the commit** — SHA and author written to the run summary.
4. **Import** Git assets via `/api/v1/assets/import/`.
5. **Smoke test** — connectivity, databases, datasets, metrics, charts,
   dashboards.
6. **Dashboard health** — Playwright render suite against production.
7. **Confirm convergence** — `diff.py --fail-on-drift` must now be clean.
8. **Record metadata** — deployment log, smoke report and backup archive
   uploaded as artifacts.

**Fail safely:** any failure in steps 5–7 triggers `rollback.py --env prod`
followed by a verifying smoke test.

## Required GitHub configuration

Per environment (`dev`, `staging`, `prod`) — Settings → Environments:

**Variables** (not secret)
```
SUPERSET_URL     https://superset.example.com
MCP_URL          https://superset.example.com:5008/mcp
```

**Secrets**
```
SUPERSET_API_USERNAME    service account for GitOps deployments
SUPERSET_API_PASSWORD
DATABASE_URI             warehouse SQLAlchemy URI
WAREHOUSE_DB_PASSWORD
```

For `prod`, also enable **Required reviewers** — that approval is the human
gate in the promotion path.

Use a dedicated service account with the minimum rights to import assets, not a
personal admin login.

## Rollback

```bash
.venv/bin/python scripts/rollback.py --env prod --list
.venv/bin/python scripts/rollback.py --env prod              # previous success
.venv/bin/python scripts/rollback.py --env prod --to <sha>
.venv/bin/python scripts/rollback.py --env prod --dry-run
.venv/bin/python scripts/smoke_test.py --env prod            # always verify
```

Git history is the rollback mechanism, not a metadata-database snapshot. A
database restore would also revert unrelated runtime state — query history,
user preferences, permissions — and cannot be reviewed. Re-importing a known
commit reverts exactly the assets and nothing else.

## Production deployment checklist

**Before**
- [ ] Change reviewed and merged through a pull request
- [ ] `validate.yml` green on the merge commit
- [ ] Deployed to STAGING and verified there
- [ ] `diff.py --env prod` clean, or the drift consciously reconciled
- [ ] Deployment window agreed with dashboard owners
- [ ] Someone available to verify and, if needed, roll back

**Configuration**
- [ ] `SUPERSET_ENV=prod`
- [ ] `SUPERSET_SECRET_KEY` unique to production, from the secret store
- [ ] `SUPERSET_SECURE_COOKIES=true`, `SUPERSET_TALISMAN_ENABLED=true`, HTTPS terminated
- [ ] `MCP_READ_ONLY=true`, `MCP_AUTH_ENABLED=true`, JWT issuer/audience/JWKS set
- [ ] `MCP_ALLOW_PROD_WRITES` **unset**
- [ ] Metadata database backed up on its own schedule (independent of GitOps)
- [ ] Redis reachable and `REDIS_URL` set
- [ ] Logs shipped to the aggregator (MCP audit lives in stdout — see docs/mcp.md)

**Deploy**
- [ ] `deploy-prod` dispatched with confirmation text `DEPLOY`
- [ ] Approval granted by a reviewer other than the author
- [ ] Backup artifact present in the run

**After**
- [ ] Smoke test passed
- [ ] Playwright render suite passed
- [ ] `diff.py --env prod --fail-on-drift` clean
- [ ] Dashboards spot-checked by a human
- [ ] Deployment record present in `.deployments/prod.jsonl`

## Observability

Each deployment records:

```json
{"deployment_id": "20260908T114730123456Z", "environment": "prod",
 "git_commit": "8ee0cef...", "timestamp": "2026-09-08T11:47:30+00:00",
 "actor": "minhtla", "result": "SUCCESS", "detail": {...}}
```

`GITOPS_COMMIT_SHA` and `GITOPS_DEPLOYMENT_ID` are also passed into the Superset
container, so a running instance can name the commit it is serving.

Logged elsewhere: import/export (script logs), MCP operations (middleware,
stdout), validation and drift (CI job summaries), tests (CI artifacts, retained
14–90 days).

## Known limitations

| Area | Status |
|---|---|
| GitHub Actions workflows | **NOT VERIFIED** — written and YAML-validated, but never executed on GitHub; this repository has no remote. |
| STAGING and PROD environments | **NOT VERIFIED** — do not exist here. Only DEV was exercised end to end. |
| MCP JWT authentication | **NOT VERIFIED** — implemented and configured; only `MCP_AUTH_ENABLED=false` dev mode was tested. No identity provider available. |
| Vault / AWS / GCP / Azure secrets | **NOT IMPLEMENTED** — the `env:` indirection in `scripts/lib/environment.py::_resolve_reference` is the designed seam; only environment variables work today. |
| MCP audit to the metadata DB | **BLOCKED UPSTREAM** — Superset's `DBEventLogger.log()` signature mismatch. Audit relies on the structured stdout log. |
| Alerts and reports | Not configured (`ALERT_REPORTS=false`); needs Celery workers and a beat scheduler. |
| Multi-domain assets | Only the `SALES` domain has reference assets. `FINANCE`, `MARKETING`, `OPS` and `PRODUCT` are accepted by the conventions but unused. |
| Visual baselines | **NOT VERIFIED in CI.** Only a `-chromium-darwin.png` baseline is committed, and Playwright writes-and-fails on a missing snapshot, so the PR gate runs with `--grep-invert "visual regression"`. See docs/testing.md for the one-command fix. |
| Concurrent deployments | Guarded by GitHub `concurrency` groups only; there is no server-side deployment lock. |
| Auto-rollback source | Restores the pre-deploy backup taken in step 1, not arbitrary history: `.deployments/` is gitignored and absent from a CI checkout. Verified locally with the history removed. |
