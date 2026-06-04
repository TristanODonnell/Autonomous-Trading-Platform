from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from autonomous_trading_platform.cli.commands import portfolio as portfolio_cmd
from autonomous_trading_platform.cli.main import build_parser
from autonomous_trading_platform.governance.models.governance_state import GovernanceState


class TestPortfolioParserCoverage:
    def test_core_read_commands_registered(self) -> None:
        parser = build_parser()

        args = parser.parse_args(["portfolio", "snapshot", "--json"])
        assert args.func is portfolio_cmd.handle_snapshot

        args = parser.parse_args(["portfolio", "summary", "--json"])
        assert args.func is portfolio_cmd.handle_summary

        args = parser.parse_args(["portfolio", "holdings", "--json"])
        assert args.func is portfolio_cmd.handle_holdings

        args = parser.parse_args(["portfolio", "allocation", "--json"])
        assert args.func is portfolio_cmd.handle_allocation

        args = parser.parse_args(["portfolio", "equity-curve", "--period", "1m", "--json"])
        assert args.func is portfolio_cmd.handle_equity_curve
        assert args.period == "1m"

        args = parser.parse_args(
            [
                "portfolio",
                "performance",
                "--from-date",
                "2026-01-01",
                "--to-date",
                "2026-05-01",
                "--json",
            ]
        )
        assert args.func is portfolio_cmd.handle_performance

        args = parser.parse_args(["portfolio", "performance-by-period", "--json"])
        assert args.func is portfolio_cmd.handle_performance_by_period

        args = parser.parse_args(["portfolio", "risk", "--json"])
        assert args.func is portfolio_cmd.handle_risk

    def test_allocation_commands_registered(self) -> None:
        parser = build_parser()

        args = parser.parse_args(
            [
                "portfolio",
                "allocation",
                "resolve",
                "--strategy-id",
                "momentum_v1",
                "--state",
                "approved_live",
                "--performance-tier",
                "top",
                "--json",
            ]
        )
        assert args.func is portfolio_cmd.handle_allocation_resolve
        assert args.state is GovernanceState.APPROVED_LIVE

        args = parser.parse_args(["portfolio", "allocation", "resolve-many", "--json"])
        assert args.func is portfolio_cmd.handle_allocation_resolve_many

        args = parser.parse_args(
            ["portfolio", "allocation", "aggregate", "--proposed", "momentum_v1=25"]
        )
        assert args.func is portfolio_cmd.handle_allocation_aggregate
        assert args.proposed == ["momentum_v1=25"]

    def test_allocation_config_and_simulation_commands_registered(self) -> None:
        parser = build_parser()

        args = parser.parse_args(["portfolio", "allocation-config", "show", "--json"])
        assert args.func is portfolio_cmd.handle_allocation_config_show

        args = parser.parse_args(
            [
                "portfolio",
                "allocation-config",
                "snapshot",
                "--output",
                "artifacts/portfolio/allocation-config.json",
            ]
        )
        assert args.func is portfolio_cmd.handle_allocation_config_snapshot
        assert args.output == Path("artifacts/portfolio/allocation-config.json")

        args = parser.parse_args(
            [
                "portfolio",
                "allocation-config",
                "verify",
                "--config",
                "artifacts/portfolio/allocation-config.json",
                "--strategy-id",
                "momentum_v1",
                "--state",
                "approved_live",
            ]
        )
        assert args.func is portfolio_cmd.handle_allocation_config_verify
        assert args.config == Path("artifacts/portfolio/allocation-config.json")

        args = parser.parse_args(
            [
                "portfolio",
                "simulate-allocation",
                "--config",
                "artifacts/portfolio/allocation-config.json",
                "--strategy-id",
                "momentum_v1",
                "--state",
                "approved_live",
                "--capital",
                "100000",
            ]
        )
        assert args.func is portfolio_cmd.handle_simulate_allocation
        assert args.capital == 100000.0

    def test_cash_positions_reconcile_and_export_registered(self) -> None:
        parser = build_parser()

        args = parser.parse_args(["portfolio", "cash", "latest", "--json"])
        assert args.func is portfolio_cmd.handle_cash_latest

        args = parser.parse_args(["portfolio", "positions", "latest", "--json"])
        assert args.func is portfolio_cmd.handle_positions_latest

        args = parser.parse_args(["portfolio", "reconcile", "--json"])
        assert args.func is portfolio_cmd.handle_reconcile

        args = parser.parse_args(
            ["portfolio", "export", "--output", "artifacts/portfolio/current.json"]
        )
        assert args.func is portfolio_cmd.handle_export
        assert args.output == Path("artifacts/portfolio/current.json")

        args = parser.parse_args(
            [
                "portfolio",
                "verify-dashboard-state",
                "--run-id",
                "11111111-1111-4111-8111-111111111111",
                "--json",
            ]
        )
        assert args.func is portfolio_cmd.handle_verify_dashboard_state

    def test_construction_commands_registered(self) -> None:
        parser = build_parser()
        batch_id = "11111111-1111-4111-8111-111111111111"

        args = parser.parse_args(["portfolio", "construction", "runs", "--limit", "20", "--json"])
        assert args.func is portfolio_cmd.handle_construction_runs
        assert args.limit == 20

        args = parser.parse_args(["portfolio", "construction", "show", "--batch-id", batch_id])
        assert args.func is portfolio_cmd.handle_construction_show

        args = parser.parse_args(["portfolio", "construction", "by-run-id", "--run-id", batch_id])
        assert args.func is portfolio_cmd.handle_construction_by_run_id

        args = parser.parse_args(
            ["portfolio", "construction", "raw-signals", "--batch-id", batch_id]
        )
        assert args.func is portfolio_cmd.handle_construction_raw_signals

        args = parser.parse_args(["portfolio", "construction", "netted", "--batch-id", batch_id])
        assert args.func is portfolio_cmd.handle_construction_netted

        args = parser.parse_args(
            [
                "portfolio",
                "construction",
                "intents",
                "--batch-id",
                batch_id,
                "--constraint-status",
                "rejected",
            ]
        )
        assert args.func is portfolio_cmd.handle_construction_intents
        assert args.constraint_status == "rejected"

        args = parser.parse_args(["portfolio", "construction", "conflicts", "--batch-id", batch_id])
        assert args.func is portfolio_cmd.handle_construction_conflicts

        args = parser.parse_args(["portfolio", "construction", "verify", "--batch-id", batch_id])
        assert args.func is portfolio_cmd.handle_construction_verify

    def test_factor_commands_registered(self) -> None:
        parser = build_parser()

        args = parser.parse_args(
            [
                "portfolio",
                "factor-exposures",
                "current",
                "--portfolio-id",
                "paper",
                "--lookback-window",
                "20",
            ]
        )
        assert args.func is portfolio_cmd.handle_factor_exposures_current
        assert args.lookback_window == 20

        args = parser.parse_args(
            ["portfolio", "factor-neutralization", "current", "--portfolio-id", "paper"]
        )
        assert args.func is portfolio_cmd.handle_factor_neutralization_current


class TestPortfolioHelpers:
    def test_parse_proposed_overrides(self) -> None:
        proposed = portfolio_cmd._parse_proposed_overrides(["momentum_v1=25", "mr_v1=0.5"])

        assert proposed == {"momentum_v1": Decimal("25"), "mr_v1": Decimal("0.5")}

    def test_positive_int_validation(self) -> None:
        assert portfolio_cmd._positive_int("1") == 1

    def test_governance_state_parser(self) -> None:
        assert (
            portfolio_cmd._parse_governance_state("approved_live") is GovernanceState.APPROVED_LIVE
        )
