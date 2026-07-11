"""Dependency-light Saturn contract v1 domain and serialization API."""

from .contract import CONTRACT_VERSION, ContractValidationError, validate_contract
from .domain import ContractArtifact, DatasetComparisonArtifact, DatasetProfileArtifact
from .migration import migrate_legacy
from .serialization import dumps, load_contract, loads

__all__ = [
    "CONTRACT_VERSION", "ContractValidationError", "dumps", "load_contract",
    "loads", "migrate_legacy", "validate_contract", "ContractArtifact",
    "DatasetComparisonArtifact", "DatasetProfileArtifact",
]
