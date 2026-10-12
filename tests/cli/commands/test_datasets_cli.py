from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from autonomous_trading_platform.cli.commands import datasets as datasets_cmd
from autonomous_trading_platform.cli.main import build_parser


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
