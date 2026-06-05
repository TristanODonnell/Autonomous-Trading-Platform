from __future__ import annotations

import argparse
import hashlib
import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, cast, get_args, get_origin

import yaml
from pydantic import ValidationError

from autonomous_trading_platform.application.services.operator_settings_service import (
    OperatorSettingsService,
)
from autonomous_trading_platform.cli.formatters import print_error, print_header, print_json
from autonomous_trading_platform.db import get_session
from autonomous_trading_platform.interfaces.rest.schemas.settings_schema import (
    OperatorSettingsUpdateRequest,
)
from autonomous_trading_platform.storage.sor.repositories.core.audit_logs_repository import (
    AuditLogRepository,
)
from autonomous_trading_platform.storage.sor.repositories.core.operator_settings_repository import (
    OperatorSettingsRepository,
)

_MUTATION_MESSAGE = "Mutating settings commands require --updated-by and --reason."
_ALLOWED_UPDATE_KEYS = frozenset(OperatorSettingsUpdateRequest.model_fields) - {"reason"}


def register(subparsers) -> None:
    parser = subparsers.add_parser(
        "settings",
        help="Inspect, validate, and update persisted operator settings.",
    )
    settings_subparsers = parser.add_subparsers(dest="settings_command", required=True)

    show_parser = settings_subparsers.add_parser("show", help="Show current settings.")
    show_parser.add_argument("--json", action="store_true", help="Print JSON output.")
    show_parser.set_defaults(func=handle_show)

    sources_parser = settings_subparsers.add_parser(
        "sources",
        help="Show setting source-of-truth metadata.",
    )
    sources_parser.set_defaults(func=handle_sources)

    validate_parser = settings_subparsers.add_parser(
        "validate",
        help="Validate a settings config without writing it.",
    )
    validate_parser.add_argument("--config", required=True, type=Path)
    validate_parser.set_defaults(func=handle_validate)

    diff_parser = settings_subparsers.add_parser(
        "diff",
        help="Diff current persisted settings against a config.",
    )
    diff_parser.add_argument("--config", required=True, type=Path)
    diff_parser.set_defaults(func=handle_diff)

    seed_parser = settings_subparsers.add_parser(
        "seed",
        help="Apply settings from a JSON/YAML config.",
    )
    seed_parser.add_argument("--config", required=True, type=Path)
    seed_parser.add_argument("--dry-run", action="store_true")
    seed_parser.add_argument("--updated-by")
    seed_parser.add_argument("--reason")
    seed_parser.set_defaults(func=handle_seed)

    set_parser = settings_subparsers.add_parser(
        "set",
        help="Apply one setting value.",
    )
    set_parser.add_argument("--key", required=True, choices=sorted(_ALLOWED_UPDATE_KEYS))
    set_parser.add_argument("--value", required=True)
    set_parser.add_argument("--updated-by")
    set_parser.add_argument("--reason")
    set_parser.add_argument("--dry-run", action="store_true")
    set_parser.set_defaults(func=handle_set)

    risk_profile_parser = settings_subparsers.add_parser(
        "risk-profile",
        help="Show the effective operator risk profile.",
    )
    risk_profile_parser.set_defaults(func=handle_risk_profile)

    advanced_parser = settings_subparsers.add_parser(
        "advanced",
        help="Show advanced settings metadata from runtime configuration and overrides.",
    )
    advanced_parser.set_defaults(func=handle_advanced)

    export_parser = settings_subparsers.add_parser(
        "export",
        help="Export current settings and source metadata.",
    )
    export_parser.add_argument("--output", required=True, type=Path)
    export_parser.set_defaults(func=handle_export)

    snapshot_parser = settings_subparsers.add_parser(
        "snapshot",
        help="Write a settings/runtime configuration snapshot with a content hash.",
    )
    snapshot_parser.add_argument("--output", required=True, type=Path)
    snapshot_parser.set_defaults(func=handle_snapshot)

    verify_persisted_parser = settings_subparsers.add_parser(
        "verify-persisted",
        help="Verify persisted setting values.",
    )
    verify_persisted_parser.add_argument(
        "--expect",
        action="append",
        default=[],
        help="Expected key=value pair; repeatable.",
    )
    verify_persisted_parser.set_defaults(func=handle_verify_persisted)

    audit_log_parser = settings_subparsers.add_parser(
        "audit-log",
        help="Show recent OPERATOR_SETTINGS_UPDATED audit entries.",
    )
    audit_log_parser.add_argument("--limit", type=_positive_int, default=20)
    audit_log_parser.set_defaults(func=handle_audit_log)

    verify_runtime_parser = settings_subparsers.add_parser(
        "verify-runtime-effect",
        help="Settings-domain wrapper for backtesting risk parameter verification.",
    )
    verify_runtime_parser.add_argument("--controls", required=True, type=Path)
    verify_runtime_parser.add_argument("--config", required=True, type=Path)
    verify_runtime_parser.add_argument("--symbols", required=True)
    verify_runtime_parser.add_argument("--start", required=True)
    verify_runtime_parser.add_argument("--end", required=True)
    verify_runtime_parser.add_argument("--starting-cash", type=Decimal, default=Decimal("100000"))
    verify_runtime_parser.add_argument("--random-seed", type=int, default=42)
    verify_runtime_parser.add_argument("--reset-sim-state", action="store_true")
    verify_runtime_parser.add_argument("--print-summary", action="store_true")
    verify_runtime_parser.add_argument(
        "--parameter",
        action="append",
        dest="parameters",
        choices=[
            "max_portfolio_drawdown",
            "max_strategy_drawdown",
            "risk_tolerance",
            "max_capital_per_strategy",
            "target_portfolio_volatility",
        ],
    )
    verify_runtime_parser.set_defaults(func=handle_verify_runtime_effect)

    verify_notify_parser = settings_subparsers.add_parser(
        "verify-notifications",
        help="Settings-domain wrapper for backtesting notification verification.",
    )
    verify_notify_parser.add_argument("--controls", required=True, type=Path)
    verify_notify_parser.add_argument("--config", required=True, type=Path)
    verify_notify_parser.set_defaults(func=handle_verify_notifications)

    reset_parser = settings_subparsers.add_parser(
        "reset-defaults",
        help="Reset persisted settings to platform defaults.",
    )
    reset_parser.add_argument("--dry-run", action="store_true")
    reset_parser.add_argument("--updated-by")
    reset_parser.add_argument("--reason")
    reset_parser.set_defaults(func=handle_reset_defaults)


