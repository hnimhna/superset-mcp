# Licensed under the Apache License, Version 2.0
"""Annotated template for `config/superset_config.py`.

`superset_config.py` in this repository is already safe to commit: it contains
no literal secrets and reads every sensitive value from the environment. This
file exists as a reference for operators standing up a new environment, and as
the place to record which variables must be provided.

It is NOT loaded by the application. To use it as a starting point for a fork:

    cp config/superset_config.example.py config/superset_config.py

Required environment variables (the process refuses to start without them):

    SUPERSET_SECRET_KEY        Flask/Superset signing key. Unique PER
                               ENVIRONMENT. Rotating it invalidates sessions
                               and any encrypted stored credentials.
    SUPERSET_METADATA_DB_URI   SQLAlchemy URI for Superset's own metadata DB.

Recommended:

    SUPERSET_ENV               dev | staging | prod. Drives the secure defaults
                               below; `prod` additionally forbids MCP dev-mode
                               auth and unguarded MCP writes.
    REDIS_URL                  enables Redis caching; without it Superset falls
                               back to a per-process in-memory cache, which is
                               wrong for any multi-worker deployment.
    SUPERSET_SECURE_COOKIES    default: true when SUPERSET_ENV=prod
    SUPERSET_TALISMAN_ENABLED  default: true when SUPERSET_ENV=prod

MCP:

    MCP_READ_ONLY              default TRUE. When true, tools tagged "mutate"
                               are not registered at all. See docs/mcp.md.
    MCP_AUTH_ENABLED           default: true when SUPERSET_ENV=prod.
    MCP_JWT_ALGORITHM          RS256 (default) or HS256.
    MCP_JWKS_URI               RS256: the identity provider's JWKS endpoint.
    MCP_JWT_SECRET             HS256 only. Never hardcode.
    MCP_JWT_ISSUER             expected `iss` claim.
    MCP_JWT_AUDIENCE          expected `aud` claim.
    MCP_REQUIRED_SCOPES        comma-separated scopes required on the token.
    MCP_ALLOW_PROD_WRITES      additionally required to expose mutating MCP
                               tools when SUPERSET_ENV=prod. Absent this, a
                               production instance with MCP_READ_ONLY=false
                               refuses to start.

Deployment provenance (optional, surfaced for drift reporting):

    GITOPS_COMMIT_SHA          the commit this instance is serving.
    GITOPS_DEPLOYMENT_ID       the deployment that put it there.

Design rules the real config follows, and any fork should keep:

  1. No secret has a literal default. `_require()` raises rather than falling
     back, because a silent default is how a development credential reaches
     production.
  2. One file serves every environment. Behaviour is switched by environment
     variables, never by editing code per environment.
  3. MCP write access is off unless explicitly enabled, and production needs
     two independent settings to turn it on.

See config/superset_config.py for the implementation and docs/mcp.md for the
reasoning behind the MCP settings.
"""

raise RuntimeError(
    "config/superset_config.example.py is documentation, not a runtime config. "
    "Use config/superset_config.py."
)
