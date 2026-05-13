from __future__ import annotations

import argparse
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from autonomous_trading_platform.cli.formatters import print_error, print_header, print_json
from autonomous_trading_platform.db import get_session
from autonomous_trading_platform.governance.models.governance_state import GovernanceState
from autonomous_trading_platform.research.strategy_generation.generators.utils import make_config
from autonomous_trading_platform.storage.sor.models.allocation_overrides import AllocationOverrides
from autonomous_trading_platform.storage.sor.models.strategy_configs import StrategyConfigs
from autonomous_trading_platform.storage.sor.models.strategy_governance import StrategyGovernance
from autonomous_trading_platform.storage.sor.repositories.core.allocation_overrides_repository import (
    AllocationOverridesRepository,
)
from autonomous_trading_platform.storage.sor.repositories.core.operator_settings_repository import (
    OperatorSettingsRepository,
)
from autonomous_trading_platform.storage.sor.repositories.core.runtime_control_state_repository import (
    RuntimeControlStateRepository,
)
from autonomous_trading_platform.storage.sor.repositories.core.strategy_configs_repository import (
    StrategyConfigsRepository,
)
from autonomous_trading_platform.storage.sor.repositories.core.strategy_control_state_repository import (
    StrategyControlStateRepository,
)

_FIXTURE_ACTOR = "fixture-seed"

_VALID_GOVERNANCE_STATES = {s.value for s in GovernanceState}

_VALID_SETTINGS_KEYS = {
    "risk_tolerance",
    "max_drawdown_limit",
    "max_strategy_drawdown",
    "rebalance_frequency",
    "auto_promote_enabled",
    "min_sharpe_for_promotion",
    "min_paper_trading_period_days",
    "auto_demote_on_breach",
    "notify_drawdown_alerts",
    "notify_strategy_promotion_events",
    "notify_pipeline_failures",
    "per_strategy_cap",
    "target_portfolio_volatility",
    "slippage_model",
    "transaction_cost_model",
}

_VALID_CONTROL_KEYS = {
    "trading_enabled",
    "trading_paused",
    "kill_switch_enabled",
    "trading_mode",
    "reason",
}


def register(subparsers) -> None:
    backtesting_parser = subparsers.add_parser(
        "backtesting",
        help="Backtesting operations",
    )
    backtesting_subparsers = backtesting_parser.add_subparsers(
        dest="backtesting_command",
        required=True,
    )

    run_parser = backtesting_subparsers.add_parser("run", help="Run one backtest")
    run_parser.add_argument("--timestamp")
    run_parser.set_defaults(func=handle_run)

    inspect_results_parser = backtesting_subparsers.add_parser(
        "inspect-results", help="Inspect backtesting results"
    )
    inspect_results_parser.add_argument("--run-id", required=True)
    inspect_results_parser.set_defaults(func=handle_inspect_results)

    seed_parser = backtesting_subparsers.add_parser(
        "seed-fixture",
        help=(
            "Seed the DB with strategies, allocations, and optional settings/controls "
            "from a YAML fixture file before running replay-debug."
        ),
    )
    seed_parser.add_argument(
        "--fixture",
        required=True,
        type=Path,
        help="Path to the YAML fixture file (e.g. fixtures/ma_crossover_debug.yaml)",
    )
    seed_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be seeded without writing to the DB",
    )
    seed_parser.set_defaults(func=handle_seed_fixture)

    seed_settings_parser = backtesting_subparsers.add_parser(
        "seed-settings",
        help="Write operator settings to the DB from a YAML config file.",
    )
    seed_settings_parser.add_argument(
        "--config",
        required=True,
        type=Path,
        help="Path to the settings YAML file (e.g. fixtures/settings.yaml)",
    )
    seed_settings_parser.set_defaults(func=handle_seed_settings)

    read_settings_parser = backtesting_subparsers.add_parser(
        "read-settings",
        help="Print the current operator settings from the DB.",
    )
    read_settings_parser.set_defaults(func=handle_read_settings)

    seed_controls_parser = backtesting_subparsers.add_parser(
        "seed-controls",
        help="Seed strategies, allocations, and runtime controls from a YAML config file.",
    )
    seed_controls_parser.add_argument(
        "--config",
        required=True,
        type=Path,
        help="Path to the controls YAML file (e.g. fixtures/controls.yaml)",
    )
    seed_controls_parser.add_argument(
        "--clean",
        action="store_true",
        help="Wipe all existing strategy governance/control/allocation rows before seeding.",
    )
    seed_controls_parser.set_defaults(func=handle_seed_controls)

    read_controls_parser = backtesting_subparsers.add_parser(
        "read-controls",
        help="Print the current controls state from the DB, grouped by frontend section.",
    )
    read_controls_parser.set_defaults(func=handle_read_controls)

    read_portfolio_parser = backtesting_subparsers.add_parser(
        "read-portfolio",
        help="Print current portfolio state exactly as the API serves it to the frontend.",
    )
    read_portfolio_parser.set_defaults(func=handle_read_portfolio)

    read_dashboard_parser = backtesting_subparsers.add_parser(
        "read-dashboard",
        help="Print current dashboard state exactly as the API serves it to the frontend.",
    )
    read_dashboard_parser.set_defaults(func=handle_read_dashboard)


