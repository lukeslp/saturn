"""Saturn CLI entry point."""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from . import __version__
from .compare import compare_dataframes, split_dataframe
from .ingestion import adapter_for, reservoir_sample
from .profilers import profile_columns, profile_dataframe
from .report import (
    assemble,
    render_compare_html,
    render_html,
    write_compare_findings,
    write_findings,
)

try:
    from .viewer.app import DEFAULT_PORT as VIEWER_DEFAULT_PORT
    from .viewer.app import create_app
except ImportError:  # [web] extra not installed
    create_app = None
    VIEWER_DEFAULT_PORT = 5043

try:
    from .llm.engine import run_compare_insights, run_insights
    from .llm.gateway import parse_provider_spec
    from .llm.keys import MissingKeyError, load_api_keys
    _LLM_AVAILABLE = True
except ImportError:  # ~/shared/llm_providers not on PYTHONPATH
    run_insights = None
    run_compare_insights = None
    parse_provider_spec = None
    load_api_keys = None

    class MissingKeyError(RuntimeError):  # type: ignore[no-redef]
        pass

    _LLM_AVAILABLE = False


def _resolve_llm(llm_spec: list[str] | None):
    """Return (specs, api_keys) or None if the pass should be skipped (with a message)."""
    if not llm_spec:
        return None
    if not _LLM_AVAILABLE:
        console.print(
            "[red]--llm requires ~/shared on PYTHONPATH[/] (see docs/DEPLOY.md)"
        )
        return None
    try:
        specs = [parse_provider_spec(s) for s in llm_spec]
        keys = load_api_keys([s.provider for s in specs])
    except (MissingKeyError, ValueError) as e:
        console.print(f"[yellow]insight pass skipped:[/] {e}")
        return None
    return specs, keys


def _maybe_run_insights(data, llm_spec: list[str] | None) -> None:
    """Populate `data.insight_bundle` when --llm was passed. Fail-open throughout."""
    resolved = _resolve_llm(llm_spec)
    if resolved is None:
        return
    specs, keys = resolved

    label = ", ".join(s.label() for s in specs)
    with console.status(f"insight pass ({label})", spinner="dots"):
        bundle = run_insights(data, specs=specs, api_keys=keys)
    data.insight_bundle = bundle

    if bundle.errors:
        console.print(
            f"[yellow]insight pass completed with {len(bundle.errors)} error(s)[/]"
        )
    console.print(f"[green]✓[/] insight pass: {len(bundle.insights)} insight(s)")


def _maybe_run_compare_insights(report, llm_spec: list[str] | None) -> None:
    """Populate `report.insight_bundle` when --llm was passed on the compare command."""
    resolved = _resolve_llm(llm_spec)
    if resolved is None:
        return
    specs, keys = resolved

    label = ", ".join(s.label() for s in specs)
    with console.status(f"compare insight pass ({label})", spinner="dots"):
        bundle = run_compare_insights(report, specs=specs, api_keys=keys)
    report.insight_bundle = bundle

    if bundle.errors:
        console.print(
            f"[yellow]compare insight pass completed with {len(bundle.errors)} error(s)[/]"
        )
    console.print(
        f"[green]✓[/] compare insight pass: {len(bundle.insights)} insight(s)"
    )

app = typer.Typer(
    name="saturn",
    help="Dataset dissector. Stats pass is free and deterministic; language-model insight is opt-in.",
    add_completion=False,
    no_args_is_help=True,
)
console = Console()


def _run_full(
    source: str,
    *,
    split: str,
    config: str | None,
    seed: int,
    out: Path,
    findings: Path,
    open_browser: bool,
    llm_spec: list[str] | None = None,
) -> None:
    console.print(Panel(f"[bold]saturn[/bold] v{__version__}  —  {source}  [dim](full corpus)[/]", border_style="blue"))

    adapter = adapter_for(source, split=split, config=config)
    console.print(f"[dim]adapter:[/] {type(adapter).__name__}  [dim]→[/] {adapter.source}")

    with console.status("loading dataset", spinner="dots"):
        df = adapter.load_dataframe()
    console.print(f"[dim]loaded:[/] {df.height:,} rows × {df.width} cols  [dim]({df.estimated_size('mb'):.1f} MB in memory)[/]")

    with console.status("inferring schema", spinner="dots"):
        schema = adapter.schema()

    with console.status("profiling columns (vectorised)", spinner="dots"):
        results = profile_dataframe(df, schema.columns, sample_seed=seed)

    data = assemble(
        source=adapter.source,
        row_count=df.height,
        sampled_rows=df.height,
        seed=seed,
        schema=schema.columns,
        results=results,
        mode="full",
    )

    _print_summary(schema.columns, results, df.height)

    _maybe_run_insights(data, llm_spec)

    out_path = render_html(data, out)
    findings_path = write_findings(data, findings)
    console.print(f"[green]✓[/] HTML report: [bold]{out_path}[/]")
    console.print(f"[green]✓[/] JSON findings: [bold]{findings_path}[/]")

    if open_browser:
        import webbrowser

        webbrowser.open(out_path.as_uri())


