"""Polars-native profiling path — the default mode."""

from __future__ import annotations

import polars as pl

from saturn.profilers import profile_dataframe


def test_profile_dataframe_basic():
    df = pl.DataFrame(
        {
            "image_alt_length": [10, 40, 15, 200, 25, 30, 80, 1200, 50, 60] * 20,
            "alt_text": (
                [
                    "A black cat on a sofa",
                    "ein Bild von einem Hund",
                    "foto de paisaje",
                    "a scenic mountain view at sunset with trees in the foreground",
                    "tweet preview image",
                ]
                * 40
            ),
            "author_handle": [f"user{i % 5}" for i in range(200)],
        }
    )
    schema = {
        "image_alt_length": "numeric",
        "alt_text": "text",
        "author_handle": "categorical",
    }
    results = {r.column: r for r in profile_dataframe(df, schema)}

    num = results["image_alt_length"]
    assert num.kind == "numeric"
    assert num.n == 200
    assert num.stats["max"] == 1200
    assert num.stats["min"] == 10
    assert "histogram" in num.extras

    text = results["alt_text"]
    assert text.kind == "text"
    assert text.stats["len_mean"] > 0
    assert text.extras["top_values"]
    assert isinstance(text.extras["top_values"][0], tuple)

    cat = results["author_handle"]
    assert cat.kind == "categorical"
    assert cat.n_unique == 5
    assert cat.stats["cardinality"] == 5


def test_profile_dataframe_handles_nulls():
    df = pl.DataFrame(
        {
            "x": [1.0, 2.0, None, 4.0, None],
            "y": ["a", None, "a", "b", None],
        }
    )
    schema = {"x": "numeric", "y": "categorical"}
    results = {r.column: r for r in profile_dataframe(df, schema)}
    assert results["x"].n == 5
    assert results["x"].n_null == 2
    assert results["y"].n_null == 2
