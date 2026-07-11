"""Validation for the language-neutral Saturn findings contract."""

from __future__ import annotations

import math
from typing import Any, Mapping

CONTRACT_VERSION = 1
KINDS = {"dataset_profile", "dataset_comparison"}
COLUMN_KINDS = {"numeric", "text", "categorical", "boolean", "unknown"}
ALERT_LEVELS = {"info", "warn", "error"}


class ContractValidationError(ValueError):
    """A findings artifact violates contract v1."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ContractValidationError(message)


def _exact_keys(value: Mapping[str, Any], allowed: set[str], path: str) -> None:
    unexpected = set(value) - allowed
    _require(not unexpected, f"{path} contains unknown fields: {sorted(unexpected)}")


def _integer(value: Any, path: str, *, nullable: bool = False) -> None:
    if nullable and value is None:
        return
    _require(type(value) is int and value >= 0,
             f"{path} must be a non-negative integer{' or null' if nullable else ''}")


def _number(value: Any, path: str) -> None:
    _require(type(value) in (int, float) and math.isfinite(value),
             f"{path} must be a finite number")


def _json_value(value: Any, path: str = "$") -> None:
    if value is None or type(value) in (str, bool, int):
        return
    if type(value) is float:
        _require(math.isfinite(value), f"{path} must contain only finite numbers")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            _require(isinstance(key, str), f"{path} keys must be strings")
            _json_value(item, f"{path}.{key}")
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _json_value(item, f"{path}[{index}]")
        return
    raise ContractValidationError(f"{path} must be JSON-compatible")


def _metadata(payload: Mapping[str, Any]) -> None:
    provenance = payload.get("provenance")
    _require(isinstance(provenance, dict), "provenance must be an object")
    _exact_keys(provenance, {"generatedAt"}, "provenance")
    if "generatedAt" in provenance:
        _require(provenance["generatedAt"] is None or isinstance(provenance["generatedAt"], str),
                 "provenance.generatedAt must be a string or null")

    options = payload.get("options")
    _require(isinstance(options, dict), "options must be an object")
    _exact_keys(options, {"mode", "sampledRows", "seed"}, "options")
    if "mode" in options:
        _require(isinstance(options["mode"], str), "options.mode must be a string")
    for key in ("sampledRows", "seed"):
        if key in options:
            _integer(options[key], f"options.{key}")

    engine = payload.get("engine")
    _require(isinstance(engine, dict), "engine must be an object")
    _exact_keys(engine, {"name", "version"}, "engine")
    for key in ("name", "version"):
        _require(isinstance(engine.get(key), str) and bool(engine[key]),
                 f"engine.{key} must be a non-empty string")


def _descriptor(value: Any, path: str, *, side: bool = False) -> None:
    _require(isinstance(value, dict), f"{path} must be an object")
    allowed = {"source", "rowCount", "schema"} | ({"label"} if side else set())
    _exact_keys(value, allowed, path)
    required = allowed
    _require(required <= set(value), f"{path} is missing required fields")
    _require(isinstance(value["source"], str), f"{path}.source must be a string")
    if side:
        _require(isinstance(value["label"], str), f"{path}.label must be a string")
    _integer(value["rowCount"], f"{path}.rowCount", nullable=True)
    schema = value["schema"]
    _require(isinstance(schema, dict), f"{path}.schema must be an object")
    _require(all(isinstance(name, str) and kind in COLUMN_KINDS
                 for name, kind in schema.items()),
             f"{path}.schema contains an invalid column kind")


def _alert(value: Any, path: str) -> None:
    _require(isinstance(value, dict), f"{path} must be an object")
    _exact_keys(value, {"level", "code", "message"}, path)
    _require(set(value) == {"level", "code", "message"}, f"{path} is incomplete")
    _require(value["level"] in ALERT_LEVELS, f"{path}.level is invalid")
    code = value["code"]
    _require(isinstance(code, str) and bool(code) and
             all(part and part.replace("_", "").isalnum() and part[0].isalpha()
                 for part in code.split(".")), f"{path}.code is invalid")
    _require(isinstance(value["message"], str), f"{path}.message must be a string")


def _alerts(value: Any, path: str) -> None:
    _require(isinstance(value, list), f"{path} must be an array")
    for index, alert in enumerate(value):
        _alert(alert, f"{path}[{index}]")


def _profile(value: Any, path: str) -> None:
    _require(isinstance(value, dict), f"{path} must be an object")
    fields = {"name", "kind", "count", "nullCount", "uniqueCount", "nullRate",
              "stats", "details", "alerts"}
    _exact_keys(value, fields, path)
    _require(fields <= set(value), f"{path} is missing required fields")
    _require(isinstance(value["name"], str), f"{path}.name must be a string")
    _require(value["kind"] in COLUMN_KINDS, f"{path}.kind is invalid")
    _integer(value["count"], f"{path}.count")
    _integer(value["nullCount"], f"{path}.nullCount")
    _integer(value["uniqueCount"], f"{path}.uniqueCount", nullable=True)
    _number(value["nullRate"], f"{path}.nullRate")
    _require(0 <= value["nullRate"] <= 1, f"{path}.nullRate must be between 0 and 1")
    for key in ("stats", "details"):
        _require(isinstance(value[key], dict), f"{path}.{key} must be an object")
    _alerts(value["alerts"], f"{path}.alerts")


def _correlations(value: Any) -> None:
    _require(isinstance(value, dict), "correlations must be an object")
    _exact_keys(value, {"labels", "values", "pairCounts"}, "correlations")
    _require(set(value) == {"labels", "values", "pairCounts"},
             "correlations is missing required fields")
    labels = value["labels"]
    _require(isinstance(labels, list) and all(isinstance(item, str) for item in labels),
             "correlations.labels must be strings")
    size = len(labels)
    values = value["values"]
    _require(isinstance(values, list) and len(values) == size and
             all(isinstance(row, list) and len(row) == size for row in values),
             "correlations.values must be a square matrix")
    for row_index, row in enumerate(values):
        for column_index, item in enumerate(row):
            if item is not None:
                _number(item, f"correlations.values[{row_index}][{column_index}]")
    counts = value["pairCounts"]
    _require(isinstance(counts, list) and len(counts) == size and
             all(isinstance(row, list) and len(row) == size for row in counts),
             "correlations.pairCounts must be a square matrix")
    for row_index, row in enumerate(counts):
        for column_index, item in enumerate(row):
            _integer(item, f"correlations.pairCounts[{row_index}][{column_index}]")


def _comparison_column(value: Any, path: str) -> None:
    _require(isinstance(value, dict), f"{path} must be an object")
    fields = {"name", "sideKinds", "compatible", "profiles", "delta", "notes"}
    _exact_keys(value, fields, path)
    _require(fields <= set(value), f"{path} is missing required fields")
    _require(isinstance(value["name"], str), f"{path}.name must be a string")
    side_kinds = value["sideKinds"]
    _require(isinstance(side_kinds, dict) and set(side_kinds) == {"a", "b"},
             f"{path}.sideKinds must contain only a and b")
    _require(all(kind is None or kind in COLUMN_KINDS for kind in side_kinds.values()),
             f"{path}.sideKinds contains an invalid kind")
    _require(type(value["compatible"]) is bool, f"{path}.compatible must be boolean")
    profiles = value["profiles"]
    _require(isinstance(profiles, dict) and set(profiles) == {"a", "b"},
             f"{path}.profiles must contain only a and b")
    for side in ("a", "b"):
        if profiles[side] is not None:
            _profile(profiles[side], f"{path}.profiles.{side}")
    _require(isinstance(value["delta"], dict), f"{path}.delta must be an object")
    _require(isinstance(value["notes"], list) and
             all(isinstance(note, str) for note in value["notes"]),
             f"{path}.notes must be an array of strings")


def validate_contract(payload: Mapping[str, Any]) -> None:
    """Raise ``ContractValidationError`` unless *payload* is valid contract v1."""
    _require(isinstance(payload, Mapping), "contract root must be an object")
    _json_value(dict(payload))
    _require(type(payload.get("contractVersion")) is int and
             payload["contractVersion"] == CONTRACT_VERSION,
             "contractVersion must equal 1")
    kind = payload.get("kind")
    _require(kind in KINDS, "kind must be dataset_profile or dataset_comparison")
    common = {"contractVersion", "kind", "provenance", "options", "engine",
              "columns", "alerts", "extensions"}
    allowed = common | ({"descriptor", "correlations"} if kind == "dataset_profile"
                        else {"sides"})
    _exact_keys(payload, allowed, "contract root")
    required = common - {"extensions"}
    required |= {"descriptor"} if kind == "dataset_profile" else {"sides"}
    _require(required <= set(payload), "contract root is missing required fields")
    _metadata(payload)
    if "extensions" in payload:
        _require(isinstance(payload["extensions"], dict), "extensions must be an object")
    _alerts(payload["alerts"], "alerts")
    columns = payload["columns"]
    _require(isinstance(columns, list), "columns must be an array")

    if kind == "dataset_profile":
        _descriptor(payload["descriptor"], "descriptor")
        for index, column in enumerate(columns):
            _profile(column, f"columns[{index}]")
        if "correlations" in payload:
            _correlations(payload["correlations"])
    else:
        sides = payload["sides"]
        _require(isinstance(sides, dict) and set(sides) == {"a", "b"},
                 "sides must contain only a and b")
        for side in ("a", "b"):
            _descriptor(sides[side], f"sides.{side}", side=True)
        for index, column in enumerate(columns):
            _comparison_column(column, f"columns[{index}]")