def handle_show(args: argparse.Namespace) -> int:
    payload = _current_settings_payload()
    if payload is None:
        print_json(
            {
                "found": False,
                "message": "No persisted operator settings found; run `atp settings seed` first.",
            }
        )
        return 0

    print_json({"found": True, "settings": payload})
    return 0


def handle_sources(args: argparse.Namespace) -> int:
    payload = _current_settings_payload() or _default_settings_payload()
    print_json(_settings_metadata(payload))
    return 0


def handle_validate(args: argparse.Namespace) -> int:
    patch = _read_config_patch(args.config)
    if patch is None:
        return 1

    validated = _validate_patch(patch)
    if validated is None:
        return 1

    print_json({"valid": True, "settings": _jsonable(validated)})
    return 0


def handle_diff(args: argparse.Namespace) -> int:
    patch = _read_config_patch(args.config)
    if patch is None:
        return 1

    validated = _validate_patch(patch)
    if validated is None:
        return 1

    persisted = _current_settings_payload()
    current = persisted or _default_settings_payload()
    print_json(
        {
            "current_found": persisted is not None,
            "diff": _diff_settings(current, validated),
        }
    )
    return 0


def handle_seed(args: argparse.Namespace) -> int:
    patch = _read_config_patch(args.config)
    if patch is None:
        return 1

    validated = _validate_patch(patch)
    if validated is None:
        return 1

    if args.dry_run:
        current = _current_settings_payload() or _default_settings_payload()
        print_json(
            {
                "dry_run": True,
                "would_write": _jsonable(validated),
                "diff": _diff_settings(current, validated),
            }
        )
        return 0

    if not _has_actor_and_reason(args):
        return 1

    return _update_settings(validated, updated_by=args.updated_by, reason=args.reason)


def handle_set(args: argparse.Namespace) -> int:
    value = _coerce_value(args.key, args.value)
    if value is _InvalidValue:
        return 1

    validated = _validate_patch({args.key: value})
    if validated is None:
        return 1

    if args.dry_run:
        current = _current_settings_payload() or _default_settings_payload()
        print_json(
            {
                "dry_run": True,
                "would_write": _jsonable(validated),
                "diff": _diff_settings(current, validated),
            }
        )
        return 0

    if not _has_actor_and_reason(args):
        return 1

    return _update_settings(validated, updated_by=args.updated_by, reason=args.reason)


