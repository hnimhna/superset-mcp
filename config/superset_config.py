# Licensed under the Apache License, Version 2.0
"""
Superset runtime configuration for the GitOps + MCP platform.

Design rules enforced here:
  1. NO secret has a literal default. Anything sensitive is read from the
     environment and the process refuses to start if it is missing.
  2. The same file serves DEV / STAGING / PROD. Behaviour is switched by
     SUPERSET_ENV plus explicit environment variables, never by editing code.
  3. MCP write access is OFF unless explicitly enabled. Read-only is enforced
     natively by FastMCP tool-tag filtering, not merely by convention.

See docs/mcp.md and docs/deployment.md.
"""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------
# Environment helpers
# --------------------------------------------------------------------------
def _env(key: str, default: str | None = None) -> str | None:
    value = os.environ.get(key, default)
    return value.strip() if isinstance(value, str) else value


def _require(key: str) -> str:
    """Read a mandatory setting. Fail loudly rather than fall back to a default.

    A silent default for SECRET_KEY or a database URI is how a development
    credential reaches production, so this raises instead.
    """
    value = _env(key)
    if not value:
        raise RuntimeError(
            f"Required environment variable {key!r} is not set. "
            "Refusing to start with an insecure default. "
            "See config/environments/<env>.yaml and .env.example."
        )
    return value


def _bool(key: str, default: bool = False) -> bool:
    raw = _env(key)
    if raw is None:
        return default
    return raw.lower() in {"1", "true", "yes", "on"}


def _int(key: str, default: int) -> int:
    raw = _env(key)
    try:
        return int(raw) if raw is not None else default
    except ValueError:
        return default


SUPERSET_ENV = (_env("SUPERSET_ENV", "dev") or "dev").lower()
IS_PRODUCTION = SUPERSET_ENV == "prod"

# --------------------------------------------------------------------------
# Core security
# --------------------------------------------------------------------------
SECRET_KEY = _require("SUPERSET_SECRET_KEY")

# Metadata database. This is *runtime state*, not the source of truth.
# Git holds the asset definitions; this DB holds whatever is currently deployed.
SQLALCHEMY_DATABASE_URI = _require("SUPERSET_METADATA_DB_URI")
SQLALCHEMY_TRACK_MODIFICATIONS = False

# --------------------------------------------------------------------------
# Cookies / CSRF / transport security
# --------------------------------------------------------------------------
WTF_CSRF_ENABLED = True
# The import/export REST endpoints are exercised by CI and the GitOps scripts
# with an explicit CSRF token, so they are NOT exempted here.
WTF_CSRF_EXEMPT_LIST: list[str] = []
WTF_CSRF_TIME_LIMIT = None

SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
# Secure cookies require HTTPS; enabled everywhere except local dev.
SESSION_COOKIE_SECURE = _bool("SUPERSET_SECURE_COOKIES", IS_PRODUCTION)

TALISMAN_ENABLED = _bool("SUPERSET_TALISMAN_ENABLED", IS_PRODUCTION)
TALISMAN_CONFIG = {
    "force_https": IS_PRODUCTION,
    "content_security_policy": None,
    "session_cookie_secure": SESSION_COOKIE_SECURE,
}

# Public role. Empty in every environment: access is granted explicitly.
PUBLIC_ROLE_LIKE = None
AUTH_ROLE_PUBLIC = "Public"

# --------------------------------------------------------------------------
# Caching / async
# --------------------------------------------------------------------------
REDIS_URL = _env("REDIS_URL")

if REDIS_URL:
    CACHE_CONFIG = {
        "CACHE_TYPE": "RedisCache",
        "CACHE_DEFAULT_TIMEOUT": _int("CACHE_DEFAULT_TIMEOUT", 300),
        "CACHE_KEY_PREFIX": f"superset_{SUPERSET_ENV}_",
        "CACHE_REDIS_URL": REDIS_URL,
    }
    DATA_CACHE_CONFIG = {**CACHE_CONFIG, "CACHE_KEY_PREFIX": f"superset_data_{SUPERSET_ENV}_"}
    FILTER_STATE_CACHE_CONFIG = {
        **CACHE_CONFIG,
        "CACHE_KEY_PREFIX": f"superset_filter_{SUPERSET_ENV}_",
    }
    EXPLORE_FORM_DATA_CACHE_CONFIG = {
        **CACHE_CONFIG,
        "CACHE_KEY_PREFIX": f"superset_form_{SUPERSET_ENV}_",
    }
else:
    CACHE_CONFIG = {"CACHE_TYPE": "SimpleCache", "CACHE_DEFAULT_TIMEOUT": 300}

# --------------------------------------------------------------------------
# Feature flags
# --------------------------------------------------------------------------
FEATURE_FLAGS: dict[str, bool] = {
    # Tags are a first-class managed asset in assets/tags/.
    "TAGGING_SYSTEM": True,
    # Required for dashboard-native filters to round-trip through export/import.
    "DASHBOARD_CROSS_FILTERS": True,
    "EMBEDDED_SUPERSET": False,
    "ALERT_REPORTS": False,
    # Horizontal filter bar and drill-to-detail are UI conveniences that do not
    # affect serialized asset shape; enabled for analyst ergonomics in non-prod.
    "DRILL_TO_DETAIL": True,
    "DRILL_BY": True,
}

# --------------------------------------------------------------------------
# SQL Lab
# --------------------------------------------------------------------------
SQLLAB_CTAS_NO_LIMIT = True
SQL_MAX_ROW = _int("SUPERSET_SQL_MAX_ROW", 100_000)
SQLLAB_TIMEOUT = _int("SUPERSET_SQLLAB_TIMEOUT", 300)
SUPERSET_WEBSERVER_TIMEOUT = _int("SUPERSET_WEBSERVER_TIMEOUT", 300)

