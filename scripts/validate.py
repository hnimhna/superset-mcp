#!/usr/bin/env python
# Licensed under the Apache License, Version 2.0
"""Validate the committed assets before they are allowed to deploy.

Everything here runs offline against `assets/` alone, so it is cheap enough to
run on every pull request and needs no Superset instance and no credentials.

Checks performed:
  structure   every asset parses, carries a UUID, and lives in a known directory
  identity    UUIDs are well formed and unique across the repository
  naming      names satisfy docs/naming-convention.md (see lib/conventions.py)
  references  every chart resolves to a dataset, every dataset to a database,
              and every dashboard chart reference resolves to a chart
  metrics     metrics are semantically named, documented and have expressions
  secrets     no credential-shaped value appears in any asset
  placeholders every ${VAR} used is defined by at least one environment

    python scripts/validate.py
    python scripts/validate.py --env prod    # also check prod can resolve it
"""

from __future__ import annotations

import argparse
import logging
import re
import sys
import uuid as uuid_lib
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.lib.conventions import (  # noqa: E402
    Violation,
    check_chart_name,
    check_dashboard_name,
    check_database_name,
    check_dataset_name,
    check_metric_name,
)
from scripts.lib.environment import available_environments, load_environment  # noqa: E402
from scripts.lib.normalize import find_placeholders, load_yaml  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("validate")

REPO_ROOT = Path(__file__).resolve().parents[1]
ASSETS_DIR = REPO_ROOT / "assets"

KNOWN_DIRS = {"databases", "datasets", "charts", "dashboards", "saved_queries", "tags"}

#: Patterns that indicate a real credential has been committed. Deliberately
#: conservative: a false positive costs a reviewer a minute, a false negative
#: leaks a production password.
SECRET_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("sqlalchemy-uri-with-password",
     re.compile(r"://[^:/\s]+:(?!XXXXXXXXXX|\$\{)[^@/\s]{3,}@")),
    ("aws-access-key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("private-key-block", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |PGP )?PRIVATE KEY-----")),
    ("github-token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b")),
    ("slack-token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")),
    ("google-api-key", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b")),
    ("bearer-token", re.compile(r"\bBearer\s+[A-Za-z0-9._\-]{20,}")),
    # `env:NAME`, `${PLACEHOLDER}`, null/empty and Superset's XXXXXXXXXX mask are
    # indirection, not credentials - only a literal value is a finding.
    ("password-assignment",
     re.compile(r"(?i)\b(password|passwd|secret|api_key|apikey|token)s?\s*[:=]\s*"
                r"(?!null\b|''|\"\"|\$\{|env:|XXXXXXXXXX|\{\}|\[\])"
                r"[^\s'\"]{8,}")),
]


def _iter_assets() -> list[tuple[str, dict[str, Any]]]:
    out: list[tuple[str, dict[str, Any]]] = []
    for path in sorted(ASSETS_DIR.rglob("*")):
        if not (path.is_file() and path.suffix in {".yaml", ".yml"}):
            continue
        rel = str(path.relative_to(ASSETS_DIR))
        config = load_yaml(path.read_bytes())
        if isinstance(config, dict):
            out.append((rel, config))
    return out


def check_structure(assets: list[tuple[str, dict[str, Any]]]) -> list[Violation]:
    issues: list[Violation] = []
    for path, config in assets:
        if path in {"metadata.yaml", "tags.yaml"}:
            continue
        top = path.split("/")[0]
        if top not in KNOWN_DIRS:
            issues.append(Violation(path, "structure.directory",
                                    f"unknown asset directory {top!r}"))
        if "uuid" not in config:
            issues.append(Violation(path, "structure.uuid",
                                    "asset has no uuid; UUID is the cross-environment identity"))
    return issues


