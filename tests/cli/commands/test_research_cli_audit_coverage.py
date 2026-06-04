from __future__ import annotations

from autonomous_trading_platform.cli.commands import research as research_cmd
from autonomous_trading_platform.cli.main import build_parser


class TestResearchAuditParserCoverage:
    def test_experiment_catalog_commands_registered(self) -> None:
        parser = build_parser()

        args = parser.parse_args(["research", "list-experiments"])
        assert args.func is research_cmd.handle_list_experiments

        args = parser.parse_args(["research", "inspect-experiment", "--experiment-id", "exp-1"])
        assert args.func is research_cmd.handle_inspect_experiment

        args = parser.parse_args(
            [
                "research",
                "cancel-experiment",
                "--experiment-id",
                "exp-1",
                "--reason",
                "operator request",
                "--dry-run",
            ]
        )
        assert args.func is research_cmd.handle_cancel_experiment
        assert args.dry_run is True

        args = parser.parse_args(
            ["research", "list-experiment-strategies", "--experiment-id", "exp-1"]
        )
        assert args.func is research_cmd.handle_list_experiment_strategies

    def test_plan_and_validation_commands_registered(self) -> None:
        parser = build_parser()

        args = parser.parse_args(["research", "validate-config", "--config", "experiment.yaml"])
        assert args.func is research_cmd.handle_validate_config

        args = parser.parse_args(["research", "plan-experiment", "--config", "experiment.yaml"])
        assert args.func is research_cmd.handle_plan_experiment

        args = parser.parse_args(
            ["research", "run-experiment", "--config", "experiment.yaml", "--dry-run"]
        )
        assert args.func is research_cmd.handle_run_experiment
        assert args.dry_run is True

        args = parser.parse_args(
            [
                "research",
                "run-simulation",
                "--dataset-version-id",
                "bars-v1",
                "--price-basis",
                "raw",
                "--symbols",
                "AAPL",
                "--start-date",
                "2026-01-01",
                "--end-date",
                "2026-01-31",
                "--strategy-type",
                "momentum",
                "--random-seed",
                "42",
                "--strategy-id",
                "strat-1",
                "--dry-run",
            ]
        )
        assert args.func is research_cmd.handle_run_simulation
        assert args.dry_run is True

    def test_run_inspection_commands_registered(self) -> None:
        parser = build_parser()

        args = parser.parse_args(["research", "list-runs", "--experiment-id", "exp-1"])
        assert args.func is research_cmd.handle_list_runs

        args = parser.parse_args(["research", "inspect-run", "--run-id", "run-1"])
        assert args.func is research_cmd.handle_inspect_run

    def test_cache_and_dependency_commands_registered(self) -> None:
        parser = build_parser()

        args = parser.parse_args(
            [
                "research",
                "resolve-feature-dependencies",
                "--strategy-type",
                "momentum",
                "--symbols",
                "AAPL,MSFT",
                "--dataset-version-id",
                "bars-v1",
                "--price-basis",
                "adjusted",
                "--start-date",
                "2026-01-01",
                "--end-date",
                "2026-03-31",
            ]
        )
        assert args.func is research_cmd.handle_resolve_feature_dependencies

        args = parser.parse_args(["research", "inspect-cache", "--cache", "strategy-generation"])
        assert args.func is research_cmd.handle_inspect_cache

        args = parser.parse_args(["research", "validate-cache", "--cache", "simulation-results"])
        assert args.func is research_cmd.handle_validate_cache

        args = parser.parse_args(
            ["research", "clear-cache", "--cache", "simulation-results", "--execute"]
        )
        assert args.func is research_cmd.handle_clear_cache
        assert args.execute is True

    def test_analysis_intelligence_and_calibration_commands_registered(self) -> None:
        parser = build_parser()

        args = parser.parse_args(
            ["research", "run-validation", "--type", "walk-forward", "--config", "validation.yaml"]
        )
        assert args.func is research_cmd.handle_run_validation

        args = parser.parse_args(["research", "analyze-regimes", "--experiment-id", "exp-1"])
        assert args.func is research_cmd.handle_analyze_regimes

        args = parser.parse_args(
            ["research", "intelligence", "rank-candidates", "--experiment-id", "exp-1"]
        )
        assert args.func is research_cmd.handle_intelligence_rank_candidates

        args = parser.parse_args(
            ["research", "intelligence", "cluster-strategies", "--experiment-id", "exp-1"]
        )
        assert args.func is research_cmd.handle_intelligence_cluster_strategies

        args = parser.parse_args(
            ["research", "intelligence", "predict-robustness", "--experiment-id", "exp-1"]
        )
        assert args.func is research_cmd.handle_intelligence_predict_robustness

        args = parser.parse_args(
            [
                "research",
                "calibrate-slippage",
                "--fills-source",
                "fills.json",
                "--output",
                "calibration.json",
            ]
        )
        assert args.func is research_cmd.handle_calibrate_slippage

        args = parser.parse_args(["research", "inspect-cost-model"])
        assert args.func is research_cmd.handle_inspect_cost_model


class TestResearchAuditParserValidation:
    def test_positive_max_workers(self) -> None:
        parser = build_parser()
        try:
            parser.parse_args(["research", "run-experiment", "--max-workers", "0"])
        except SystemExit as exc:
            assert exc.code == 2
        else:
            raise AssertionError("expected argparse validation failure")

    def test_mutation_rate_is_percentage(self) -> None:
        parser = build_parser()
        try:
            parser.parse_args(["research", "generate-strategies", "--mutation-rate", "1.5"])
        except SystemExit as exc:
            assert exc.code == 2
        else:
            raise AssertionError("expected argparse validation failure")
