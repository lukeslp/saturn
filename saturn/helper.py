"""Dependency-light machine helper for isolated local profiling jobs."""

from __future__ import annotations

import json
import os
import signal
import tempfile
from pathlib import Path
from typing import Any, Literal, TypedDict

from . import __version__
from .compare import compare_dataframes
from .core import dumps, migrate_legacy, validate_contract
from .ingestion import _schema_from_dataframe
from .profilers import profile_dataframe


class InputDescriptor(TypedDict, total=False):
    source: str
    label: str


class JobInput(TypedDict):
    path: str
    format: Literal["arrow", "json"]
    descriptor: InputDescriptor


class JobEnvelope(TypedDict):
    jobVersion: int
    operation: Literal["profile", "compare"]
    inputs: list[JobInput]
    outputPath: str
    options: dict[str, Any]


class HelperError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class HelperCancelled(HelperError):
    def __init__(self):
        super().__init__("cancelled", "job cancelled")


def _event(event: str, phase: str, fraction: float, message: str) -> None:
    print(json.dumps({"event": event, "phase": phase, "fraction": fraction,
                      "message": message}, separators=(",", ":")), flush=True)


def _inside(root: Path, raw: Any, *, must_exist: bool) -> Path:
    if not isinstance(raw, str) or not raw:
        raise HelperError("invalid_envelope", "path must be a non-empty string")
    candidate = (root / raw).resolve(strict=False)
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise HelperError("path_outside_job_directory", "path leaves job directory") from exc
    if must_exist and not candidate.is_file():
        raise HelperError("input_not_found", f"input does not exist: {raw}")
    return candidate


def _validate(raw: Any, root: Path) -> tuple[JobEnvelope, list[Path], Path]:
    if not isinstance(raw, dict):
        raise HelperError("invalid_envelope", "job envelope must be an object")
    required = {"jobVersion", "operation", "inputs", "outputPath", "options"}
    if set(raw) != required:
        raise HelperError("invalid_envelope", "job envelope fields are invalid")
    if raw["jobVersion"] != 1:
        raise HelperError("unsupported_job_version", "jobVersion must equal 1")
    operation = raw["operation"]
    if operation not in {"profile", "compare"}:
        raise HelperError("invalid_operation", "operation must be profile or compare")
    inputs = raw["inputs"]
    expected = 1 if operation == "profile" else 2
    if not isinstance(inputs, list) or len(inputs) != expected:
        raise HelperError("invalid_envelope", f"{operation} requires {expected} input(s)")
    if not isinstance(raw["options"], dict):
        raise HelperError("invalid_envelope", "options must be an object")
    paths: list[Path] = []
    for item in inputs:
        if not isinstance(item, dict) or set(item) != {"path", "format", "descriptor"}:
            raise HelperError("invalid_envelope", "input fields are invalid")
        if item["format"] not in {"arrow", "json"}:
            raise HelperError("unsupported_format", "format must be arrow or json")
        descriptor = item["descriptor"]
        if (not isinstance(descriptor, dict) or not set(descriptor) <= {"source", "label"}
                or not isinstance(descriptor.get("source"), str)):
            raise HelperError("invalid_envelope", "descriptor is invalid")
        if "label" in descriptor and not isinstance(descriptor["label"], str):
            raise HelperError("invalid_envelope", "descriptor.label must be a string")
        paths.append(_inside(root, item["path"], must_exist=True))
    output = _inside(root, raw["outputPath"], must_exist=False)
    return raw, paths, output


def _load(path: Path, format: str):
    import polars as pl

    try:
        if format == "arrow":
            return pl.read_ipc(path)
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, list) or not all(isinstance(row, dict) for row in payload):
            raise ValueError("JSON input must be an array of record objects")
        return pl.DataFrame(payload, infer_schema_length=None)
    except HelperCancelled:
        raise
    except Exception as exc:
        raise HelperError("input_read_failed", f"could not read {path.name}: {exc}") from exc


