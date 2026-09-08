# Licensed under the Apache License, Version 2.0
"""Deterministic normalization of Superset export bundles into Git assets.

A raw Superset export is not fit for version control: filenames carry numeric
primary keys, dashboards embed runtime chart IDs, database URIs contain
credentials, and every bundle carries a fresh timestamp. Committing that
produces enormous diffs on every export even when nothing changed.

This module converts a raw bundle into a canonical form where the *only* thing
that changes between two exports is a real change to the asset. Every rule
below was derived from Superset 6.1.0's own importer/exporter source, not
guessed, because a rule that strips something the importer requires produces a
repo that cannot be deployed.

RULES
-----
metadata.yaml
    `timestamp` is regenerated on every export -> removed.

filenames
    Superset writes `charts/<name>_<pk>.yaml`. The primary key is
    environment-specific, so a chart exported from DEV and the same chart
    exported from PROD land in different files. Renamed to `<name>.yaml`.
    Import does not care about filenames, only about the top-level directory.

databases/*.yaml
    `sqlalchemy_uri` is exported with the password masked as `XXXXXXXXXX`, so
    it is useless *and* environment-specific. Replaced with a `${...}`
    placeholder resolved at import time from environment configuration.
    `database_name` carries an environment suffix by naming convention
    (SALES_DWH_DEV / SALES_DWH_PROD); the suffix is replaced with `${ENV}` so
    one file serves every environment. Identity is the UUID, which is stable.

charts/*.yaml
    `query_context` is a denormalized cache of the chart's query containing
    numeric datasource IDs. Superset regenerates it, and
    `update_chart_config_dataset()` rewrites it on import, so it is dropped.
    `params.datasource` ("<pk>__table") is a numeric pointer to the dataset.
    It is removed entirely rather than placeholder-substituted, because the
    importer unconditionally sets it from `dataset_uuid`
    (superset/commands/utils.py:205: `config["params"].update({"datasource": ...})`).
    Keeping a placeholder there would risk a literal "${...}" string surviving
    into a deployed chart; the real dependency edge is `dataset_uuid`, which is
    preserved and is what validation checks.

dashboards/*.yaml
    `position.<CHART-*>.meta.chartId` is a numeric primary key, but it CANNOT
    simply be deleted: the importer's `build_uuid_to_id_map()` reads
    `child["meta"]["chartId"]` unconditionally for every CHART node and would
    raise KeyError. Instead each chartId is rewritten to a deterministic
    integer derived from the chart's UUID, and every metadata structure that
    references chart IDs (expanded_slices, filter_scopes,
    timed_refresh_immune_slices, default_filters, scope.excluded) is rewritten
    through the same mapping so the file stays internally consistent.

all files
    Re-serialized with sorted keys and a fixed style so that map ordering can
    never produce a spurious diff.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

import yaml

#: Placeholder written in place of a real connection string.
DATABASE_URI_PLACEHOLDER = "${WAREHOUSE_URI}"
#: Environment suffixes recognised in database names by naming convention.
ENV_TOKENS = ("DEV", "STAGING", "PROD", "TEST")
#: Superset masks exported passwords with this literal.
MASKED_PASSWORD = "XXXXXXXXXX"

_FILENAME_ID_SUFFIX = re.compile(r"_(\d+)(?=\.yaml$)")


# --------------------------------------------------------------------------
# YAML helpers
# --------------------------------------------------------------------------
class _CanonicalDumper(yaml.SafeDumper):
    """Dumper that never emits aliases, so repeated values stay readable."""

    def ignore_aliases(self, data: Any) -> bool:  # noqa: D102
        return True


def dump_yaml(obj: Any) -> str:
    """Serialize deterministically: sorted keys, block style, stable width."""
    return yaml.dump(
        obj,
        Dumper=_CanonicalDumper,
        sort_keys=True,
        default_flow_style=False,
        allow_unicode=True,
        width=100,
        indent=2,
    )


def load_yaml(raw: bytes | str) -> Any:
    return yaml.safe_load(raw.decode() if isinstance(raw, bytes) else raw)


# --------------------------------------------------------------------------
# Stable identity
# --------------------------------------------------------------------------
def stable_chart_id(chart_uuid: str) -> int:
    """Map a chart UUID to a deterministic positive integer.

    Used to replace environment-specific chart primary keys inside dashboard
    position JSON. The value is meaningless to Superset - the importer
    overwrites it with the real new ID - but it must be present, stable and
    distinct per chart so that cross-references remain resolvable.
    """
    digest = hashlib.sha256(chart_uuid.encode()).hexdigest()
    # Keep it well inside a signed 32-bit range; 1-based to avoid a falsy id.
    return (int(digest[:8], 16) % 900_000_000) + 1


_ENV_SUFFIX_RE = re.compile(
    r"_(?:" + "|".join(ENV_TOKENS) + r")$", re.IGNORECASE
)


def canonical_name(name: str) -> str:
    """Filesystem-safe, environment-independent form of an asset name.

    `SALES_DWH_${ENV}` and `SALES_DWH_DEV` both canonicalise to `SALES_DWH`, so
    the same logical database occupies the same path in Git no matter which
    environment the export came from.
    """
    text = str(name).replace("${ENV}", "")
    text = re.sub(r"[^A-Za-z0-9_.-]+", "_", text)
    text = re.sub(r"_+", "_", text).strip("_")
    return _ENV_SUFFIX_RE.sub("", text) or text


def canonical_filename(path: str, config: dict[str, Any]) -> str:
    """Return the deterministic path for an exported asset file.

    Superset writes `charts/<name>_<pk>.yaml` and nests datasets under the
    exporting environment's database name (`datasets/SALES_DWH_DEV/...`). Both
    carry environment-specific information into Git, so both are rewritten.
    Import ignores filenames entirely - only the top-level directory matters -
    so this is safe.
    """
    directory, _, filename = path.rpartition("/")
    if not directory:
        return path

    segments = directory.split("/")
    kind = segments[0]
    # datasets/<database_name>/<table>.yaml - canonicalise the database folder.
    if kind == "datasets" and len(segments) > 1:
        segments = [kind] + [canonical_name(seg) for seg in segments[1:]]
    directory = "/".join(segments)

    name = {
        "charts": config.get("slice_name"),
        "dashboards": config.get("dashboard_title"),
        "datasets": config.get("table_name"),
        "databases": config.get("database_name"),
    }.get(kind)
    if name:
        return f"{directory}/{canonical_name(name)}.yaml"
    return f"{directory}/{_FILENAME_ID_SUFFIX.sub('', filename)}"


# --------------------------------------------------------------------------
# Per-kind normalization
# --------------------------------------------------------------------------
def normalize_metadata(config: dict[str, Any]) -> dict[str, Any]:
    config = dict(config)
    config.pop("timestamp", None)
    return config


def normalize_database(config: dict[str, Any]) -> dict[str, Any]:
    """Strip credentials and the environment suffix from a database config."""
    config = dict(config)
    uri = config.get("sqlalchemy_uri")
    if isinstance(uri, str) and not uri.startswith("${"):
        config["sqlalchemy_uri"] = DATABASE_URI_PLACEHOLDER

    name = config.get("database_name")
    if isinstance(name, str):
        for token in ENV_TOKENS:
            if name.upper().endswith(f"_{token}"):
                config["database_name"] = f"{name[: -(len(token))]}${{ENV}}"
                break
    # Runtime-only bookkeeping that differs per instance.
    for key in ("impersonate_user", "cache_timeout"):
        config.setdefault(key, config.get(key))
    return config


def normalize_dataset(config: dict[str, Any]) -> dict[str, Any]:
    """Sort dataset columns and metrics so ordering cannot cause diffs."""
    config = dict(config)
    if isinstance(config.get("columns"), list):
        config["columns"] = sorted(
            config["columns"], key=lambda c: str(c.get("column_name", ""))
        )
    if isinstance(config.get("metrics"), list):
        config["metrics"] = sorted(
            config["metrics"], key=lambda m: str(m.get("metric_name", ""))
        )
    return config


def normalize_chart(config: dict[str, Any]) -> dict[str, Any]:
    """Drop derived query state and mirror the importer's own param rewrites."""
    config = dict(config)
    # Regenerated by Superset and rewritten on import; pure diff noise.
    config["query_context"] = None

    params = config.get("params")
    if isinstance(params, dict):
        params = dict(params)
        # Removed rather than placeholdered: the importer always sets it.
        params.pop("datasource", None)
        # The importer runs filter_chart_annotations(), which rewrites
        # params["annotation_layers"] to the FORMULA-only subset - adding the
        # key as [] when it was absent. Applying the identical transform here
        # is what makes export(import(x)) == x; without it every chart gains a
        # spurious `annotation_layers: []` line on the first round trip.
        layers = params.get("annotation_layers") or []
        params["annotation_layers"] = [
            layer for layer in layers
            if isinstance(layer, dict) and layer.get("annotationType") == "FORMULA"
        ]
        config["params"] = params
    return config


