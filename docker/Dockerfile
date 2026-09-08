# syntax=docker/dockerfile:1
#
# Superset image for the GitOps + MCP platform.
#
# Two facts about the official image drive this file, both verified directly
# against apache/superset:6.1.0 rather than assumed:
#
#  1. Python lives in a uv-managed virtualenv at /app/.venv, and that venv has
#     no `pip` module. A plain `pip install` resolves to /usr/local/bin/pip
#     (the *system* interpreter) and the packages are then invisible to
#     Superset. Installs must go through `uv pip install --python /app/.venv`.
#
#  2. The MCP service needs fastmcp>=3.1.0, which requires Python >= 3.11.
#     The default 6.1.0 tag ships Python 3.10, so the -py311 variant is used.
#     On 3.10 the install resolves to nothing and `superset mcp run` dies with
#     ModuleNotFoundError: No module named 'uvicorn'.
ARG SUPERSET_VERSION=6.1.0
FROM apache/superset:${SUPERSET_VERSION}-py311

USER root

ENV UV_LINK_MODE=copy

# MCP service dependencies plus the drivers this platform requires.
# `fastmcp` is the extra Superset declares for superset/mcp_service; it pulls
# uvicorn in transitively. Versions are pinned so the image is reproducible.
RUN uv pip install --python /app/.venv --no-cache \
      "fastmcp>=3.1.0,<4.0" \
      "psycopg2-binary==2.9.9" \
      "redis>=4.6.0,<6.0" \
 && /app/.venv/bin/python -c "import fastmcp, uvicorn, psycopg2, redis; print('mcp+driver deps OK')" \
 && /app/.venv/bin/python -c "from superset.mcp_service.server import run_server; print('superset mcp importable')"

# Superset config is baked in from the repo so the image is self-contained;
# every value inside it is read from the environment at runtime.
COPY --chown=superset:superset config/superset_config.py /app/pythonpath/superset_config.py
COPY --chown=superset:superset docker/mcp_server.py /app/pythonpath/mcp_server.py

ENV SUPERSET_CONFIG_PATH=/app/pythonpath/superset_config.py \
    PYTHONPATH=/app/pythonpath:/app \
    PYTHONDONTWRITEBYTECODE=1

USER superset

EXPOSE 8088 5008
