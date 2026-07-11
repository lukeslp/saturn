"""Validation for the language-neutral Saturn findings contract."""

from __future__ import annotations

import math
from typing import Any, Mapping

CONTRACT_VERSION = 1
KINDS = {"dataset_profile", "dataset_comparison"}
COLUMN_KINDS = {"numeric", "text", "categorical", "boolean", "unknown"}


class ContractValidationError(ValueError):
    """A findings artifact violates contract v1."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ContractValidationError(message)


def _finite(value: Any, path: str = "$") -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise ContractValidationError(f"{path} must contain only finite numbers")
    if isinstance(value, Mapping):
        for key, item in value.items():
            _require(isinstance(key, str), f"{path} keys must be strings")
            _finite(item, f"{path}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _finite(item, f"{path}[{index}]")


def _descriptor(value: Any, path: str) -> None:
    _require(isinstance(value, dict), f"{path} must be an object")
    _require(isinstance(value.get("source"), str), f"{path}.source must be a string")
    rows = value.get("rowCount")
    _require(rows is None or isinstance(rows, int) and rows >= 0,
             f"{path}.rowCount must be a non-negative integer or null")
    schema = value.get("schema")
    _require(isinstance(schema, dict), f"{path}.schema must be an object")
    _require(all(isinstance(n, str) and k in COLUMN_KINDS for n, k in schema.items()),
             f"{path}.schema contains an invalid column kind")


def _profile(value: Any, path: str) -> None:
    _require(isinstance(value, dict), f"{path} must be an object")
    _require(isinstance(value.get("name"), str), f"{path}.name must be a string")
    _require(value.get("kind") in COLUMN_KINDS, f"{path}.kind is invalid")
    for key in ("count", "nullCount"):
        _require(isinstance(value.get(key), int) and value[key] >= 0,
                 f"{path}.{key} must be a non-negative integer")
    alerts = value.get("alerts", [])
    _require(isinstance(alerts, list), f"{path}.alerts must be an array")
    for index, alert in enumerate(alerts):
        _require(isinstance(alert, dict) and isinstance(alert.get("code"), str),
                 f"{path}.alerts[{index}].code must be a string")


def validate_contract(payload: Mapping[str, Any]) -> None:
    """Raise ``ContractValidationError`` unless *payload* is valid contract v1."""
    _require(isinstance(payload, Mapping), "contract root must be an object")
    _finite(payload)
    _require(payload.get("contractVersion") == CONTRACT_VERSION,
             "contractVersion must equal 1")
    kind = payload.get("kind")
    _require(kind in KINDS, "kind must be dataset_profile or dataset_comparison")
    for key in ("provenance", "options", "engine"):
        _require(isinstance(payload.get(key), dict), f"{key} must be an object")
    if "extensions" in payload:
        _require(isinstance(payload["extensions"], dict), "extensions must be an object")

    if kind == "dataset_profile":
        _descriptor(payload.get("descriptor"), "descriptor")
        columns = payload.get("columns")
        _require(isinstance(columns, list), "columns must be an array")
        for index, column in enumerate(columns):
            _profile(column, f"columns[{index}]")
        correlations = payload.get("correlations")
        if correlations is not None:
            _require(isinstance(correlations, dict), "correlations must be an object")
            labels, values, counts = (correlations.get(k) for k in ("labels", "values", "pairCounts"))
            _require(isinstance(labels, list) and all(isinstance(x, str) for x in labels),
                     "correlations.labels must be strings")
            n = len(labels)
            _require(isinstance(values, list) and len(values) == n and
                     all(isinstance(r, list) and len(r) == n for r in values),
                     "correlations.values must be a square matrix")
            _require(isinstance(counts, list) and len(counts) == n and
                     all(isinstance(r, list) and len(r) == n and
                         all(isinstance(v, int) and v >= 0 for v in r) for r in counts),
                     "correlations.pairCounts must be a square non-negative integer matrix")
    else:
        sides = payload.get("sides")
        _require(isinstance(sides, dict) and set(sides) >= {"a", "b"},
                 "sides must contain a and b")
        for side in ("a", "b"):
            _descriptor(sides[side], f"sides.{side}")
            _require(isinstance(sides[side].get("label"), str),
                     f"sides.{side}.label must be a string")
        columns = payload.get("columns")
        _require(isinstance(columns, list), "columns must be an array")
        for index, column in enumerate(columns):
            _require(isinstance(column, dict) and isinstance(column.get("name"), str),
                     f"columns[{index}].name must be a string")
            side_kinds = column.get("sideKinds")
            _require(isinstance(side_kinds, dict) and set(side_kinds) >= {"a", "b"},
                     f"columns[{index}].sideKinds must contain a and b")
            _require(all(v is None or v in COLUMN_KINDS for v in side_kinds.values()),
                     f"columns[{index}].sideKinds contains an invalid kind")
            _require(isinstance(column.get("compatible"), bool),
                     f"columns[{index}].compatible must be boolean")
