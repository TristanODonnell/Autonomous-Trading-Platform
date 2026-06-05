from __future__ import annotations

from types import SimpleNamespace

from sqlalchemy.orm import Session

from autonomous_trading_platform.cli.commands import backtesting
from autonomous_trading_platform.cli.main import build_parser
from autonomous_trading_platform.storage.sor.models.dataset_versions import DatasetVersions
from autonomous_trading_platform.storage.sor.models.metrics_summary import MetricsSummary
from autonomous_trading_platform.storage.sor.models.simulation_runs import SimulationRuns


def test_verify_governance_allocation_command_is_registered() -> None:
    parser = build_parser()

    args = parser.parse_args(
        [
            "backtesting",
            "verify-governance-allocation",
            "--controls",
            "fixtures/controls.yaml",
            "--settings",
            "fixtures/settings.yaml",
        ]
    )

    assert args.func is backtesting.handle_verify_governance_allocation
    assert args.controls.as_posix() == "fixtures/controls.yaml"
    assert args.settings.as_posix() == "fixtures/settings.yaml"


def test_verify_auto_promotion_command_is_registered() -> None:
    parser = build_parser()

    args = parser.parse_args(
        [
            "backtesting",
            "verify-auto-promotion",
            "--settings",
            "fixtures/settings.yaml",
        ]
    )

    assert args.func is backtesting.handle_verify_auto_promotion
    assert args.settings.as_posix() == "fixtures/settings.yaml"


def test_verify_auto_demotion_command_is_registered() -> None:
    parser = build_parser()

    args = parser.parse_args(
        [
            "backtesting",
            "verify-auto-demotion",
            "--settings",
            "fixtures/settings.yaml",
        ]
    )

    assert args.func is backtesting.handle_verify_auto_demotion
    assert args.settings.as_posix() == "fixtures/settings.yaml"


def test_governance_audit_metric_probes_straddle_rule_thresholds() -> None:
    rule = SimpleNamespace(
        min_sharpe=1.5,
        max_drawdown=0.15,
        min_days_tested=30,
        min_trade_count=10,
        min_cagr=None,
        min_win_rate=None,
    )

    passing = backtesting._governance_audit_metrics_for_rule(rule, should_pass=True)
    failing = backtesting._governance_audit_metrics_for_rule(rule, should_pass=False)

    assert passing["sharpe"] > rule.min_sharpe
    assert passing["max_drawdown"] < rule.max_drawdown
    assert passing["days_tested"] > rule.min_days_tested
    assert passing["trade_count"] > rule.min_trade_count

    assert failing["sharpe"] < rule.min_sharpe
    assert failing["max_drawdown"] > rule.max_drawdown
    assert failing["days_tested"] < rule.min_days_tested
    assert failing["trade_count"] < rule.min_trade_count


def test_governance_audit_reports_source_of_truth_split() -> None:
    assert backtesting._governance_audit_source_of_truth_payload() == {
        "automation_controls_source": "settings",
        "promotion_thresholds_source": "promotion_rules",
        "allocation_targets_source": ("capital_allocation_policies + allocation_overrides"),
    }


def test_governance_audit_marks_legacy_threshold_settings_ignored() -> None:
    settings = {
        "min_sharpe_for_promotion": 9.9,
        "min_paper_trading_period_days": 365,
    }

    ignored = backtesting._governance_audit_deprecated_or_ignored_settings(settings)

    assert ignored == [
        {
            "setting": "min_sharpe_for_promotion",
            "value": 9.9,
            "classification": "deprecated/persisted-only",
            "active_source": "promotion_rules.min_sharpe",
            "ignored_for": "manual_promotion_eligibility",
        },
        {
            "setting": "min_paper_trading_period_days",
            "value": 365,
            "classification": "deprecated/persisted-only",
            "active_source": "promotion_rules.min_days_tested",
            "ignored_for": "manual_promotion_eligibility",
        },
    ]


def test_governance_audit_seeds_promotion_rules_from_audit_defaults_not_settings(
    db_session: Session,
) -> None:
    settings_row = SimpleNamespace(
        min_sharpe_for_promotion=9.9,
        max_strategy_drawdown=0.99,
        min_paper_trading_period_days=365,
    )

    result = backtesting._governance_audit_seed_promotion_rules(
        session=db_session,
        settings_row=settings_row,
    )

    research_rule = result["rules"]["approved_research_to_approved_paper"]
    assert research_rule.min_sharpe == 1.5
    assert research_rule.max_drawdown == 0.15
    assert research_rule.min_days_tested == 30
    assert result["summary"][0]["threshold_source"] == "promotion_rules"
    assert result["summary"][0]["seed_threshold_source"] == "audit_defaults"


def test_auto_promotion_verification_uses_constraint_safe_legacy_settings() -> None:
    source_values = backtesting.handle_verify_auto_promotion.__code__.co_consts

    assert 99.0 not in source_values
    assert 999 not in source_values


def test_auto_demotion_seed_creates_metric_parent_runs_for_postgres() -> None:
    class _Dialect:
        name = "postgresql"

    class _Bind:
        dialect = _Dialect()

    class _FakeSession:
        bind = _Bind()

        def __init__(self) -> None:
            self.rows: list[object] = []

        def add(self, row) -> None:
            self.rows.append(row)

        def flush(self) -> None:
            pass

        def commit(self) -> None:
            pass

    session = _FakeSession()

    backtesting._auto_demotion_audit_seed(session=session)

    assert any(isinstance(row, DatasetVersions) for row in session.rows)
    simulation_run_ids = {row.run_id for row in session.rows if isinstance(row, SimulationRuns)}
    metric_run_ids = {row.run_id for row in session.rows if isinstance(row, MetricsSummary)}
    assert metric_run_ids
    assert metric_run_ids <= simulation_run_ids
