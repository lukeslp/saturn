"""Static domain shapes for Saturn contract v1 consumers."""

from __future__ import annotations

from typing import Any, Literal, TypedDict

ColumnKind = Literal["numeric", "text", "categorical", "boolean", "unknown"]


class Alert(TypedDict):
    level: str
    code: str
    message: str


class DatasetDescriptor(TypedDict):
    source: str
    rowCount: int | None
    schema: dict[str, ColumnKind]


class ColumnProfile(TypedDict):
    name: str
    kind: ColumnKind
    count: int
    nullCount: int
    uniqueCount: int | None
    nullRate: float
    stats: dict[str, Any]
    details: dict[str, Any]
    alerts: list[Alert]


class Correlations(TypedDict):
    labels: list[str]
    values: list[list[float | None]]
    pairCounts: list[list[int]]


class ComparisonSide(DatasetDescriptor):
    label: str


class ComparisonColumn(TypedDict):
    name: str
    sideKinds: dict[Literal["a", "b"], ColumnKind | None]
    compatible: bool
    profiles: dict[Literal["a", "b"], ColumnProfile | None]
    delta: dict[str, Any]
    notes: list[str]


class _ProfileOptional(TypedDict, total=False):
    correlations: Correlations
    extensions: dict[str, Any]


class DatasetProfileArtifact(_ProfileOptional):
    contractVersion: Literal[1]
    kind: Literal["dataset_profile"]
    descriptor: DatasetDescriptor
    provenance: dict[str, Any]
    options: dict[str, Any]
    engine: dict[str, Any]
    columns: list[ColumnProfile]
    alerts: list[Alert]


class _ComparisonOptional(TypedDict, total=False):
    extensions: dict[str, Any]


class DatasetComparisonArtifact(_ComparisonOptional):
    contractVersion: Literal[1]
    kind: Literal["dataset_comparison"]
    sides: dict[Literal["a", "b"], ComparisonSide]
    provenance: dict[str, Any]
    options: dict[str, Any]
    engine: dict[str, Any]
    columns: list[ComparisonColumn]
    alerts: list[Alert]


ContractArtifact = DatasetProfileArtifact | DatasetComparisonArtifact
