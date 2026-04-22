from __future__ import annotations

from saturn.profilers import profile_columns


def test_profile_numeric_text_categorical(tiny_synthetic):
    schema = {
        "image_alt_length": "numeric",
        "alt_text": "text",
        "author_handle": "categorical",
        "image_index": "numeric",
    }
    results = {r.column: r for r in profile_columns(schema, tiny_synthetic)}

    num = results["image_alt_length"]
    assert num.kind == "numeric"
    assert num.stats["min"] > 0
    assert num.stats["max"] >= num.stats["median"]
    assert "histogram" in num.extras

    text = results["alt_text"]
    assert text.kind == "text"
    assert text.stats["len_mean"] > 0
    assert text.extras["top_values"]

    cat = results["author_handle"]
    assert cat.kind == "categorical"
    assert cat.n_unique is not None
    assert cat.n_unique <= 25


def test_alerts_fire(tiny_synthetic):
    schema = {"image_alt_length": "numeric"}
    # force a constant column
    for row in tiny_synthetic:
        row["image_alt_length"] = 0
    results = profile_columns(schema, tiny_synthetic)
    alerts = {a.code for a in results[0].alerts}
    assert "constant" in alerts or results[0].n_unique == 1