def check_uuids(assets: list[tuple[str, dict[str, Any]]]) -> list[Violation]:
    issues: list[Violation] = []
    seen: dict[str, str] = {}
    for path, config in assets:
        raw = config.get("uuid")
        if raw is None:
            continue
        try:
            uuid_lib.UUID(str(raw))
        except (ValueError, AttributeError, TypeError):
            issues.append(Violation(path, "identity.uuid.malformed",
                                    f"{raw!r} is not a valid UUID"))
            continue
        if str(raw) in seen:
            issues.append(Violation(path, "identity.uuid.duplicate",
                                    f"UUID {raw} is also used by {seen[str(raw)]}"))
        else:
            seen[str(raw)] = path
    return issues


def check_naming(assets: list[tuple[str, dict[str, Any]]]) -> list[Violation]:
    issues: list[Violation] = []
    for path, config in assets:
        kind = path.split("/")[0]
        if kind == "databases":
            issues += check_database_name(config.get("database_name", ""), path)
        elif kind == "datasets":
            issues += check_dataset_name(config.get("table_name", ""), path)
        elif kind == "charts":
            issues += check_chart_name(config.get("slice_name", ""), path)
        elif kind == "dashboards":
            issues += check_dashboard_name(config.get("dashboard_title", ""), path)
    return issues


def check_metrics(assets: list[tuple[str, dict[str, Any]]]) -> list[Violation]:
    """Metrics must be semantic, documented and backed by an expression."""
    issues: list[Violation] = []
    expressions: dict[str, tuple[str, str]] = {}
    for path, config in assets:
        if not path.startswith("datasets/"):
            continue
        for metric in config.get("metrics") or []:
            if not isinstance(metric, dict):
                continue
            name = metric.get("metric_name", "")
            issues += check_metric_name(name, path)
            expression = (metric.get("expression") or "").strip()
            if not expression:
                issues.append(Violation(path, "metric.expression",
                                        f"metric {name!r} has no SQL expression"))
            if name != "count" and not (metric.get("description") or "").strip():
                issues.append(Violation(path, "metric.description",
                                        f"metric {name!r} has no business definition; "
                                        "every published metric needs one"))
            # Catch the same business metric defined twice with different SQL.
            normalized = re.sub(r"\s+", " ", expression.lower())
            if normalized and normalized in expressions:
                other_path, other_name = expressions[normalized]
                if other_name != name:
                    issues.append(Violation(
                        path, "metric.duplicate",
                        f"metric {name!r} duplicates {other_name!r} in "
                        f"{other_path} with identical SQL; define it once"))
            elif normalized:
                expressions[normalized] = (path, name)
    return issues


def check_references(assets: list[tuple[str, dict[str, Any]]]) -> list[Violation]:
    """Every dependency edge must resolve inside the repository."""
    issues: list[Violation] = []
    by_path = dict(assets)

    database_uuids = {
        str(c["uuid"]) for p, c in assets if p.startswith("databases/") and "uuid" in c
    }
    dataset_uuids = {
        str(c["uuid"]) for p, c in assets if p.startswith("datasets/") and "uuid" in c
    }
    chart_uuids = {
        str(c["uuid"]) for p, c in assets if p.startswith("charts/") and "uuid" in c
    }

    for path, config in assets:
        kind = path.split("/")[0]
        if kind == "datasets":
            ref = config.get("database_uuid")
            if not ref:
                issues.append(Violation(path, "reference.database.missing",
                                        "dataset does not declare database_uuid"))
            elif str(ref) not in database_uuids:
                issues.append(Violation(path, "reference.database.dangling",
                                        f"database_uuid {ref} is not in assets/databases/"))
        elif kind == "charts":
            ref = config.get("dataset_uuid")
            if not ref:
                issues.append(Violation(path, "reference.dataset.missing",
                                        "chart does not declare dataset_uuid"))
            elif str(ref) not in dataset_uuids:
                issues.append(Violation(path, "reference.dataset.dangling",
                                        f"dataset_uuid {ref} is not in assets/datasets/"))
        elif kind == "dashboards":
            position = config.get("position") or {}
            for node in position.values():
                if not (isinstance(node, dict) and node.get("type") == "CHART"):
                    continue
                meta = node.get("meta") or {}
                ref = meta.get("uuid")
                if not ref:
                    issues.append(Violation(path, "reference.chart.missing",
                                            f"chart node {node.get('id')} has no uuid"))
                elif str(ref) not in chart_uuids:
                    issues.append(Violation(
                        path, "reference.chart.dangling",
                        f"chart node {node.get('id')} references {ref}, "
                        "which is not in assets/charts/"))
                if "chartId" not in meta:
                    issues.append(Violation(
                        path, "reference.chart.chartId",
                        f"chart node {node.get('id')} has no chartId; Superset's "
                        "importer reads it unconditionally and import will fail"))
            # Native filters must point at a dataset that exists.
            metadata = config.get("metadata") or {}
            for native_filter in metadata.get("native_filter_configuration") or []:
                for target in (native_filter or {}).get("targets") or []:
                    ref = (target or {}).get("datasetUuid")
                    if ref and str(ref) not in dataset_uuids:
                        issues.append(Violation(
                            path, "reference.filter.dangling",
                            f"native filter {native_filter.get('id')} targets "
                            f"dataset {ref}, which is not in assets/datasets/"))
    _ = by_path
    return issues