# --------------------------------------------------------------------------
# Import / export behaviour (GitOps critical)
# --------------------------------------------------------------------------
# Version-controlled assets carry ${PLACEHOLDER} URIs, never real credentials.
# Import supplies credentials out-of-band via the `passwords` parameter.
VERSIONED_EXPORT = True

# --------------------------------------------------------------------------
# MCP service
# --------------------------------------------------------------------------
# Superset 6.1 tags every MCP tool: read tools carry tag "core", mutating tools
# carry tag "mutate" (verified in superset/mcp_service/*/tool/*.py, e.g.
# update_chart.py -> tags=["mutate"], list_charts.py -> tags=["core"]).
#
# FastMCP applies MCP_FACTORY_CONFIG["exclude_tags"] at tool-registration time,
# so a read-only server does not merely refuse writes - the write tools are
# never advertised to the client at all. This is the enforcement point for
# MCP_READ_ONLY, and it is defence-in-depth on top of Superset RBAC.
MCP_READ_ONLY = _bool("MCP_READ_ONLY", True)  # default-deny writes

MCP_SERVICE_HOST = _env("MCP_SERVICE_HOST", "0.0.0.0")
MCP_SERVICE_PORT = _int("MCP_SERVICE_PORT", 5008)
MCP_SERVICE_URL = _env("MCP_SERVICE_URL")
MCP_DEBUG = _bool("MCP_DEBUG", False)

# Always enforce the acting user's RBAC and row-level security.
MCP_RBAC_ENABLED = True

# Superset 6.1 enables a tool-search proxy by default: tools/list returns only
# `search_tools` + `call_tool` + pinned tools, and the real registry is reached
# through that proxy. That proxy resolves tools from the underlying registry and
# bypasses FastMCP tag filtering, so it defeats exclude_tags. A BI governance
# platform needs the advertised tool surface to be exactly the permitted surface,
# so tool search is disabled by default here and the flat tool list is used.
MCP_TOOL_SEARCH_ENABLED = _bool("MCP_TOOL_SEARCH_ENABLED", False)

MCP_TOOL_SEARCH_CONFIG: dict[str, Any] = {
    "enabled": MCP_TOOL_SEARCH_ENABLED,
    "strategy": "bm25",
    "max_results": 5,
    "always_visible": ["health_check", "get_instance_info"],
    "search_tool_name": "search_tools",
    "call_tool_name": "call_tool",
    "compact_schemas": True,
    "max_description_length": 300,
    "include_schemas": False,
}

MCP_FACTORY_CONFIG: dict[str, Any] = {
    "name": None,
    "branding": None,
    "instructions": None,
    "auth": None,
    "lifespan": None,
    "tools": None,
    "include_tags": None,
    # The single lever that makes the MCP surface read-only.
    "exclude_tags": {"mutate"} if MCP_READ_ONLY else None,
    "config": None,
}

# --- MCP authentication ---------------------------------------------------
# Dev mode (no auth, fixed identity) is permitted ONLY outside production.
MCP_AUTH_ENABLED = _bool("MCP_AUTH_ENABLED", IS_PRODUCTION)

if MCP_AUTH_ENABLED:
    MCP_JWT_ALGORITHM = _env("MCP_JWT_ALGORITHM", "RS256")
    MCP_JWT_ISSUER = _env("MCP_JWT_ISSUER")
    MCP_JWT_AUDIENCE = _env("MCP_JWT_AUDIENCE")
    if MCP_JWT_ALGORITHM == "HS256":
        MCP_JWT_SECRET = _require("MCP_JWT_SECRET")
    else:
        MCP_JWKS_URI = _env("MCP_JWKS_URI")
        MCP_JWT_PUBLIC_KEY = _env("MCP_JWT_PUBLIC_KEY")
    _scopes = _env("MCP_REQUIRED_SCOPES")
    if _scopes:
        MCP_REQUIRED_SCOPES = [s.strip() for s in _scopes.split(",") if s.strip()]
else:
    if IS_PRODUCTION:
        raise RuntimeError(
            "MCP_AUTH_ENABLED=false is not permitted when SUPERSET_ENV=prod. "
            "Production MCP access requires JWT authentication."
        )
    # Fixed dev identity. Mutually exclusive with MCP_AUTH_ENABLED by design.
    MCP_DEV_USERNAME = _env("MCP_DEV_USERNAME", "admin")

# Hard invariant: production must never expose mutating MCP tools implicitly.
if IS_PRODUCTION and not MCP_READ_ONLY:
    if not _bool("MCP_ALLOW_PROD_WRITES", False):
        raise RuntimeError(
            "Refusing to start: MCP_READ_ONLY=false in production without "
            "MCP_ALLOW_PROD_WRITES=true. Production writes through MCP require "
            "explicit, auditable authorization. See docs/mcp.md."
        )
    logger.warning(
        "MCP PRODUCTION WRITE ACCESS IS ENABLED. Every mutating operation is "
        "audited via the Superset event logger."
    )

# --------------------------------------------------------------------------
# Observability
# --------------------------------------------------------------------------
ENABLE_TIME_ROTATE = True
LOG_LEVEL = _env("SUPERSET_LOG_LEVEL", "INFO")

# Structured event logging captures MCP tool invocations (actor, action,
# target, result) alongside normal Superset actions. See docs/mcp.md.
EVENT_LOGGER_CLASS = _env(
    "SUPERSET_EVENT_LOGGER",
    "superset.utils.log.DBEventLogger",
)

# Stamp every running instance so drift reports can name the deployed commit.
GIT_SHA = _env("GITOPS_COMMIT_SHA", "unknown")
DEPLOYMENT_ID = _env("GITOPS_DEPLOYMENT_ID", "local")
