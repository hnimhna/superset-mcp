# Licensed under the Apache License, Version 2.0
"""Naming and content conventions enforced across the repository.

These are the rules documented in docs/naming-convention.md, expressed once so
that validation, CI and the unit tests all agree. A convention that is only
written in prose drifts; a convention that is executable does not.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: Business domains this platform serves. Extend deliberately - a new domain is
#: a governance decision, not a typo.
DOMAINS = ("SALES", "FINANCE", "MARKETING", "OPS", "PRODUCT")

#: Deployment environments that may appear as a database-name suffix.
ENVIRONMENTS = ("DEV", "STAGING", "PROD", "TEST")

_DOMAIN = "|".join(DOMAINS)
_ENV = "|".join(ENVIRONMENTS)

#: <DOMAIN>_<SYSTEM>_<ENV>, e.g. SALES_DWH_PROD. In Git the environment is held
#: as the ${ENV} placeholder, so both spellings are accepted.
DATABASE_RE = re.compile(rf"^({_DOMAIN})_[A-Z0-9]+(?:_[A-Z0-9]+)*_(?:{_ENV}|\$\{{ENV\}})$")

#: <DOMAIN>_<SUBJECT>_<GRAIN>, e.g. SALES_ORDER_DAILY.
DATASET_RE = re.compile(rf"^({_DOMAIN})_[A-Z0-9]+(?:_[A-Z0-9]+)+$")

#: <DOMAIN>_<PURPOSE>_<VISUALIZATION>, e.g. SALES_REVENUE_TREND.
CHART_RE = re.compile(rf"^({_DOMAIN})_[A-Z0-9]+(?:_[A-Z0-9]+)+$")

#: <DOMAIN>_<BUSINESS_PURPOSE>, e.g. SALES_EXECUTIVE_OVERVIEW.
DASHBOARD_RE = re.compile(rf"^({_DOMAIN})_[A-Z0-9]+(?:_[A-Z0-9]+)*$")

#: Metrics are semantic, lowercase snake_case identifiers.
METRIC_RE = re.compile(r"^[a-z][a-z0-9_]*$")

#: Metric names that indicate an unfinished or throwaway definition.
FORBIDDEN_METRIC_NAMES = frozenset({
    "metric1", "metric_1", "test_metric", "tmp", "temp_metric", "aaa",
    "foo", "bar", "test", "new_metric", "untitled", "copy_of_metric",
})

_PLACEHOLDER_NAME = re.compile(r"^(untitled|new chart|new dashboard|copy of|test)", re.I)


@dataclass(frozen=True)
class Violation:
    """One convention breach, addressable to a specific file."""

    path: str
    rule: str
    message: str

    def __str__(self) -> str:  # pragma: no cover - formatting only
        return f"{self.path}: [{self.rule}] {self.message}"


def check_database_name(name: str, path: str) -> list[Violation]:
    if not DATABASE_RE.match(name or ""):
        return [Violation(path, "naming.database",
                          f"{name!r} must match <DOMAIN>_<SYSTEM>_<ENV>, "
                          f"domain one of {', '.join(DOMAINS)} "
                          "(the environment is held as ${ENV} in Git)")]
    return []


def check_dataset_name(name: str, path: str) -> list[Violation]:
    if not DATASET_RE.match(name or ""):
        return [Violation(path, "naming.dataset",
                          f"{name!r} must match <DOMAIN>_<SUBJECT>_<GRAIN>")]
    return []


def check_chart_name(name: str, path: str) -> list[Violation]:
    issues: list[Violation] = []
    if _PLACEHOLDER_NAME.match(name or ""):
        issues.append(Violation(path, "naming.chart.placeholder",
                                f"{name!r} is a placeholder name"))
    elif not CHART_RE.match(name or ""):
        issues.append(Violation(path, "naming.chart",
                                f"{name!r} must match "
                                "<DOMAIN>_<PURPOSE>_<VISUALIZATION>"))
    return issues


def check_dashboard_name(name: str, path: str) -> list[Violation]:
    issues: list[Violation] = []
    if _PLACEHOLDER_NAME.match(name or ""):
        issues.append(Violation(path, "naming.dashboard.placeholder",
                                f"{name!r} is a placeholder name"))
    elif not DASHBOARD_RE.match(name or ""):
        issues.append(Violation(path, "naming.dashboard",
                                f"{name!r} must match <DOMAIN>_<BUSINESS_PURPOSE>"))
    return issues


def check_metric_name(name: str, path: str) -> list[Violation]:
    issues: list[Violation] = []
    if name in FORBIDDEN_METRIC_NAMES:
        issues.append(Violation(path, "naming.metric.placeholder",
                                f"metric {name!r} is a throwaway name; use a "
                                "semantic business term"))
    elif not METRIC_RE.match(name or ""):
        issues.append(Violation(path, "naming.metric",
                                f"metric {name!r} must be lowercase snake_case"))
    return issues
