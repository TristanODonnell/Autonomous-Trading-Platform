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