def handle_risk_profile(args: argparse.Namespace) -> int:
    session = get_session()
    try:
        result = _service(session).get_risk_profile()
    finally:
        session.close()

    print_json(result)
    return 0


def handle_advanced(args: argparse.Namespace) -> int:
    from sqlalchemy import select

    from autonomous_trading_platform.config.settings import Settings
    from autonomous_trading_platform.storage.sor.models.allocation_overrides import (
        AllocationOverrides,
    )

    session = get_session()
    try:
        settings = Settings()
        drawdown_overrides = [
            {
                "strategy_id": row.strategy_id,
                "max_drawdown_allowed": row.max_drawdown_allowed,
                "updated_by": row.overridden_by,
                "reason": row.override_reason,
            }
            for row in session.scalars(
                select(AllocationOverrides)
                .where(AllocationOverrides.is_active.is_(True))
                .where(AllocationOverrides.max_drawdown_allowed.is_not(None))
                .order_by(AllocationOverrides.strategy_id.asc())
            )
        ]
        payload = {
            "read_only": True,
            "per_strategy_max_drawdown_overrides": drawdown_overrides,
            "position_size_caps_per_asset": [
                {
                    "asset": "*",
                    "max_position_size_usd": settings.max_symbol_exposure,
                }
            ],
            "cost_model_configuration": {
                "fixed_commission": Decimal("0"),
                "per_share_commission": Decimal("0"),
                "default_half_spread": Decimal("0"),
                "extra_slippage_bps": Decimal("0"),
                "slippage_rate": Decimal("0.0001"),
            },
            "metadata": {
                "position_size_caps_source": "MAX_SYMBOL_EXPOSURE",
                "cost_model_source": "simulation defaults",
            },
        }
    finally:
        session.close()

    print_json(payload)
    return 0


def handle_export(args: argparse.Namespace) -> int:
    current = _current_settings_payload()
    payload = {
        "settings": current,
        "metadata": _settings_metadata(current or _default_settings_payload()),
        "exported_at": datetime.now(UTC).isoformat(),
    }
    _write_payload(args.output, payload)
    print_json({"wrote": str(args.output), "settings_found": payload["settings"] is not None})
    return 0


def handle_snapshot(args: argparse.Namespace) -> int:
    current = _current_settings_payload()
    payload: dict[str, Any] = {
        "settings": current,
        "metadata": _settings_metadata(current or _default_settings_payload()),
        "captured_at": datetime.now(UTC).isoformat(),
    }
    try:
        from autonomous_trading_platform.config.settings import Settings

        env_settings = Settings()
        payload["runtime_config"] = {
            "app_env": env_settings.app_env,
            "trading_environment": env_settings.trading_environment.value,
            "no_live_trading": env_settings.no_live_trading,
            "enable_live_trading": env_settings.enable_live_trading,
            "include_live_modules": env_settings.include_live_modules,
            "shadow_mode_enabled": env_settings.shadow_mode_enabled,
            "max_gross_exposure": env_settings.max_gross_exposure,
            "max_symbol_exposure": env_settings.max_symbol_exposure,
            "max_net_exposure": env_settings.max_net_exposure,
            "max_leverage": env_settings.max_leverage,
        }
    except Exception as exc:
        payload["runtime_config_error"] = str(exc)

    content = json.dumps(_jsonable(payload), sort_keys=True, default=str)
    payload["sha256"] = hashlib.sha256(content.encode("utf-8")).hexdigest()
    _write_payload(args.output, payload)
    print_json({"wrote": str(args.output), "sha256": payload["sha256"]})
    return 0


def handle_verify_persisted(args: argparse.Namespace) -> int:
    current = _current_settings_payload()
    if current is None:
        print_error("No persisted operator settings row found.")
        return 1

    expectations = _parse_expectations(args.expect)
    if expectations is None:
        return 1

    results = []
    all_matched = True
    for key, expected in expectations.items():
        if key not in current:
            print_error(f"Unknown persisted settings key: {key}")
            return 1
        actual = current[key]
        matched = _normalize_for_compare(actual) == _normalize_for_compare(expected)
        all_matched = all_matched and matched
        results.append({"key": key, "expected": expected, "actual": actual, "matched": matched})

    print_json({"matched": all_matched, "results": results})
    return 0 if all_matched else 1