def handle_run(args: argparse.Namespace) -> int:
    print_header("Backtesting Run")
    print_json({"timestamp": args.timestamp, "status": "not_implemented"})
    return 0


def handle_inspect_results(args: argparse.Namespace) -> int:
    print_header("Backtesting Results")
    print_json({"run_id": args.run_id, "status": "not_implemented"})
    return 0


def handle_seed_fixture(args: argparse.Namespace) -> int:
    fixture_path: Path = args.fixture
    dry_run: bool = args.dry_run

    if not fixture_path.exists():
        print_error(f"Fixture file not found: {fixture_path}")
        return 1

    try:
        raw = yaml.safe_load(fixture_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        print_error(f"Failed to parse fixture YAML: {exc}")
        return 1

    label = raw.get("label", str(fixture_path.name))
    strategy_defs: list[dict[str, Any]] = raw.get("strategies", [])
    settings_patch: dict[str, Any] = raw.get("settings", {})
    controls_patch: dict[str, Any] = raw.get("controls", {})

    # --- validate before touching DB ---
    errors = _validate_fixture(strategy_defs, settings_patch, controls_patch)
    if errors:
        for err in errors:
            print_error(err)
        return 1

    print_header(f"Seed Fixture: {label}")

    if dry_run:
        print("  DRY RUN — no DB writes will occur\n")

    seeded_strategies = []
    for strat_def in strategy_defs:
        strategy_type = strat_def["type"]
        params = strat_def.get("parameters", {})
        display_name = strat_def.get("display_name", strategy_type)
        enabled = bool(strat_def.get("enabled", True))
        governance_state = strat_def.get("governance_state", "approved_research")
        allocation_pct = strat_def.get("allocation_pct")

        config = make_config(strategy_type, params)

        seeded_strategies.append(
            {
                "strategy_id": config.strategy_id,
                "display_name": display_name,
                "type": strategy_type,
                "enabled": enabled,
                "governance_state": governance_state,
                "allocation_pct": allocation_pct,
                "config_hash": config.config_hash(),
            }
        )

    print_json(
        {
            "label": label,
            "dry_run": dry_run,
            "strategies_to_seed": seeded_strategies,
            "settings_patch": settings_patch or None,
            "controls_patch": controls_patch or None,
        }
    )

    if dry_run:
        return 0

    session = get_session()
    try:
        now = datetime.now(UTC)

        configs_repo = StrategyConfigsRepository(session)
        control_state_repo = StrategyControlStateRepository(session)
        alloc_repo = AllocationOverridesRepository(session)

        for strat_def in strategy_defs:
            strategy_type = strat_def["type"]
            params = strat_def.get("parameters", {})
            display_name = strat_def.get("display_name", strategy_type)
            enabled = bool(strat_def.get("enabled", True))
            governance_state_str = strat_def.get("governance_state", "approved_research")
            allocation_pct = strat_def.get("allocation_pct")

            config = make_config(strategy_type, params)
            config_hash = config.config_hash()

            # 1. strategy_configs
            configs_repo.upsert(
                StrategyConfigs(
                    strategy_id=config.strategy_id,
                    config_hash=config_hash,
                    config_json=config.model_dump(mode="json"),
                    created_at=now,
                    strategy_type=strategy_type,
                    metadata_json={"display_name": display_name, "seeded_by": _FIXTURE_ACTOR},
                )
            )

            # 2. strategy_governance
            governance_row = StrategyGovernance(
                strategy_id=config.strategy_id,
                config_hash=config_hash,
                current_state=governance_state_str,
                experiment_id="fixture_seed",
                source_run_id=None,
                submitted_at=now,
                updated_at=now,
                submitted_by=_FIXTURE_ACTOR,
            )
            existing_gov = session.get(
                StrategyGovernance, {"strategy_id": config.strategy_id, "config_hash": config_hash}
            )
            if existing_gov is None:
                session.add(governance_row)
            else:
                existing_gov.current_state = governance_state_str
                existing_gov.updated_at = now

            # 3. strategy_control_states
            control_state_repo.set_enabled(
                strategy_id=config.strategy_id,
                enabled=enabled,
                reason="fixture seed",
                updated_by=_FIXTURE_ACTOR,
                updated_at=now,
            )

            # 4. allocation_overrides — deactivate old, create fresh
            if allocation_pct is not None:
                alloc_repo.deactivate_override(config.strategy_id)
                session.flush()
                alloc_repo.create_override(
                    AllocationOverrides(
                        override_id=str(uuid.uuid4()),
                        strategy_id=config.strategy_id,
                        overridden_by=_FIXTURE_ACTOR,
                        override_reason=f"fixture seed: {label}",
                        max_pct_of_capital=float(allocation_pct),
                        max_position_size_usd=None,
                        max_drawdown_allowed=None,
                        is_active=True,
                        created_at=now,
                        expires_at=None,
                    )
                )

        # 5. operator_settings patch (optional)
        if settings_patch:
            OperatorSettingsRepository(session).update_current(
                values=settings_patch, updated_by=_FIXTURE_ACTOR
            )

        # 6. runtime_control_state patch (optional)
        if controls_patch:
            ctrl_repo = RuntimeControlStateRepository(session)
            state = ctrl_repo.get_or_create_global_state()
            for key, value in controls_patch.items():
                setattr(state, key, value)
            state.updated_by = _FIXTURE_ACTOR
            state.updated_at = now
            session.flush()

        session.commit()

    except Exception as exc:
        session.rollback()
        print_error(f"Seed failed — rolled back: {exc}")
        return 1
    finally:
        session.close()

    print("\n  Seed complete. Run replay-debug with --reset-sim-state to start a clean backtest.")
    return 0


def handle_seed_settings(args: argparse.Namespace) -> int:
    config_path: Path = args.config
    if not config_path.exists():
        print_error(f"Config file not found: {config_path}")
        return 1

    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        print_error(f"Failed to parse settings YAML: {exc}")
        return 1

    patch: dict[str, Any] = raw.get(
        "settings", raw
    )  # accept bare dict or wrapped under 'settings:'

    unknown = set(patch) - _VALID_SETTINGS_KEYS
    if unknown:
        for key in sorted(unknown):
            print_error(
                f"Unknown settings key: '{key}'. Valid keys: {sorted(_VALID_SETTINGS_KEYS)}"
            )
        return 1

    if not patch:
        print_error("No settings fields found in config file.")
        return 1

    print_header("Seed Settings")
    print_json({"writing": patch})

    session = get_session()
    try:
        OperatorSettingsRepository(session).update_current(values=patch, updated_by=_FIXTURE_ACTOR)
        session.commit()
    except Exception as exc:
        session.rollback()
        print_error(f"Seed failed — rolled back: {exc}")
        return 1
    finally:
        session.close()

    print("\n  Done. Refresh the Settings page in the frontend to verify.")
    return 0


def handle_read_settings(args: argparse.Namespace) -> int:
    print_header("Current Operator Settings (DB)")
    session = get_session()
    try:
        row = OperatorSettingsRepository(session).get_current()
    finally:
        session.close()

    if row is None:
        print_json({"status": "no settings row found — run seed-settings first"})
        return 0

    print_json(
        {
            "risk_tolerance": row.risk_tolerance,
            "max_drawdown_limit": float(row.max_drawdown_limit),
            "max_strategy_drawdown": float(row.max_strategy_drawdown),
            "per_strategy_cap": float(row.per_strategy_cap),
            "target_portfolio_volatility": float(row.target_portfolio_volatility),
            "rebalance_frequency": row.rebalance_frequency,
            "auto_promote_enabled": row.auto_promote_enabled,
            "min_sharpe_for_promotion": float(row.min_sharpe_for_promotion),
            "min_paper_trading_period_days": row.min_paper_trading_period_days,
            "auto_demote_on_breach": row.auto_demote_on_breach,
            "slippage_model": row.slippage_model,
            "transaction_cost_model": row.transaction_cost_model,
            "notify_drawdown_alerts": row.notify_drawdown_alerts,
            "notify_strategy_promotion_events": row.notify_strategy_promotion_events,
            "notify_pipeline_failures": row.notify_pipeline_failures,
            "updated_by": row.updated_by,
            "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        }
    )
    return 0


def handle_seed_controls(args: argparse.Namespace) -> int:
    clean: bool = args.clean
    config_path: Path = args.config
    if not config_path.exists():
        print_error(f"Config file not found: {config_path}")
        return 1

    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        print_error(f"Failed to parse controls YAML: {exc}")
        return 1

    controls_patch: dict[str, Any] = raw.get("controls", {})
    strategy_defs: list[dict[str, Any]] = raw.get("strategies", [])

    unknown_ctrl = set(controls_patch) - _VALID_CONTROL_KEYS
    if unknown_ctrl:
        for key in sorted(unknown_ctrl):
            print_error(f"Unknown controls key: '{key}'")
        return 1

    print_header("Seed Controls")

    # Preview what will be written
    preview_strategies = []
    for strat_def in strategy_defs:
        config = make_config(strat_def["type"], strat_def.get("parameters", {}))
        gov = strat_def.get("governance_state", "approved_for_paper_trading")
        section = _controls_section_for_governance(gov)
        preview_strategies.append(
            {
                "strategy_id": config.strategy_id,
                "display_name": strat_def.get("display_name", strat_def["type"]),
                "governance_state": gov,
                "frontend_section": section,
                "enabled": strat_def.get("enabled", True),
                "allocation_pct": strat_def.get("allocation_pct"),
            }
        )

    print_json(
        {
            "yaml_contents": {
                "controls": controls_patch or None,
                "strategies": preview_strategies,
            }
        }
    )

    session = get_session()
    try:
        now = datetime.now(UTC)
        configs_repo = StrategyConfigsRepository(session)
        ctrl_state_repo = StrategyControlStateRepository(session)
        alloc_repo = AllocationOverridesRepository(session)

        if clean:
            from sqlalchemy import delete as sa_delete

            from autonomous_trading_platform.storage.sor.models.strategy_control_states import (
                StrategyControlState as _SCSModel,
            )

            session.execute(sa_delete(StrategyGovernance))
            session.execute(sa_delete(_SCSModel))
            session.execute(
                sa_delete(AllocationOverrides).where(AllocationOverrides.is_active.is_(True))
            )
            session.flush()
            print(
                "  Cleaned existing strategy governance, control states, and active allocation overrides.\n"
            )

        for strat_def in strategy_defs:
            strategy_type = strat_def["type"]
            params = strat_def.get("parameters", {})
            display_name = strat_def.get("display_name", strategy_type)
            enabled = bool(strat_def.get("enabled", True))
            governance_state_str = strat_def.get("governance_state", "approved_for_paper_trading")
            allocation_pct = strat_def.get("allocation_pct")

            config = make_config(strategy_type, params)
            config_hash = config.config_hash()

            configs_repo.upsert(
                StrategyConfigs(
                    strategy_id=config.strategy_id,
                    config_hash=config_hash,
                    config_json=config.model_dump(mode="json"),
                    created_at=now,
                    strategy_type=strategy_type,
                    metadata_json={"display_name": display_name, "seeded_by": _FIXTURE_ACTOR},
                )
            )

            existing_gov = session.get(
                StrategyGovernance,
                {"strategy_id": config.strategy_id, "config_hash": config_hash},
            )
            if existing_gov is None:
                session.add(
                    StrategyGovernance(
                        strategy_id=config.strategy_id,
                        config_hash=config_hash,
                        current_state=governance_state_str,
                        experiment_id="controls_seed",
                        source_run_id=None,
                        submitted_at=now,
                        updated_at=now,
                        submitted_by=_FIXTURE_ACTOR,
                    )
                )
            else:
                existing_gov.current_state = governance_state_str
                existing_gov.updated_at = now

            ctrl_state_repo.set_enabled(
                strategy_id=config.strategy_id,
                enabled=enabled,
                reason="controls seed",
                updated_by=_FIXTURE_ACTOR,
                updated_at=now,
            )

            if allocation_pct is not None:
                alloc_repo.deactivate_override(config.strategy_id)
                session.flush()
                alloc_repo.create_override(
                    AllocationOverrides(
                        override_id=str(uuid.uuid4()),
                        strategy_id=config.strategy_id,
                        overridden_by=_FIXTURE_ACTOR,
                        override_reason="controls seed",
                        max_pct_of_capital=float(allocation_pct),
                        max_position_size_usd=None,
                        max_drawdown_allowed=None,
                        is_active=True,
                        created_at=now,
                        expires_at=None,
                    )
                )

        if controls_patch:
            ctrl_repo = RuntimeControlStateRepository(session)
            state = ctrl_repo.get_or_create_global_state()
            for key, value in controls_patch.items():
                setattr(state, key, value)
            state.updated_by = _FIXTURE_ACTOR
            state.updated_at = now
            session.flush()

        session.commit()
    except Exception as exc:
        session.rollback()
        print_error(f"Seed failed — rolled back: {exc}")
        return 1
    finally:
        session.close()

    print("\n  Seed complete. Refresh the Controls page in the frontend to verify.")
    return 0


def handle_read_controls(args: argparse.Namespace) -> int:
    from sqlalchemy import select as sa_select

    print_header("Current Controls State (DB)")
    session = get_session()
    try:
        ctrl_repo = RuntimeControlStateRepository(session)
        runtime = ctrl_repo.get_global_state()

        # All strategies with their governance, control state, and allocation
        from autonomous_trading_platform.storage.sor.models.allocation_overrides import (
            AllocationOverrides as AO,
        )
        from autonomous_trading_platform.storage.sor.models.strategy_control_states import (
            StrategyControlState as SCS,
        )
        from autonomous_trading_platform.storage.sor.models.strategy_governance import (
            StrategyGovernance as SG,
        )

        gov_rows = list(session.scalars(sa_select(SG)).all())
        ctrl_by_id = {r.strategy_id: r for r in session.scalars(sa_select(SCS)).all()}
        alloc_by_id = {
            r.strategy_id: r
            for r in session.scalars(sa_select(AO).where(AO.is_active.is_(True))).all()
        }
        configs_by_id = {
            r.strategy_id: r
            for r in session.scalars(
                sa_select(StrategyConfigs).where(
                    StrategyConfigs.strategy_id.in_([g.strategy_id for g in gov_rows])
                )
            ).all()
        }
    finally:
        session.close()

    def _display_name(strategy_id: str) -> str:
        cfg = configs_by_id.get(strategy_id)
        if cfg and cfg.metadata_json:
            return str(cfg.metadata_json.get("display_name", strategy_id))
        return strategy_id

    toggles, allocations, pending = [], [], []
    for g in gov_rows:
        ctrl = ctrl_by_id.get(g.strategy_id)
        alloc = alloc_by_id.get(g.strategy_id)
        section = _controls_section_for_governance(g.current_state)
        entry = {
            "strategy_id": g.strategy_id,
            "display_name": _display_name(g.strategy_id),
            "governance_state": g.current_state,
            "enabled": ctrl.enabled if ctrl else True,
            "allocation_pct": float(alloc.max_pct_of_capital)
            if alloc and alloc.max_pct_of_capital
            else None,
        }
        if section == "strategy_toggles":
            toggles.append(entry)
        elif section == "pending_promotion":
            pending.append(entry)

        if section == "strategy_toggles" and alloc:
            allocations.append({**entry, "allocation_pct": float(alloc.max_pct_of_capital)})

    print_json(
        {
            "db_state": {
                "kill_switch": {
                    "kill_switch_enabled": runtime.kill_switch_enabled if runtime else None,
                    "trading_enabled": runtime.trading_enabled if runtime else None,
                    "trading_paused": runtime.trading_paused if runtime else None,
                    "trading_mode": runtime.trading_mode if runtime else None,
                },
                "strategy_toggles": toggles,
                "allocation_overrides": allocations,
                "pending_promotion": pending,
            }
        }
    )
    return 0


def handle_read_portfolio(args: argparse.Namespace) -> int:
    from autonomous_trading_platform.application.services.portfolio_analytics_service import (
        PortfolioAnalyticsService,
    )
    from autonomous_trading_platform.application.services.portfolio_equity_curve_service import (
        PortfolioEquityCurveService,
    )
    from autonomous_trading_platform.application.services.portfolio_summary_service import (
        PortfolioSummaryService,
    )

    print_header("Portfolio State (DB / same as API)")
    session = get_session()
    try:
        summary = PortfolioSummaryService(session=session).get_summary()
        holdings = PortfolioAnalyticsService(session=session).get_holdings()
        alloc = PortfolioAnalyticsService(session=session).get_allocation()
        risk = PortfolioAnalyticsService(session=session).get_risk()
        perf = PortfolioAnalyticsService(session=session).get_performance()
        curve_1m = PortfolioEquityCurveService(session=session).get_equity_curve("1m")
    finally:
        session.close()

    print_json(
        {
            "portfolio_summary": {k: _to_json(v) for k, v in summary.items()},
            "holdings": [
                {k: _to_json(v) for k, v in h.items()} for h in holdings.get("holdings", [])
            ],
            "allocation": {
                "by_strategy": [
                    {k: _to_json(v) for k, v in row.items()} for row in alloc.get("by_strategy", [])
                ],
                "by_asset": [
                    {k: _to_json(v) for k, v in row.items()} for row in alloc.get("by_asset", [])
                ],
            },
            "risk": {k: _to_json(v) for k, v in risk.items()},
            "performance": {k: _to_json(v) for k, v in perf.items()},
            "equity_curve_1m_points": len(curve_1m.get("points", [])),
            "equity_curve_1m_range": _curve_range(curve_1m.get("points", [])),
        }
    )
    return 0


def handle_read_dashboard(args: argparse.Namespace) -> int:
    from autonomous_trading_platform.application.services.active_strategies_service import (
        ActiveStrategiesService,
    )
    from autonomous_trading_platform.application.services.portfolio_analytics_service import (
        PortfolioAnalyticsService,
    )
    from autonomous_trading_platform.application.services.portfolio_equity_curve_service import (
        PortfolioEquityCurveService,
    )
    from autonomous_trading_platform.application.services.portfolio_summary_service import (
        PortfolioSummaryService,
    )

    print_header("Dashboard State (DB / same as API)")
    session = get_session()
    try:
        summary = PortfolioSummaryService(session=session).get_summary()
        active_strategies = ActiveStrategiesService(session=session).list_active_strategies()
        risk = PortfolioAnalyticsService(session=session).get_risk()
        perf = PortfolioAnalyticsService(session=session).get_performance()
        curve_1m = PortfolioEquityCurveService(session=session).get_equity_curve("1m")
    finally:
        session.close()

    print_json(
        {
            "portfolio_summary": {k: _to_json(v) for k, v in summary.items()},
            "active_strategies": [
                {k: _to_json(v) for k, v in s.items()} for s in active_strategies
            ],
            "risk": {k: _to_json(v) for k, v in risk.items()},
            "performance": {k: _to_json(v) for k, v in perf.items()},
            "equity_curve_1m_points": len(curve_1m.get("points", [])),
            "equity_curve_1m_range": _curve_range(curve_1m.get("points", [])),
        }
    )
    return 0


def _to_json(value: Any) -> Any:
    from datetime import date, datetime
    from decimal import Decimal

    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


def _curve_range(points: list[dict]) -> dict | None:
    if not points:
        return None
    timestamps: list[str] = [str(p["timestamp"]) for p in points if p.get("timestamp") is not None]
    values: list[float] = [float(p["value"]) for p in points if p.get("value") is not None]
    return {
        "from": min(timestamps) if timestamps else None,
        "to": max(timestamps) if timestamps else None,
        "min_equity": min(values) if values else None,
        "max_equity": max(values) if values else None,
    }


def _controls_section_for_governance(state: str) -> str:
    if state in {
        "approved_for_paper_trading",
        "approved_for_live_trading",
        "approved_paper",
        "approved_live",
    }:
        return "strategy_toggles"
    if state in {"approved_research", "proposed"}:
        return "pending_promotion"
    return "other"


def _validate_fixture(
    strategy_defs: list[dict[str, Any]],
    settings_patch: dict[str, Any],
    controls_patch: dict[str, Any],
) -> list[str]:
    errors: list[str] = []

    if not strategy_defs:
        errors.append("Fixture must define at least one strategy under 'strategies:'")

    for i, strat in enumerate(strategy_defs):
        tag = f"strategies[{i}]"
        if "type" not in strat:
            errors.append(f"{tag}: 'type' is required")
        gov = strat.get("governance_state", "approved_research")
        if gov not in _VALID_GOVERNANCE_STATES:
            errors.append(
                f"{tag}: invalid governance_state '{gov}'. Valid: {sorted(_VALID_GOVERNANCE_STATES)}"
            )
        alloc = strat.get("allocation_pct")
        if alloc is not None and not (0.0 < float(alloc) <= 1.0):
            errors.append(f"{tag}: allocation_pct must be between 0.0 and 1.0 (got {alloc})")

    for key in settings_patch:
        if key not in _VALID_SETTINGS_KEYS:
            errors.append(
                f"settings: unknown key '{key}'. Valid keys: {sorted(_VALID_SETTINGS_KEYS)}"
            )

    for key in controls_patch:
        if key not in _VALID_CONTROL_KEYS:
            errors.append(
                f"controls: unknown key '{key}'. Valid keys: {sorted(_VALID_CONTROL_KEYS)}"
            )

    return errors
