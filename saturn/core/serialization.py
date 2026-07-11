"""Strict deterministic JSON serialization for contract artifacts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from .contract import ContractValidationError, validate_contract
from .migration import migrate_legacy


def dumps(payload: Mapping[str, Any], *, indent: int | None = None) -> str:
    validate_contract(payload)
    try:
        return json.dumps(payload, ensure_ascii=False, allow_nan=False, indent=indent,
                          sort_keys=True, separators=None if indent else (",", ":"))
    except (TypeError, ValueError) as exc:
        raise ContractValidationError(str(exc)) from exc


def loads(text: str | bytes | bytearray) -> dict[str, Any]:
    try:
        payload = json.loads(text, parse_constant=lambda value: (_ for _ in ()).throw(
            ContractValidationError(f"non-finite JSON number: {value}")))
    except json.JSONDecodeError as exc:
        raise ContractValidationError(str(exc)) from exc
    if not isinstance(payload, dict):
        raise ContractValidationError("contract root must be an object")
    if "contractVersion" not in payload:
        payload = migrate_legacy(payload)
    validate_contract(payload)
    return payload


def load_contract(source: str | bytes | bytearray | Path | Mapping[str, Any]) -> dict[str, Any]:
    if isinstance(source, Mapping):
        payload = dict(source)
        if "contractVersion" not in payload:
            payload = migrate_legacy(payload)
        validate_contract(payload)
        return payload
    if isinstance(source, Path):
        return loads(source.read_text(encoding="utf-8"))
    return loads(source)