def _run_sampled(
    source: str,
    *,
    split: str,
    config: str | None,
    sample_size: int,
    seed: int,
    out: Path,
    findings: Path,
    open_browser: bool,
    llm_spec: list[str] | None = None,
) -> None:
    console.print(
        Panel(
            f"[bold]saturn[/bold] v{__version__}  —  {source}  [dim](sample mode, n={sample_size})[/]",
            border_style="blue",
        )
    )

    adapter = adapter_for(source, split=split, config=config)
    console.print(f"[dim]adapter:[/] {type(adapter).__name__}  [dim]→[/] {adapter.source}")

    with console.status("scanning schema", spinner="dots"):
        schema = adapter.schema()

    row_count = adapter.row_count()
    if row_count is not None:
        console.print(f"[dim]rows (reported):[/] {row_count:,}")

    with console.status(f"reservoir sampling (n={sample_size}, seed={seed})", spinner="dots"):
        sample, seen = reservoir_sample(
            adapter.iter_batches(batch_size=5_000), n=sample_size, seed=seed
        )
    effective_count = row_count if row_count is not None else seen
    console.print(f"[dim]sampled:[/] {len(sample):,} rows from {seen:,}")

    with console.status("profiling columns", spinner="dots"):
        results = profile_columns(schema.columns, sample)

    data = assemble(
        source=adapter.source,
        row_count=effective_count,
        sampled_rows=len(sample),
        seed=seed,
        schema=schema.columns,
        results=results,
        mode="sample",
    )

    _print_summary(schema.columns, results, effective_count)

    _maybe_run_insights(data, llm_spec)

    out_path = render_html(data, out)
    findings_path = write_findings(data, findings)
    console.print(f"[green]✓[/] HTML report: [bold]{out_path}[/]")
    console.print(f"[green]✓[/] JSON findings: [bold]{findings_path}[/]")

    if open_browser:
        import webbrowser

        webbrowser.open(out_path.as_uri())


def _print_summary(schema: dict[str, str], results, row_count: int | None) -> None:
    table = Table(title=f"{row_count:,} rows" if row_count else "dataset", show_lines=False)
    table.add_column("column", style="bold")
    table.add_column("kind", style="cyan")
    table.add_column("null%", justify="right")
    table.add_column("unique", justify="right")
    table.add_column("alerts")
    for r in results:
        alert_strs = []
        for a in r.alerts:
            color = {"info": "white", "warn": "yellow", "error": "red"}.get(a.level, "white")
            alert_strs.append(f"[{color}]{a.code}[/]")
        table.add_row(
            r.column,
            r.kind,
            f"{r.null_rate:.1%}",
            f"{r.n_unique:,}" if r.n_unique is not None else "-",
            " ".join(alert_strs) or "[dim]—[/]",
        )
    console.print(table)


@app.command(name="analyze", help="Analyse a dataset (HuggingFace repo id or local file). Full corpus by default.")
def analyze(
    source: str = typer.Argument(..., help="HF repo id (user/dataset) or local file path"),
    sample_size: int | None = typer.Option(
        None,
        "--sample",
        help="opt into streaming sample mode (quick peek, not exhaustive)",
    ),
    seed: int = typer.Option(42, "--seed", help="random seed for deterministic sub-samples"),
    out: Path = typer.Option(Path("saturn_report.html"), "--out"),
    findings: Path = typer.Option(Path("saturn_findings.json"), "--findings"),
    split: str | None = typer.Option(None, "--split", help="HF dataset split (default: concatenate every split)"),
    config: str | None = typer.Option(None, "--config", help="HF dataset config name"),
    open_browser: bool = typer.Option(False, "--open", help="open report in browser after run"),
    llm_spec: list[str] = typer.Option(
        None,
        "--llm",
        help="provider[:model] to run insight pass. Repeat for primary + critic. "
             "Example: --llm anthropic --llm openai:gpt-4o-mini",
    ),
) -> None:
    if sample_size:
        _run_sampled(
            source,
            split=split,
            config=config,
            sample_size=sample_size,
            seed=seed,
            out=out,
            findings=findings,
            open_browser=open_browser,
            llm_spec=llm_spec,
        )
    else:
        _run_full(
            source,
            split=split,
            config=config,
            seed=seed,
            out=out,
            findings=findings,
            open_browser=open_browser,
            llm_spec=llm_spec,
        )