def handle_audit_log(args: argparse.Namespace) -> int:
    session = get_session()
    try:
        repo = AuditLogRepository(session)
        events: list[dict[str, Any]] = []
        page = 1
        while len(events) < args.limit:
            rows, total = repo.list_events(
                page=page,
                page_size=max(args.limit, 20),
                action_type="OPERATOR_SETTINGS_UPDATED",
            )
            for row in rows:
                if row.component == "settings":
                    events.append(
                        {
                            "event_id": row.event_id,
                            "event_timestamp": row.event_timestamp.isoformat(),
                            "event_type": row.event_type,
                            "component": row.component,
                            "message": row.message,
                            "metadata": row.metadata_,
                        }
                    )
                    if len(events) >= args.limit:
                        break
            if page * max(args.limit, 20) >= total or not rows:
                break
            page += 1
    finally:
        session.close()

    print_json({"events": events, "limit": args.limit})
    return 0


def handle_verify_runtime_effect(args: argparse.Namespace) -> int:
    from autonomous_trading_platform.cli.commands.backtesting import (
        handle_verify_risk_parameter_effects,
    )

    wrapped = argparse.Namespace(**vars(args))
    wrapped.settings = args.config
    print_header("Settings Runtime Effect Verification")
    print_json({"wrapper": "backtesting.verify-risk-parameter-effects", "external_broker": False})
    return handle_verify_risk_parameter_effects(wrapped)


def handle_verify_notifications(args: argparse.Namespace) -> int:
    from autonomous_trading_platform.cli.commands.backtesting import (
        handle_verify_notification_events,
    )

    wrapped = argparse.Namespace(**vars(args))
    wrapped.settings = args.config
    print_header("Settings Notification Verification")
    print_json({"wrapper": "backtesting.verify-notification-events", "external_broker": False})
    return handle_verify_notification_events(wrapped)


def handle_reset_defaults(args: argparse.Namespace) -> int:
    defaults = _default_settings_payload()
    current = _current_settings_payload() or defaults

    if args.dry_run:
        print_json(
            {"dry_run": True, "would_write": defaults, "diff": _diff_settings(current, defaults)}
        )
        return 0

    if not _has_actor_and_reason(args):
        return 1

    return _update_settings(defaults, updated_by=args.updated_by, reason=args.reason)


def _service(session) -> OperatorSettingsService:
    return OperatorSettingsService(
        settings_repo=OperatorSettingsRepository(session),
        audit_log_repo=AuditLogRepository(session),
    )


def _current_settings_payload() -> dict[str, Any] | None:
    session = get_session()
    try:
        row = OperatorSettingsRepository(session).get_current()
        if row is None:
            return None
        return _row_to_payload(row)
    finally:
        session.close()


def _row_to_payload(row) -> dict[str, Any]:
    fields = [
        "risk_tolerance",
        "max_drawdown_limit",
        "max_strategy_drawdown",
        "rebalance_frequency",
        "auto_promote_enabled",
        "auto_rebalance_enabled",
        "min_sharpe_for_promotion",
        "min_paper_trading_period_days",
        "auto_demote_on_breach",
        "notify_drawdown_alerts",
        "notify_strategy_promotion_events",
        "notify_strategy_demotion_events",
        "notify_allocation_rebalance_events",
        "notify_pipeline_failures",
        "per_strategy_cap",
        "target_portfolio_volatility",
        "slippage_model",
        "transaction_cost_model",
        "max_total_strategy_allocation_pct",
        "max_portfolio_symbol_exposure_usd",
        "max_portfolio_symbol_pct",
        "min_rebalance_interval_hours",
        "min_allocation_change_pct",
        "turnover_penalty_weight",
        "portfolio_max_drawdown_pct",
        "portfolio_drawdown_action",
        "portfolio_drawdown_recovery_mode",
        "updated_by",
    ]
    payload = {field: getattr(row, field) for field in fields if hasattr(row, field)}
    payload["settings_id"] = row.settings_id
    payload["created_at"] = row.created_at.isoformat() if row.created_at else None
    payload["updated_at"] = row.updated_at.isoformat() if row.updated_at else None
    return payload


