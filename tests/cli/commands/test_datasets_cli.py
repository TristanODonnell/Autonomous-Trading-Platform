from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest

from autonomous_trading_platform.application.services import (
    research_window_dataset_service as window_service,
)
from autonomous_trading_platform.cli.commands import datasets as datasets_cmd
from autonomous_trading_platform.cli.main import build_parser
from autonomous_trading_platform.contracts.runtime.research_window_dataset import (
    PriceJump,
    ResearchWindowDatasetResult,
)


class TestDatasetsParserCoverage:
    def test_publish_registered(self) -> None:
        parser = build_parser()

        args = parser.parse_args(["datasets", "publish"])
        assert args.func is datasets_cmd.handle_publish
        assert args.trading_date is None
        assert args.data_root is None

        args = parser.parse_args(
            ["datasets", "publish", "--trading-date", "2026-10-12", "--data-root", "data"]
        )
        assert args.trading_date == date(2026, 10, 12)
        assert args.data_root == Path("data")

    def test_pull_registered_with_pinning_options(self) -> None:
        parser = build_parser()

        args = parser.parse_args(["datasets", "pull"])
        assert args.func is datasets_cmd.handle_pull
        assert args.as_of is None
        assert args.versions is None
        assert args.no_register is False

        args = parser.parse_args(["datasets", "pull", "--as-of", "2026-10-12", "--no-register"])
        assert args.as_of == date(2026, 10, 12)
        assert args.no_register is True

        args = parser.parse_args(
            ["datasets", "pull", "--version", "raw_bars_v1", "--version", "returns_v1"]
        )
        assert args.versions == ["raw_bars_v1", "returns_v1"]

        with pytest.raises(SystemExit):
            parser.parse_args(
                ["datasets", "pull", "--as-of", "2026-10-12", "--version", "raw_bars_v1"]
            )

    def test_list_remote_registered(self) -> None:
        parser = build_parser()

        args = parser.parse_args(["datasets", "list-remote"])
        assert args.func is datasets_cmd.handle_list_remote
        assert args.as_of is None

        args = parser.parse_args(["datasets", "list-remote", "--as-of", "2026-10-12"])
        assert args.as_of == date(2026, 10, 12)


def test_store_requires_bucket(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    monkeypatch.delenv("DATASET_S3_BUCKET", raising=False)
    from autonomous_trading_platform.config.settings import Settings

    with pytest.raises(RuntimeError, match="DATASET_S3_BUCKET"):
        datasets_cmd._store(Settings())


class TestBuildWindowCli:
    """``atp datasets build-window``: options, the symbols file, and the exit codes."""

    _BASE = ["datasets", "build-window", "--start", "2024-10-01", "--end", "2026-11-16"]
    # What the stand-in service returns, or raises when it is an exception.
    result: ResearchWindowDatasetResult | Exception

    @pytest.fixture()
    def built(self, monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
        """Replaces the service: records each call and returns ``self.result``."""
        calls: list[dict[str, Any]] = []
        test = self

        class _Service:
            def __init__(self, session: Any, *, base_path: Any = None) -> None:
                pass

            def build_window(self, **kwargs: Any) -> ResearchWindowDatasetResult:
                calls.append(kwargs)
                if isinstance(test.result, Exception):
                    raise test.result
                return test.result

        @contextmanager
        def _scope() -> Iterator[object]:
            yield object()

        monkeypatch.setattr(window_service, "ResearchWindowDatasetService", _Service)
        monkeypatch.setattr(datasets_cmd, "session_scope", _scope)
        return calls

    def _result(self, status: str, **fields: Any) -> ResearchWindowDatasetResult:
        return ResearchWindowDatasetResult(
            status=status,  # type: ignore[arg-type]
            start=date(2024, 10, 1),
            end=date(2026, 11, 16),
            symbols=["AAPL", "MSFT"],
            **fields,
        )

    def test_registered_and_needs_a_mode_and_names(self) -> None:
        parser = build_parser()

        args = parser.parse_args([*self._BASE, "--symbols", "AAPL,MSFT", "--plan"])
        assert args.func is datasets_cmd.handle_build_window
        assert (args.start, args.end) == (date(2024, 10, 1), date(2026, 11, 16))
        assert args.plan is True and args.enforce is False

        for incomplete in (
            [*self._BASE, "--symbols", "AAPL"],
            [*self._BASE, "--enforce"],
            [*self._BASE, "--symbols", "AAPL", "--plan", "--enforce"],
            [*self._BASE, "--symbols", "AAPL", "--symbols-file", "names.txt", "--plan"],
        ):
            with pytest.raises(SystemExit):
                parser.parse_args(incomplete)

    def test_names_come_from_a_file_and_a_built_dataset_exits_zero(
        self, built: list[dict[str, Any]], tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        names = tmp_path / "names.txt"
        names.write_text("AAPL,MSFT\nNVDA\n", encoding="utf-8")
        self.result = self._result("created", dataset_version_id="raw_bars_x", rows=10)

        args = build_parser().parse_args([*self._BASE, "--symbols-file", str(names), "--enforce"])
        code = args.func(args)

        assert code == 0
        assert [n.strip() for n in built[0]["symbols"]] == ["AAPL", "MSFT", "NVDA"]
        assert built[0]["plan_only"] is False
        assert (built[0]["start"], built[0]["end"]) == (date(2024, 10, 1), date(2026, 11, 16))
        assert '"dataset_version_id": "raw_bars_x"' in capsys.readouterr().out

    def test_a_refused_dataset_exits_one_and_says_why(
        self, built: list[dict[str, Any]], capsys: pytest.CaptureFixture[str]
    ) -> None:
        self.result = self._result("refused", errors=["1 session(s) have no bars for any symbol"])

        args = build_parser().parse_args([*self._BASE, "--symbols", "AAPL,MSFT", "--enforce"])
        code = args.func(args)

        assert code == 1
        printed = capsys.readouterr()
        assert "Window dataset not built: 1 session(s) have no bars" in printed.out + printed.err

    def test_a_bad_request_exits_one(
        self, built: list[dict[str, Any]], capsys: pytest.CaptureFixture[str]
    ) -> None:
        self.result = ValueError("--end 2024-01-01 is before --start 2024-10-01")

        args = build_parser().parse_args([*self._BASE, "--symbols", "AAPL", "--plan"])
        code = args.func(args)

        assert code == 1
        assert built[0]["plan_only"] is True
        printed = capsys.readouterr()
        assert "Window dataset not built: --end 2024-01-01" in printed.out + printed.err

    def test_a_long_list_of_price_jumps_is_cut_and_counted(
        self, built: list[dict[str, Any]], capsys: pytest.CaptureFixture[str]
    ) -> None:
        jump = PriceJump(
            symbol="AAPL",
            timestamp=datetime(2025, 1, 2, 14, 30, tzinfo=UTC),
            previous_close=100.0,
            close=10.0,
            change=-0.9,
        )
        self.result = self._result("created", price_jumps=[jump] * 60)

        args = build_parser().parse_args([*self._BASE, "--symbols", "AAPL", "--enforce"])
        code = args.func(args)

        out = capsys.readouterr().out
        assert code == 0
        assert '"price_jump_count": 60' in out
        assert out.count('"previous_close"') == 50