@app.command(name="huggingface", help="Convenience: analyse a HuggingFace dataset.")
def huggingface(
    repo: str = typer.Argument(..., help="HuggingFace repo id (user/dataset)"),
    sample_size: int | None = typer.Option(None, "--sample"),
    seed: int = typer.Option(42, "--seed"),
    out: Path = typer.Option(Path("saturn_report.html"), "--out"),
    findings: Path = typer.Option(Path("saturn_findings.json"), "--findings"),
    split: str | None = typer.Option(None, "--split", help="HF split (default: concat every split)"),
    config: str | None = typer.Option(None, "--config"),
    open_browser: bool = typer.Option(False, "--open"),
    llm_spec: list[str] = typer.Option(
        None,
        "--llm",
        help="provider[:model] to run insight pass. Repeat for primary + critic.",
    ),
) -> None:
    src = f"hf://{repo}"
    if sample_size:
        _run_sampled(
            src,
            split=split,
            config=config,
            sample_size=sample_size,
            seed=seed,
            out=out,
            findings=findings,
            open_browser=open_browser,
            llm_spec=llm_spec,
        )
    else:
        _run_full(
            src,
            split=split,
            config=config,
            seed=seed,
            out=out,
            findings=findings,
            open_browser=open_browser,
            llm_spec=llm_spec,
        )


@app.command(
    name="compare",
    help="Compare two datasets (or two slices of one) column-by-column.",
)
def compare(
    source_a: str = typer.Argument(..., help="First source (HF repo id or file path)"),
    source_b: str | None = typer.Argument(
        None,
        help="Second source. Omit + pass --by COL to split source_a by column value.",
    ),
    by: str | None = typer.Option(
        None,
        "--by",
        help="When source_b is omitted, split source_a by this column's two most frequent values",
    ),
    values: str | None = typer.Option(
        None,
        "--values",
        help="Comma-separated values for --by (default: top two by frequency)",
    ),
    label_a: str | None = typer.Option(None, "--label-a"),
    label_b: str | None = typer.Option(None, "--label-b"),
    out: Path = typer.Option(Path("saturn_compare.html"), "--out"),
    findings: Path = typer.Option(Path("saturn_compare.json"), "--findings"),
    seed: int = typer.Option(42, "--seed"),
    split_a: str | None = typer.Option(None, "--split-a", help="HF split for source_a"),
    split_b: str | None = typer.Option(None, "--split-b", help="HF split for source_b"),
    open_browser: bool = typer.Option(False, "--open"),
    llm_spec: list[str] = typer.Option(
        None,
        "--llm",
        help="provider[:model] to run compare-mode insight pass. Repeat for primary + critic.",
    ),
) -> None:
    console.print(Panel(f"[bold]saturn compare[/bold] v{__version__}", border_style="magenta"))

    if source_b is None and by is None:
        raise typer.BadParameter("pass either a second source or --by COLUMN")

    adapter_a = adapter_for(source_a, split=split_a)
    console.print(f"[dim]A:[/] {type(adapter_a).__name__}  [dim]→[/] {adapter_a.source}")
    with console.status("loading A", spinner="dots"):
        df_a = adapter_a.load_dataframe()
    console.print(f"[dim]A loaded:[/] {df_a.height:,} rows × {df_a.width} cols")

    if source_b is None:
        # split the single source by --by COL
        wanted = [v.strip() for v in values.split(",")] if values else None
        partitions = split_dataframe(df_a, by, wanted)
        if len(partitions) < 2:
            raise typer.BadParameter(
                f"column {by!r} needs at least two distinct values to compare"
            )
        (la, df_left), (lb, df_right) = partitions[0], partitions[1]
        if label_a is None:
            label_a = str(la)
        if label_b is None:
            label_b = str(lb)
        df_a, df_b = df_left, df_right
        source_b_resolved = f"{adapter_a.source}[{by}={lb}]"
        source_a_resolved = f"{adapter_a.source}[{by}={la}]"
    else:
        adapter_b = adapter_for(source_b, split=split_b)
        console.print(f"[dim]B:[/] {type(adapter_b).__name__}  [dim]→[/] {adapter_b.source}")
        with console.status("loading B", spinner="dots"):
            df_b = adapter_b.load_dataframe()
        console.print(f"[dim]B loaded:[/] {df_b.height:,} rows × {df_b.width} cols")
        source_a_resolved = adapter_a.source
        source_b_resolved = adapter_b.source
        if label_a is None:
            label_a = "A"
        if label_b is None:
            label_b = "B"

    with console.status("profiling + diffing columns", spinner="dots"):
        report = compare_dataframes(
            df_a,
            df_b,
            label_a=label_a,
            label_b=label_b,
            source_a=source_a_resolved,
            source_b=source_b_resolved,
            seed=seed,
        )

    _print_compare_summary(report)

    _maybe_run_compare_insights(report, llm_spec)

    out_path = render_compare_html(report, out)
    findings_path = write_compare_findings(report, findings)
    console.print(f"[green]✓[/] HTML report: [bold]{out_path}[/]")
    console.print(f"[green]✓[/] JSON findings: [bold]{findings_path}[/]")

    if open_browser:
        import webbrowser

        webbrowser.open(out_path.as_uri())


