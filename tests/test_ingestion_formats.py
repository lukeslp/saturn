"""Ingestion tests for the expanded file-format surface (XLSX/ODS/TSV/Feather)."""

from __future__ import annotations

from pathlib import Path

import polars as pl
import pytest

from saturn.ingestion import FileAdapter, adapter_for


def _sample_df() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "id": [1, 2, 3, 4, 5],
            "label": ["alpha", "beta", "gamma", "delta", "epsilon"],
            "value": [1.1, 2.2, 3.3, 4.4, 5.5],
        }
    )


# ---------- XLSX -----------------------------------------------------------


def test_xlsx_round_trip(tmp_path):
    path = tmp_path / "sheet.xlsx"
    _sample_df().write_excel(path)

    adapter = adapter_for(str(path))
    assert isinstance(adapter, FileAdapter)
    df = adapter.load_dataframe()
    assert df.height == 5
    assert df.columns == ["id", "label", "value"]

    schema = adapter.schema()
    assert schema.columns["id"] in {"numeric", "categorical"}
    assert schema.columns["label"] in {"categorical", "text"}
    assert schema.columns["value"] == "numeric"


def test_xlsx_multi_sheet_takes_first(tmp_path):
    import xlsxwriter

    path = tmp_path / "multi.xlsx"
    with xlsxwriter.Workbook(str(path)) as wb:
        first = wb.add_worksheet("first")
        first.write_row(0, 0, ["x", "y"])
        first.write_row(1, 0, [1, 2])
        second = wb.add_worksheet("second")
        second.write_row(0, 0, ["p", "q"])
        second.write_row(1, 0, [3, 4])

    df = FileAdapter(path).load_dataframe()
    # Took the first sheet — columns should be x,y
    assert list(df.columns) == ["x", "y"]


def test_xlsx_iter_batches(tmp_path):
    path = tmp_path / "sheet.xlsx"
    _sample_df().write_excel(path)

    batches = list(FileAdapter(path).iter_batches(batch_size=2))
    assert sum(len(b) for b in batches) == 5
    assert batches[0][0]["label"] == "alpha"


# ---------- TSV ------------------------------------------------------------


def test_tsv_load(tmp_path):
    path = tmp_path / "demo.tsv"
    path.write_text("id\tlabel\tvalue\n1\talpha\t1.5\n2\tbeta\t2.5\n3\tgamma\t3.5\n")
    df = FileAdapter(path).load_dataframe()
    assert df.height == 3
    assert df.columns == ["id", "label", "value"]


def test_tsv_schema_and_row_count(tmp_path):
    path = tmp_path / "demo.tsv"
    path.write_text("id\tlabel\n1\ta\n2\tb\n3\tc\n")
    adapter = FileAdapter(path)
    schema = adapter.schema()
    assert "id" in schema.columns
    assert "label" in schema.columns
    assert adapter.row_count() == 3


# ---------- Feather / Arrow ------------------------------------------------


def test_feather_round_trip(tmp_path):
    path = tmp_path / "data.feather"
    _sample_df().write_ipc(path)
    df = FileAdapter(path).load_dataframe()
    assert df.height == 5
    assert "value" in df.columns


def test_arrow_round_trip(tmp_path):
    path = tmp_path / "data.arrow"
    _sample_df().write_ipc(path)
    df = FileAdapter(path).load_dataframe()
    assert df.height == 5


# ---------- adapter_for dispatch ------------------------------------------


@pytest.mark.parametrize("ext", ["xlsx", "tsv", "feather", "arrow"])
def test_adapter_for_returns_file_adapter(tmp_path, ext):
    path = tmp_path / f"file.{ext}"
    if ext == "xlsx":
        _sample_df().write_excel(path)
    elif ext == "tsv":
        path.write_text("a\tb\n1\tx\n")
    else:
        _sample_df().write_ipc(path)
    adapter = adapter_for(str(path))
    assert isinstance(adapter, FileAdapter)


# ---------- end-to-end via profile_columns (sanity) ------------------------


def test_xlsx_end_to_end_profile(tmp_path):
    """Profile an XLSX file via the streaming path (iter_batches + profile_columns)."""
    from saturn.ingestion import reservoir_sample
    from saturn.profilers import profile_columns

    path = tmp_path / "sheet.xlsx"
    _sample_df().write_excel(path)
    adapter = FileAdapter(path)
    schema = adapter.schema()
    sample, seen = reservoir_sample(adapter.iter_batches(batch_size=100), n=100, seed=42)
    assert seen == 5
    results = profile_columns(schema.columns, sample)
    assert len(results) == 3
    value_col = next(r for r in results if r.column == "value")
    assert value_col.kind == "numeric"
    assert value_col.stats["mean"] == pytest.approx(3.3, abs=0.01)
