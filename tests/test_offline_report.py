"""Static reports must not depend on remote scripts or stylesheets."""
from html.parser import HTMLParser
from pathlib import Path

import polars as pl
import pytest

from saturn.compare import compare_dataframes
from saturn.profilers import profile_columns
from saturn.report import assemble, render_compare_html, render_html


class ResourceParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.external_resources = []

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == "script" and values.get("src"):
            self.external_resources.append(values["src"])
        if tag == "link" and values.get("rel") == "stylesheet":
            self.external_resources.append(values.get("href"))


@pytest.mark.parametrize("comparison", [False, True])
def test_export_embeds_chart_runtime(tmp_path: Path, comparison):
    rows = [{"x": 1.0}, {"x": 2.0}, {"x": 3.0}]
    output = tmp_path / "report.html"
    if comparison:
        frame = pl.DataFrame(rows)
        report = compare_dataframes(frame, frame, label_a="A", label_b="B",
                                    source_a="synthetic:a", source_b="synthetic:b")
        render_compare_html(report, output)
    else:
        report = assemble(source="synthetic", row_count=3, sampled_rows=3,
                          seed=42, schema={"x": "numeric"},
                          results=profile_columns({"x": "numeric"}, rows))
        render_html(report, output)
    html = output.read_text()
    parser = ResourceParser()
    parser.feed(html)
    assert not parser.external_resources
    assert "plotly.js v" in html