def _remap_int_keyed(mapping: Any, id_map: dict[int, int]) -> Any:
    """Rewrite a {chart_id: value} mapping through id_map, keys as strings."""
    if not isinstance(mapping, dict):
        return mapping
    out: dict[str, Any] = {}
    for key, value in mapping.items():
        try:
            new_key = id_map.get(int(key), int(key))
        except (TypeError, ValueError):
            out[str(key)] = value
            continue
        out[str(new_key)] = value
    return out


def normalize_dashboard(config: dict[str, Any]) -> dict[str, Any]:
    """Replace runtime chart IDs with deterministic, UUID-derived IDs."""
    config = dict(config)
    position = config.get("position")
    if not isinstance(position, dict):
        return config

    position = json.loads(json.dumps(position))  # deep copy
    id_map: dict[int, int] = {}
    for node in position.values():
        if not (isinstance(node, dict) and node.get("type") == "CHART"):
            continue
        meta = node.get("meta")
        if not isinstance(meta, dict) or "uuid" not in meta:
            continue
        new_id = stable_chart_id(str(meta["uuid"]))
        old_id = meta.get("chartId")
        if isinstance(old_id, int):
            id_map[old_id] = new_id
        # chartId must remain present: the importer reads it unconditionally.
        meta["chartId"] = new_id
    config["position"] = position

    metadata = config.get("metadata")
    if isinstance(metadata, dict):
        metadata = json.loads(json.dumps(metadata))
        if "expanded_slices" in metadata:
            metadata["expanded_slices"] = _remap_int_keyed(
                metadata["expanded_slices"], id_map
            )
        if "timed_refresh_immune_slices" in metadata:
            metadata["timed_refresh_immune_slices"] = [
                id_map.get(i, i) for i in metadata["timed_refresh_immune_slices"] or []
            ]
        if isinstance(metadata.get("filter_scopes"), dict):
            scopes = _remap_int_keyed(metadata["filter_scopes"], id_map)
            for columns in scopes.values():
                if not isinstance(columns, dict):
                    continue
                for attributes in columns.values():
                    if isinstance(attributes, dict) and "immune" in attributes:
                        attributes["immune"] = [
                            id_map.get(i, i) for i in attributes["immune"] or []
                        ]
            metadata["filter_scopes"] = scopes
        if isinstance(metadata.get("default_filters"), str):
            try:
                parsed = json.loads(metadata["default_filters"])
                metadata["default_filters"] = json.dumps(
                    _remap_int_keyed(parsed, id_map), sort_keys=True
                )
            except (TypeError, ValueError):
                pass
        for native_filter in metadata.get("native_filter_configuration", []) or []:
            if not isinstance(native_filter, dict):
                continue
            excluded = native_filter.get("scope", {}).get("excluded")
            if excluded:
                native_filter["scope"]["excluded"] = [
                    id_map.get(i, i) for i in excluded
                ]
            # datasetId is a runtime pointer; datasetUuid is the portable one
            # and is what the importer consumes. Drop the former if both exist.
            for target in native_filter.get("targets", []) or []:
                if isinstance(target, dict) and "datasetUuid" in target:
                    target.pop("datasetId", None)
        if isinstance(metadata.get("global_chart_configuration"), dict):
            excluded = metadata["global_chart_configuration"].get("scope", {}).get(
                "excluded"
            )
            if excluded:
                metadata["global_chart_configuration"]["scope"]["excluded"] = [
                    id_map.get(i, i) for i in excluded
                ]
        config["metadata"] = metadata
    return config


