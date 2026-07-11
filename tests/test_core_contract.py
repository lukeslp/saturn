from __future__ import annotations

import importlib.resources
from copy import deepcopy
import json
import math
import subprocess
import sys
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from saturn.core import (
    ContractValidationError,
    dumps,
    load_contract,
    migrate_legacy,
    validate_contract,
)
from saturn.core.domain import (
    ColumnProfile,
    Correlations,
    DatasetComparisonArtifact,
    DatasetProfileArtifact,
)
from saturn.compare import ColumnComparison, CompareReport, CompareSide
from saturn.profilers import Alert, ProfileResult
from saturn.report import DatasetMeta, ReportData


FIXTURES = Path(__file__).parent / "fixtures" / "contract_v1"


def _schema_validator() -> Draft202012Validator:
    schema = importlib.resources.files("saturn.core.schemas").joinpath(
        "contract-v1.schema.json"
    )
    payload = json.loads(schema.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(payload)
    return Draft202012Validator(payload)


def _fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _replace(payload: dict, path: tuple[str | int, ...], value: object) -> dict:
    changed = deepcopy(payload)
    target = changed
    for part in path[:-1]:
        target = target[part]
    target[path[-1]] = value
    return changed


@pytest.mark.parametrize("name", ["dataset_profile.json", "dataset_comparison.json"])
def test_golden_contracts_validate_and_serialize_deterministically(name: str):
    expected = (FIXTURES / name).read_text(encoding="utf-8")
    artifact = load_contract(expected)

    assert dumps(artifact, indent=2) + "\n" == expected
    assert artifact["contractVersion"] == 1
    _schema_validator().validate(artifact)


@pytest.mark.parametrize(
    ("fixture", "path", "value"),
    [
        ("dataset_profile.json", ("contractVersion",), True),
        ("dataset_profile.json", ("descriptor", "rowCount"), True),
        ("dataset_profile.json", ("options", "sampledRows"), True),
        ("dataset_profile.json", ("options", "seed"), False),
        ("dataset_profile.json", ("columns", 0, "count"), True),
        ("dataset_profile.json", ("columns", 0, "uniqueCount"), True),
        ("dataset_profile.json", ("columns", 0, "nullRate"), "0"),
        ("dataset_profile.json", ("columns", 0, "stats"), []),
        ("dataset_profile.json", ("columns", 0, "details"), []),
        ("dataset_profile.json", ("columns", 0, "alerts"), [{"code": "x"}]),
        ("dataset_profile.json", ("alerts",), [{"level": "warn", "code": 7, "message": "x"}]),
        ("dataset_profile.json", ("correlations", "values"), [[True]]),
        ("dataset_profile.json", ("correlations", "values"), [["1"]]),
        ("dataset_profile.json", ("correlations", "pairCounts"), [[True]]),
        ("dataset_profile.json", ("correlations", "pairCounts"), [1]),
        ("dataset_comparison.json", ("sides", "a", "label"), 1),
        ("dataset_comparison.json", ("columns", 0, "sideKinds"), {"a": "numeric"}),
        ("dataset_comparison.json", ("columns", 0, "compatible"), 1),
        ("dataset_comparison.json", ("columns", 0, "profiles"), {"a": None}),
        ("dataset_comparison.json", ("columns", 0, "delta"), []),
        ("dataset_comparison.json", ("columns", 0, "notes"), [1]),
    ],
)
def test_malformed_contracts_are_rejected_by_runtime_and_schema(
    fixture: str, path: tuple[str | int, ...], value: object
):
    payload = _replace(_fixture(fixture), path, value)

    with pytest.raises(ContractValidationError):
        validate_contract(payload)
    assert not _schema_validator().is_valid(payload)


def test_unknown_contract_fields_are_rejected_but_extensions_are_open():
    payload = _fixture("dataset_profile.json")
    payload["descriptor"]["row_count"] = 3
    payload["extensions"] = {"org.example.future": {"anything": [1, True, None]}}

    with pytest.raises(ContractValidationError):
        validate_contract(payload)
    assert not _schema_validator().is_valid(payload)


def test_contract_serialization_rejects_non_finite_numbers():
    payload = json.loads((FIXTURES / "dataset_profile.json").read_text())
    payload["columns"][0]["stats"]["mean"] = math.nan

    with pytest.raises(ContractValidationError, match="finite"):
        dumps(payload)


def test_unknown_extensions_survive_load_dump_roundtrip():
    payload = json.loads((FIXTURES / "dataset_profile.json").read_text())
    payload["extensions"] = {"org.example.future": {"flag": True, "value": 7}}

    restored = load_contract(dumps(payload))

    assert restored["extensions"] == payload["extensions"]


def test_migrates_legacy_profile_findings():
    legacy = {
        "saturn_version": "0.2.0",
        "meta": {"source": "data.csv", "row_count": 2, "sampled_rows": 2,
                 "seed": 42, "mode": "full", "generated_at": "2026-01-01T00:00:00+00:00"},
        "schema": {"score": "numeric"},
        "columns": [{"column": "score", "kind": "numeric", "n": 2,
                     "n_null": 0, "n_unique": 2, "null_rate": 0.0,
                     "stats": {"mean": 1.5}, "extras": {},
                     "alerts": [{"level": "warn", "code": "outliers", "message": "x"}]}],
        "correlations": {"labels": ["score"], "matrix": [[1.0]], "pair_counts": [[2]]},
    }

    migrated = migrate_legacy(legacy)

    assert migrated["kind"] == "dataset_profile"
    assert migrated["descriptor"]["source"] == "data.csv"
    assert migrated["columns"][0]["alerts"][0]["code"] == "outliers"
    assert migrated["correlations"]["values"] == [[1.0]]
    assert migrated["correlations"]["pairCounts"] == [[2]]
    assert migrated["extensions"]["saturn.legacy"]["formatVersion"] == "0.2"
    validate_contract(migrated)
    _schema_validator().validate(migrated)


def test_migrates_legacy_comparison_without_coercing_side_kinds():
    legacy = {
        "saturn_version": "0.2.0",
        "a": {"label": "A", "source": "a.csv", "row_count": 1,
              "schema": {"value": "numeric"}, "language_counts": {}},
        "b": {"label": "B", "source": "b.csv", "row_count": 1,
              "schema": {"value": "text"}, "language_counts": {}},
        "columns": [{"column": "value", "kind": "numeric", "a": None, "b": None,
                     "delta": {}, "notes": ["schema drift"], "kind_a": "numeric",
                     "kind_b": "text", "compatible": False}],
        "divergences": [], "generated_at": "2026-01-01T00:00:00+00:00",
    }

    migrated = migrate_legacy(legacy)

    column = migrated["columns"][0]
    assert migrated["kind"] == "dataset_comparison"
    assert column["sideKinds"] == {"a": "numeric", "b": "text"}
    assert column["compatible"] is False
    validate_contract(migrated)
    _schema_validator().validate(migrated)


def test_report_and_comparison_offer_v1_adapters_without_changing_legacy_output():
    result = ProfileResult("score", "numeric", 2, 0, 2, {"mean": 1.5}, {},
                           [Alert("info", "constant", "example")])
    report = ReportData(
        meta=DatasetMeta("data.csv", 2, 2, 42, generated_at="2026-01-01T00:00:00+00:00"),
        schema={"score": "numeric"}, results=[result],
    )
    legacy_profile = report.to_findings()
    profile = report.to_contract_v1()

    assert "contractVersion" not in legacy_profile
    assert profile["kind"] == "dataset_profile"
    validate_contract(profile)

    comparison = CompareReport(
        a=CompareSide("A", "a.csv", 2, {"score": "numeric"}),
        b=CompareSide("B", "b.csv", 2, {"score": "text"}),
        columns=[ColumnComparison("score", "numeric", result, None,
                                  kind_a="numeric", kind_b="text", compatible=False)],
        generated_at="2026-01-01T00:00:00+00:00",
    )
    legacy_comparison = comparison.to_dict()
    contract = comparison.to_contract_v1()

    assert "contractVersion" not in legacy_comparison
    assert contract["kind"] == "dataset_comparison"
    validate_contract(contract)


def test_core_import_is_dependency_light():
    code = (
        "import sys; import saturn.core; "
        "forbidden={'flask','jinja2','plotly','polars','numpy','pandas','litellm'}; "
        "print(','.join(sorted(forbidden & set(sys.modules))))"
    )
    result = subprocess.run([sys.executable, "-I", "-c", code], check=True,
                            text=True, capture_output=True)
    assert result.stdout.strip() == ""


def test_json_schema_is_shipped_as_package_data():
    schema = importlib.resources.files("saturn.core.schemas").joinpath(
        "contract-v1.schema.json"
    )
    payload = json.loads(schema.read_text(encoding="utf-8"))

    assert payload["$id"].endswith("contract-v1.schema.json")
    assert payload["properties"]["contractVersion"]["const"] == 1


def test_domain_exposes_typed_profile_comparison_and_correlation_shapes():
    assert DatasetProfileArtifact.__required_keys__ >= {
        "contractVersion", "kind", "descriptor", "columns"
    }
    assert DatasetComparisonArtifact.__required_keys__ >= {
        "contractVersion", "kind", "sides", "columns"
    }
    assert ColumnProfile.__required_keys__ >= {"name", "kind", "count", "nullCount"}
    assert Correlations.__required_keys__ == {"labels", "values", "pairCounts"}
