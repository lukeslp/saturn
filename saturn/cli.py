"""Saturn CLI entry point."""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from . import __version__
from .ingestion import adapter_for, reservoir_sample
from .profilers import profile_columns
from .report import assemble, render_html, write_findings

app = typer.Typer(
    name="saturn",
    help="Dataset dissector. Stats pass is free and deterministic; language-model insight is opt-in.",
    add_completion=False,
    no_args_is_help=True,
)
console = Console()


def _run(
    source: str,
    *,
    split: str,
    config: str | None,
    sample_size: int,
    seed: int,
    out: Path,
    findings: Path,
    open_browser: bool,
) -> None:
    console.print(Panel(f"[bold]saturn[/bold] v{__version__}  —  {source}", border_style="blue"))

    adapter = adapter_for(source, split=split, config=config)
    console.print(f"[dim]adapter:[/] {type(adapter).__name__}  [dim]→[/] {adapter.source}")

    with console.status("scanning schema", spinner="dots"):
        schema = adapter.schema()

    console.print(f"[dim]schema:[/] {len(schema.columns)} columns")

    row_count = adapter.row_count()
    if row_count is not None:
        console.print(f"[dim]rows:[/] {row_count:,}")

    with console.status(f"sampling (n={sample_size}, seed={seed})", spinner="dots"):
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
        sample=sample,
        seed=seed,
        schema=schema.columns,
        results=results,
    )

    _print_summary(schema.columns, results, effective_count)

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


@app.command(name="analyze", help="Analyse a dataset (HuggingFace repo id or local file).")
def analyze(
    source: str = typer.Argument(..., help="HF repo id (user/dataset) or local file path"),
    sample_size: int = typer.Option(2000, "--sample", help="reservoir sample size"),
    seed: int = typer.Option(42, "--seed", help="random seed for reproducible sampling"),
    out: Path = typer.Option(Path("saturn_report.html"), "--out"),
    findings: Path = typer.Option(Path("saturn_findings.json"), "--findings"),
    split: str = typer.Option("train", "--split", help="HF dataset split"),
    config: str | None = typer.Option(None, "--config", help="HF dataset config name"),
    open_browser: bool = typer.Option(False, "--open", help="open report in browser after run"),
) -> None:
    _run(
        source,
        split=split,
        config=config,
        sample_size=sample_size,
        seed=seed,
        out=out,
        findings=findings,
        open_browser=open_browser,
    )


@app.command(name="huggingface", help="Convenience: analyse a HuggingFace dataset.")
def huggingface(
    repo: str = typer.Argument(..., help="HuggingFace repo id (user/dataset)"),
    sample_size: int = typer.Option(2000, "--sample"),
    seed: int = typer.Option(42, "--seed"),
    out: Path = typer.Option(Path("saturn_report.html"), "--out"),
    findings: Path = typer.Option(Path("saturn_findings.json"), "--findings"),
    split: str = typer.Option("train", "--split"),
    config: str | None = typer.Option(None, "--config"),
    open_browser: bool = typer.Option(False, "--open"),
) -> None:
    _run(
        f"hf://{repo}",
        split=split,
        config=config,
        sample_size=sample_size,
        seed=seed,
        out=out,
        findings=findings,
        open_browser=open_browser,
    )


@app.command(name="version")
def version() -> None:
    console.print(f"saturn {__version__}")


if __name__ == "__main__":
    app()