_NORMALIZERS = {
    "databases": normalize_database,
    "datasets": normalize_dataset,
    "charts": normalize_chart,
    "dashboards": normalize_dashboard,
}


def normalize_config(path: str, config: dict[str, Any]) -> dict[str, Any]:
    """Apply the normalizer matching a bundle path."""
    if path == "metadata.yaml":
        return normalize_metadata(config)
    kind = path.split("/")[0]
    normalizer = _NORMALIZERS.get(kind)
    return normalizer(config) if normalizer else config


def normalize_bundle(files: dict[str, bytes]) -> dict[str, bytes]:
    """Normalize a whole export bundle: rewrite contents and canonicalise paths.

    Input and output are both {relative_path: bytes}. The result is what gets
    written into `assets/` and committed.
    """
    out: dict[str, bytes] = {}
    for path in sorted(files):
        raw = files[path]
        if not path.endswith((".yaml", ".yml")):
            out[path] = raw
            continue
        config = load_yaml(raw)
        if not isinstance(config, dict):
            out[path] = raw
            continue
        normalized = normalize_config(path, config)
        out[canonical_filename(path, normalized)] = dump_yaml(normalized).encode()
    return out


# --------------------------------------------------------------------------
# Git -> Superset: placeholder resolution
# --------------------------------------------------------------------------
_PLACEHOLDER = re.compile(r"\$\{([A-Z0-9_]+)\}")


