from __future__ import annotations

import importlib.resources
import json
import math
import subprocess
import sys
from pathlib import Path

import pytest

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


@pytest.mark.parametrize("name", ["dataset_profile.json", "dataset_comparison.json"])
def test_golden_contracts_validate_and_serialize_deterministically(name: str):
    expected = (FIXTURES / name).read_text(encoding="utf-8")
    artifact = load_contract(expected)

    assert dumps(artifact, indent=2) + "\n" == expected
    assert artifact["contractVersion"] == 1


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
