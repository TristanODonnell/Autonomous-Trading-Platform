"""``atp research signal-screen``: its options, ``--describe``, and the exit codes."""

from __future__ import annotations

import json

import pytest

from autonomous_trading_platform.cli.commands import research as research_cmd
from autonomous_trading_platform.cli.main import build_parser
from autonomous_trading_platform.research.signal_screen.libraries import V1

_BASE = ["research", "signal-screen"]
_RUN = [*_BASE, "--dataset-version", "raw_bars_x", "--universe-timeline", "universe_timeline_y"]


def test_the_command_is_registered_with_its_defaults():
    args = build_parser().parse_args(_BASE)
    assert args.func is research_cmd.handle_signal_screen
    assert (args.library, args.describe, args.format) == ("v1", False, "text")
    assert args.dataset_version is None and args.universe_timeline is None
    assert args.scale is None and args.shuffles is None and args.seed is None

    args = build_parser().parse_args(
        [*_RUN, "--scale", "daily", "--shuffles", "200", "--seed", "5", "--output", "out.json"]
    )
    assert (args.scale, args.shuffles, args.seed, args.output) == (["daily"], 200, 5, "out.json")
    with pytest.raises(SystemExit):
        build_parser().parse_args([*_BASE, "--scale", "hourly"])


def test_describe_prints_the_library_and_reads_nothing(monkeypatch, capsys):
    def no_database():
        raise AssertionError("--describe must not open a database session")

    monkeypatch.setattr(research_cmd, "get_session", no_database)
    assert research_cmd.handle_signal_screen(build_parser().parse_args([*_BASE, "--describe"])) == 0
    text = capsys.readouterr().out
    assert text.splitlines()[0] == "Library v1: 80 rows"
    assert "5m.z20_low_15" in text and f"hash {V1.content_hash()}" in text

    args = build_parser().parse_args([*_BASE, "--describe", "--format", "json"])
    assert research_cmd.handle_signal_screen(args) == 0
    described = json.loads(capsys.readouterr().out)
    assert described["hash"] == V1.content_hash() and len(described["rows"]) == 80
    assert described["rows"][0]["condition"] == "rate_of_change(lookback=10) > 0"


def test_a_run_needs_a_dataset_and_a_timeline(monkeypatch, capsys):
    monkeypatch.setattr(research_cmd, "get_session", lambda: pytest.fail("no session expected"))
    assert research_cmd.handle_signal_screen(build_parser().parse_args(_BASE)) == 2
    assert "--dataset-version and --universe-timeline are needed" in capsys.readouterr().out
    args = build_parser().parse_args([*_BASE, "--library", "v9", "--describe"])
    assert research_cmd.handle_signal_screen(args) == 2
    assert "no library named 'v9'" in capsys.readouterr().out


class _Session:
    closed = False

    def close(self) -> None:
        self.closed = True


def test_a_run_hands_the_service_its_options_and_writes_the_result(monkeypatch, capsys, tmp_path):
    from autonomous_trading_platform.application.services import signal_screen_service as service

    session = _Session()
    seen: dict = {}
    report = {"library": {"name": "v1"}, "marker": 1}

    def source(given, *, dataset_version, universe_timeline):
        seen["source"] = (given, dataset_version, universe_timeline)
        return "the source"

    def run(given, *, library, scales, settings, progress):
        seen["run"] = (given, library, scales, settings.shuffles, settings.seed)
        progress("bars AAA")
        return report

    monkeypatch.setattr(research_cmd, "get_session", lambda: session)
    monkeypatch.setattr(service, "signal_screen_source", source)
    monkeypatch.setattr(service, "run_signal_screen", run)
    output = tmp_path / "screen" / "result.json"
    args = build_parser().parse_args(
        [
            *_RUN,
            "--scale",
            "daily",
            "--shuffles",
            "200",
            "--format",
            "json",
            "--output",
            str(output),
        ]
    )
    assert research_cmd.handle_signal_screen(args) == 0
    assert seen["source"] == (session, "raw_bars_x", "universe_timeline_y")
    # The seed stays the declared one when only the shuffles are changed.
    assert seen["run"] == ("the source", V1, ("daily",), 200, 20261127)
    assert session.closed
    captured = capsys.readouterr()
    assert json.loads(captured.out) == report and "bars AAA" in captured.err
    assert json.loads(output.read_text(encoding="utf-8")) == report

    # Without --scale or --shuffles: both scales, the declared settings.
    assert (
        research_cmd.handle_signal_screen(build_parser().parse_args([*_RUN, "--format", "json"]))
        == 0
    )
    assert seen["run"][2:] == (("5min", "daily"), 10_000, 20261127)


def test_a_run_that_cannot_be_loaded_says_why_and_exits_one(monkeypatch, capsys):
    from autonomous_trading_platform.application.services import signal_screen_service as service

    session = _Session()

    def source(given, *, dataset_version, universe_timeline):
        raise service.SignalScreenError("dataset version raw_bars_x is not registered")

    monkeypatch.setattr(research_cmd, "get_session", lambda: session)
    monkeypatch.setattr(service, "signal_screen_source", source)
    assert research_cmd.handle_signal_screen(build_parser().parse_args(_RUN)) == 1
    assert "signal screen: dataset version raw_bars_x is not registered" in capsys.readouterr().out
    assert session.closed