class UnresolvedPlaceholderError(RuntimeError):
    """Raised when a ${VAR} in an asset has no value for the target environment."""


def resolve_placeholders(value: Any, context: dict[str, str], *,
                         path: str = "") -> Any:
    """Recursively substitute ${VAR} from `context`.

    Fails loudly on an unknown placeholder: deploying an asset with a literal
    "${WAREHOUSE_URI}" as its connection string would create a broken database
    entry that fails later and confusingly.
    """
    if isinstance(value, str):
        def _sub(match: re.Match[str]) -> str:
            key = match.group(1)
            if key not in context:
                raise UnresolvedPlaceholderError(
                    f"{path or '<root>'}: no value for ${{{key}}} in this environment"
                )
            return context[key]

        return _PLACEHOLDER.sub(_sub, value)
    if isinstance(value, dict):
        return {k: resolve_placeholders(v, context, path=f"{path}.{k}")
                for k, v in value.items()}
    if isinstance(value, list):
        return [resolve_placeholders(v, context, path=f"{path}[{i}]")
                for i, v in enumerate(value)]
    return value


def find_placeholders(value: Any) -> set[str]:
    """Collect every ${VAR} name appearing anywhere in a structure."""
    found: set[str] = set()
    if isinstance(value, str):
        found.update(_PLACEHOLDER.findall(value))
    elif isinstance(value, dict):
        for item in value.values():
            found |= find_placeholders(item)
    elif isinstance(value, list):
        for item in value:
            found |= find_placeholders(item)
    return found