def _json_normalize(value: Any) -> Any:
    """Convert profiler tuples/scalars to the language-neutral JSON value model."""
    return json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False))


def profile_artifact(frame, *, descriptor: InputDescriptor,
                     options: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return the same contract-v1 profile emitted by the helper."""
    options = options or {}
    seed = options.get("seed", 42)
    if type(seed) is not int or seed < 0:
        raise HelperError("invalid_options", "options.seed must be a non-negative integer")
    schema = _schema_from_dataframe(frame).columns
    results = profile_dataframe(frame, schema, sample_seed=seed)
    legacy = {
        "saturn_version": __version__,
        "meta": {"source": descriptor["source"], "row_count": frame.height,
                 "sampled_rows": frame.height, "seed": seed, "mode": "full",
                 "generated_at": None},
        "schema": schema,
        "columns": [result.to_dict() for result in results],
    }
    artifact = _json_normalize(migrate_legacy(legacy))
    validate_contract(artifact)
    return artifact


def compare_artifact(left, right, *, descriptors: list[InputDescriptor],
                     options: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return a safe pairwise contract-v1 comparison."""
    options = options or {}
    seed = options.get("seed", 42)
    if type(seed) is not int or seed < 0:
        raise HelperError("invalid_options", "options.seed must be a non-negative integer")
    a, b = descriptors
    report = compare_dataframes(
        left, right, label_a=a.get("label", "a"), label_b=b.get("label", "b"),
        source_a=a["source"], source_b=b["source"], seed=seed,
    )
    report.generated_at = ""
    artifact = _json_normalize(migrate_legacy({"saturn_version": __version__,
                                               **report.to_dict()}))
    artifact["provenance"]["generatedAt"] = None
    artifact["options"] = {"seed": seed}
    validate_contract(artifact)
    return artifact


def run_job(job_file: Path) -> Path:
    """Execute one v1 job and atomically replace its single output artifact."""
    job_file = job_file.resolve(strict=True)
    root = job_file.parent
    temp_path: Path | None = None
    previous_handlers: dict[int, Any] = {}

    def cancel(_signum, _frame):
        raise HelperCancelled()

    try:
        for signum in (signal.SIGTERM, signal.SIGINT):
            previous_handlers[signum] = signal.signal(signum, cancel)
        _event("progress", "validate", 0.05, "validating job")
        try:
            raw = json.loads(job_file.read_text(encoding="utf-8"))
        except Exception as exc:
            raise HelperError("malformed_job_json", f"could not read job: {exc}") from exc
        job, input_paths, output = _validate(raw, root)
        _event("progress", "read", 0.2, "reading input")
        frames = [_load(path, item["format"]) for path, item in zip(input_paths, job["inputs"])]
        _event("progress", "analyze", 0.5, "analyzing records")
        descriptors = [item["descriptor"] for item in job["inputs"]]
        artifact = (profile_artifact(frames[0], descriptor=descriptors[0], options=job["options"])
                    if job["operation"] == "profile" else
                    compare_artifact(frames[0], frames[1], descriptors=descriptors,
                                     options=job["options"]))
        _event("progress", "write", 0.9, "writing artifact")
        fd, name = tempfile.mkstemp(prefix=f".{output.name}.", suffix=".tmp", dir=root)
        temp_path = Path(name)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(dumps(artifact, indent=2) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_path, output)
        temp_path = None
        _event("complete", "complete", 1.0, "artifact written")
        return output
    except HelperError as exc:
        _event("cancelled" if isinstance(exc, HelperCancelled) else "error",
               exc.code, 1.0, str(exc))
        raise
    except KeyboardInterrupt as exc:
        error = HelperCancelled()
        _event("cancelled", error.code, 1.0, str(error))
        raise error from exc
    except Exception as exc:
        error = HelperError("analysis_failed", f"job failed: {exc}")
        _event("error", error.code, 1.0, str(error))
        raise error from exc
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)
        for signum, handler in previous_handlers.items():
            signal.signal(signum, handler)
