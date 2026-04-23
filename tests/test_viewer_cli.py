"""Tests for the `saturn serve` CLI command."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from saturn.cli import app


def test_serve_command_help_lists_port_and_dir():
    runner = CliRunner()
    result = runner.invoke(app, ["serve", "--help"])
    assert result.exit_code == 0
    assert "--port" in result.output
    assert "--dir" in result.output


def test_serve_command_invokes_flask_run(tmp_path):
    runner = CliRunner()
    fake_app = MagicMock()
    fake_app.run.return_value = None
    with patch("saturn.cli.create_app", return_value=fake_app) as mock_create:
        result = runner.invoke(
            app, ["serve", "--dir", str(tmp_path), "--port", "5043"]
        )
    assert result.exit_code == 0, result.output
    mock_create.assert_called_once()
    fake_app.run.assert_called_once()
    kwargs = fake_app.run.call_args.kwargs
    assert kwargs["port"] == 5043
    assert kwargs["host"] == "127.0.0.1"
    assert kwargs["debug"] is False


def test_serve_command_rejects_missing_dir():
    runner = CliRunner()
    result = runner.invoke(app, ["serve", "--dir", "/nonexistent/saturn-xyz-12345"])
    assert result.exit_code != 0
    assert "not a directory" in result.output.lower()


def test_serve_command_honours_debug_flag(tmp_path):
    runner = CliRunner()
    fake_app = MagicMock()
    with patch("saturn.cli.create_app", return_value=fake_app):
        runner.invoke(app, ["serve", "--dir", str(tmp_path), "--debug"])
    assert fake_app.run.call_args.kwargs["debug"] is True
