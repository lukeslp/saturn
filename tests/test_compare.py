from __future__ import annotations

from pathlib import Path

import polars as pl

from saturn.compare import compare_dataframes, split_dataframe
from saturn.report import render_compare_html, write_compare_findings


def test_split_dataframe_picks_top_two():
    df = pl.DataFrame({"source_mode": ["a", "a", "a", "b", "b", "c"]})
    parts = split_dataframe(df, "source_mode")
    labels = [label for label, _ in parts]
    assert labels == ["a", "b"]
    sizes = [frame.height for _, frame in parts]
    assert sizes == [3, 2]


def test_compare_dataframes_produces_deltas():
    df_a = pl.DataFrame(
        {
            "alt_text": ["cat photo"] * 40 + ["a mountain landscape at sunset"] * 40,
            "length": [9] * 40 + [30] * 40,
            "mode": ["A"] * 80,
        }
    )
    df_b = pl.DataFrame(
        {
            "alt_text": ["x"] * 60 + ["y"] * 60,
            "length": [1] * 60 + [1] * 60,
            "mode": ["B"] * 120,
        }
    )

    report = compare_dataframes(
        df_a,
        df_b,
        label_a="A",
        label_b="B",
        source_a="test://a",
        source_b="test://b",
    )
    assert report.a.row_count == 80
    assert report.b.row_count == 120

    by_col = {c.column: c for c in report.columns}
    length = by_col["length"]
    assert length.delta["mean_a"] > length.delta["mean_b"]
    assert length.delta["mean_delta"] < 0


def test_render_compare_html(tmp_path: Path):
    df_a = pl.DataFrame({"x": list(range(50)), "g": ["p"] * 50})
    df_b = pl.DataFrame({"x": list(range(25, 75)), "g": ["q"] * 50})
    report = compare_dataframes(
        df_a, df_b, label_a="P", label_b="Q", source_a="a", source_b="b"
    )
    html_path = render_compare_html(report, tmp_path / "compare.html")
    findings_path = write_compare_findings(report, tmp_path / "compare.json")
    assert html_path.read_text().lower().count("plotly") > 0
    assert "P" in html_path.read_text()
    assert "Q" in html_path.read_text()
    assert findings_path.stat().st_size > 100