def check_secrets(paths: list[Path]) -> list[Violation]:
    """Refuse to let anything credential-shaped reach the repository."""
    issues: list[Violation] = []
    for path in paths:
        rel = str(path.relative_to(REPO_ROOT))
        try:
            text = path.read_text(errors="ignore")
        except OSError:
            continue
        for line_no, line in enumerate(text.splitlines(), start=1):
            for name, pattern in SECRET_PATTERNS:
                if pattern.search(line):
                    issues.append(Violation(
                        f"{rel}:{line_no}", f"secret.{name}",
                        "possible credential committed; use a ${PLACEHOLDER} "
                        "resolved from environment configuration"))
    return issues


def check_placeholders(assets: list[tuple[str, dict[str, Any]]],
                       env_name: str | None) -> list[Violation]:
    """Every ${VAR} must be resolvable by at least one environment."""
    issues: list[Violation] = []
    envs = [env_name] if env_name else available_environments()
    if not envs:
        return issues
    defined: dict[str, set[str]] = {}
    for name in envs:
        try:
            defined[name] = set(load_environment(name, resolve=False).raw
                                .get("placeholders", {}) or {})
        except FileNotFoundError:
            issues.append(Violation(f"config/environments/{name}.yaml",
                                    "environment.missing", "no such environment"))
    for path, config in assets:
        used = find_placeholders(config)
        for name, keys in defined.items():
            missing = used - keys
            if missing:
                issues.append(Violation(
                    path, "placeholder.undefined",
                    f"{', '.join(sorted(missing))} not defined for environment "
                    f"{name!r}"))
    return issues


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--env", help="Also verify this environment can resolve the assets.")
    parser.add_argument("--skip-secrets", action="store_true",
                        help="Skip the secret scan (never use in CI).")
    args = parser.parse_args()

    if not ASSETS_DIR.is_dir():
        logger.error("no assets/ directory at %s", ASSETS_DIR)
        return 1

    assets = _iter_assets()
    logger.info("validating %d asset file(s)", len(assets))

    issues: list[Violation] = []
    issues += check_structure(assets)
    issues += check_uuids(assets)
    issues += check_naming(assets)
    issues += check_metrics(assets)
    issues += check_references(assets)
    issues += check_placeholders(assets, args.env)
    if not args.skip_secrets:
        scanned = [p for p in ASSETS_DIR.rglob("*") if p.is_file()]
        scanned += [p for p in (REPO_ROOT / "config").rglob("*.yaml") if p.is_file()]
        issues += check_secrets(scanned)

    if issues:
        by_rule: dict[str, list[Violation]] = {}
        for issue in issues:
            by_rule.setdefault(issue.rule.split(".")[0], []).append(issue)
        for group in sorted(by_rule):
            print(f"\n{group.upper()} ({len(by_rule[group])})")
            for issue in by_rule[group]:
                print(f"  {issue}")
        logger.error("validation FAILED with %d issue(s)", len(issues))
        return 1

    logger.info("validation PASSED: %d asset(s), no issues", len(assets))
    return 0


if __name__ == "__main__":
    sys.exit(main())