def _print_compare_summary(report) -> None:
    table = Table(
        title=f"{report.a.label} ({report.a.row_count:,}) vs {report.b.label} ({report.b.row_count:,})",
        show_lines=False,
    )
    table.add_column("column", style="bold")
    table.add_column("kind", style="cyan")
    table.add_column(report.a.label, justify="right", style="blue")
    table.add_column(report.b.label, justify="right", style="magenta")
    table.add_column("notable Δ", justify="left")

    for c in report.columns:
        a_label = f"{c.a.n_unique:,}u" if c.a and c.a.n_unique is not None else "-"
        b_label = f"{c.b.n_unique:,}u" if c.b and c.b.n_unique is not None else "-"
        notable: list[str] = []
        if c.delta.get("null_rate_delta"):
            d = c.delta["null_rate_delta"]
            if abs(d) > 0.05:
                notable.append(f"null {d:+.1%}")
        if c.delta.get("len_mean_delta"):
            d = c.delta["len_mean_delta"]
            if abs(d) > 10:
                notable.append(f"len_mean {d:+.0f}")
        if c.delta.get("mean_delta"):
            d = c.delta["mean_delta"]
            if abs(d) > 0.1 * abs(c.delta.get("mean_a") or 1):
                notable.append(f"mean {d:+.2f}")
        if c.delta.get("language_jaccard") is not None and c.delta["language_jaccard"] < 0.7:
            notable.append(f"lang-jaccard {c.delta['language_jaccard']:.2f}")
        if c.delta.get("top_value_jaccard") is not None and c.delta["top_value_jaccard"] < 0.5:
            notable.append(f"top-val-jaccard {c.delta['top_value_jaccard']:.2f}")
        table.add_row(c.column, c.kind, a_label, b_label, ", ".join(notable) or "[dim]—[/]")
    console.print(table)


@app.command(
    name="serve",
    help="Start the live viewer (requires pip install 'saturn-dissect[web]').",
)
def serve(
    directory: Path = typer.Option(
        Path.cwd(), "--dir", help="directory containing findings JSON files"
    ),
    port: int = typer.Option(VIEWER_DEFAULT_PORT, "--port"),
    host: str = typer.Option("127.0.0.1", "--host"),
    debug: bool = typer.Option(False, "--debug"),
) -> None:
    if create_app is None:
        console.print("[red]viewer not installed. run: pip install 'saturn-dissect[web]'[/]")
        raise typer.Exit(code=1)
    if not directory.is_dir():
        console.print(f"[red]not a directory:[/] {directory}")
        raise typer.Exit(code=2)
    flask_app = create_app(findings_dir=directory)
    console.print(
        f"[green]saturn viewer[/] on [bold]http://{host}:{port}[/] "
        f"serving [dim]{directory}[/]"
    )
    flask_app.run(host=host, port=port, debug=debug)


@app.command(name="version")
def version() -> None:
    console.print(f"saturn {__version__}")


if __name__ == "__main__":
    app()