def _default_settings_payload() -> dict[str, Any]:
    return {
        "risk_tolerance": "medium",
        "max_drawdown_limit": Decimal("0.10"),
        "max_strategy_drawdown": Decimal("0.12"),
        "rebalance_frequency": "weekly",
        "auto_promote_enabled": False,
        "auto_rebalance_enabled": False,
        "min_sharpe_for_promotion": Decimal("1.5"),
        "min_paper_trading_period_days": 30,
        "auto_demote_on_breach": True,
        "notify_drawdown_alerts": True,
        "notify_strategy_promotion_events": True,
        "notify_pipeline_failures": True,
        "per_strategy_cap": Decimal("0.25"),
        "target_portfolio_volatility": Decimal("0.15"),
        "slippage_model": "fixed",
        "transaction_cost_model": "per_share",
        "max_total_strategy_allocation_pct": Decimal("1.0"),
    }


def _settings_metadata(settings: dict[str, Any]) -> dict[str, Any]:
    return {
        "source_of_truth": {
            "automation_controls_source": "settings",
            "promotion_thresholds_source": "promotion_rules",
            "allocation_targets_source": "capital_allocation_policies + allocation_overrides",
        },
        "automation_controls": {
            "auto_promote_enabled": {
                "value": settings.get("auto_promote_enabled"),
                "label": "Auto Promote",
                "description": (
                    "Allows eligible strategies to be promoted automatically based on "
                    "active Promotion Rules."
                ),
                "source": "settings",
            },
            "auto_demote_on_breach": {
                "value": settings.get("auto_demote_on_breach"),
                "label": "Auto Demote",
                "description": "Allows strategies to be demoted automatically when active rules are breached.",
                "source": "settings",
            },
            "auto_rebalance_enabled": {
                "value": settings.get("auto_rebalance_enabled"),
                "label": "Auto Rebalance",
                "description": "Allows allocation changes based on strategy quality and risk signals.",
                "source": "settings",
            },
            "rebalance_frequency": {
                "value": settings.get("rebalance_frequency"),
                "label": "Rebalance Frequency",
                "description": "How often the automated allocation review should run.",
                "source": "settings",
            },
        },
        "promotion_rules": {
            "label": "Promotion Rules",
            "source": "promotion_rules",
            "description": (
                "Active PromotionRules rows are the source of truth for manual and "
                "automated promotion eligibility thresholds."
            ),
            "controls": {
                "min_sharpe": "promotion_rules.min_sharpe",
                "max_drawdown": "promotion_rules.max_drawdown",
                "min_days_tested": "promotion_rules.min_days_tested",
                "min_trade_count": "promotion_rules.min_trade_count",
                "min_cagr": "promotion_rules.min_cagr",
                "min_win_rate": "promotion_rules.min_win_rate",
            },
        },
        "allocation_policies": {
            "label": "Allocation Policies",
            "source": "capital_allocation_policies + allocation_overrides",
            "description": (
                "CapitalAllocationPolicies define allocation caps by approval status and "
                "tier; active AllocationOverrides supply strategy-level overrides."
            ),
            "controls": {
                "policy_cap": "capital_allocation_policies.max_pct_of_capital",
                "position_cap": "capital_allocation_policies.max_position_size_usd",
                "drawdown_cap": "capital_allocation_policies.max_drawdown_allowed",
                "strategy_override": "allocation_overrides",
            },
        },
        "deprecated_or_ignored_settings": {
            "min_sharpe_for_promotion": {
                "value": settings.get("min_sharpe_for_promotion"),
                "status": "deprecated_persisted_only",
                "ignored_for": "manual_promotion_eligibility",
                "active_source": "promotion_rules.min_sharpe",
            },
            "min_paper_trading_period_days": {
                "value": settings.get("min_paper_trading_period_days"),
                "status": "deprecated_persisted_only",
                "ignored_for": "manual_promotion_eligibility",
                "active_source": "promotion_rules.min_days_tested",
            },
            "per_strategy_cap": {
                "value": settings.get("per_strategy_cap"),
                "status": "deprecated_persisted_only",
                "ignored_for": "allocation_targets",
                "active_source": "capital_allocation_policies.max_pct_of_capital",
            },
        },
    }


def _read_config_patch(config_path: Path) -> dict[str, Any] | None:
    if not config_path.exists():
        print_error(f"Config file not found: {config_path}")
        return None

    try:
        text = config_path.read_text(encoding="utf-8")
        raw = json.loads(text) if config_path.suffix.lower() == ".json" else yaml.safe_load(text)
    except (json.JSONDecodeError, yaml.YAMLError) as exc:
        print_error(f"Failed to parse settings config: {exc}")
        return None

    if not isinstance(raw, dict):
        print_error("Settings config must be an object.")
        return None

    patch = raw.get("settings", raw)
    if not isinstance(patch, dict):
        print_error("Settings config must be a bare object or contain a settings object.")
        return None
    if not patch:
        print_error("No settings fields found in config file.")
        return None

    return patch


def _validate_patch(patch: dict[str, Any]) -> dict[str, Any] | None:
    unknown = sorted(set(patch) - _ALLOWED_UPDATE_KEYS)
    if unknown:
        print_error(f"Unknown settings keys: {unknown}. Valid keys: {sorted(_ALLOWED_UPDATE_KEYS)}")
        return None

    try:
        request = OperatorSettingsUpdateRequest.model_validate(patch)
    except ValidationError as exc:
        print_error(str(exc))
        return None

    return cast(dict[str, Any], request.model_dump(exclude_none=True, exclude={"reason"}))


def _diff_settings(current: dict[str, Any], patch: dict[str, Any]) -> list[dict[str, Any]]:
    diff = []
    for key, new_value in patch.items():
        old_value = current.get(key)
        changed = _normalize_for_compare(old_value) != _normalize_for_compare(new_value)
        diff.append({"key": key, "current": old_value, "new": new_value, "changed": changed})
    return diff


def _update_settings(patch: dict[str, Any], *, updated_by: str, reason: str) -> int:
    session = get_session()
    try:
        result = _service(session).update_settings(
            patch,
            actor_user_id=updated_by,
            reason=reason,
        )
        session.commit()
        settings = dict(result.__dict__)
    except Exception as exc:
        session.rollback()
        print_error(f"Settings update failed; rolled back: {exc}")
        return 1
    finally:
        session.close()

    print_json({"updated": True, "settings": settings, "metadata": _settings_metadata(settings)})
    return 0


def _coerce_value(key: str, raw_value: str) -> Any:
    field = OperatorSettingsUpdateRequest.model_fields[key]
    annotation = field.annotation
    base_types = _annotation_types(annotation)

    if bool in base_types:
        value = raw_value.strip().lower()
        if value in {"1", "true", "yes", "on"}:
            return True
        if value in {"0", "false", "no", "off"}:
            return False
        print_error(f"{key} expects a boolean value.")
        return _InvalidValue
    if int in base_types:
        try:
            return int(raw_value)
        except ValueError:
            print_error(f"{key} expects an integer value.")
            return _InvalidValue
    if Decimal in base_types:
        try:
            return Decimal(raw_value)
        except Exception:
            print_error(f"{key} expects a decimal value.")
            return _InvalidValue
    return raw_value


def _annotation_types(annotation: Any) -> set[Any]:
    origin = get_origin(annotation)
    if origin is None:
        return {annotation}
    return set(get_args(annotation))


def _has_actor_and_reason(args: argparse.Namespace) -> bool:
    if args.updated_by and args.reason:
        return True
    print_error(_MUTATION_MESSAGE)
    return False


def _parse_expectations(values: list[str]) -> dict[str, str] | None:
    if not values:
        print_error("At least one --expect key=value pair is required.")
        return None

    expectations = {}
    for item in values:
        if "=" not in item:
            print_error(f"Invalid expectation: {item}. Use key=value.")
            return None
        key, value = item.split("=", 1)
        expectations[key.strip()] = value.strip()
    return expectations


def _normalize_for_compare(value: Any) -> str:
    if isinstance(value, Decimal):
        return str(value.normalize())
    if isinstance(value, bool):
        return str(value).lower()
    return str(value)


def _write_payload(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix.lower() in {".yaml", ".yml"}:
        path.write_text(yaml.safe_dump(_jsonable(payload), sort_keys=True), encoding="utf-8")
        return
    path.write_text(json.dumps(_jsonable(payload), indent=2, default=str), encoding="utf-8")


def _jsonable(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    return value


def _positive_int(raw: str) -> int:
    value = int(raw)
    if value <= 0:
        raise argparse.ArgumentTypeError("must be positive")
    return value


class _InvalidValue:
    pass
