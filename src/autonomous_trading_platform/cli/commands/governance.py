from __future__ import annotations

import argparse
import json
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import select

from autonomous_trading_platform.application.services.auto_demotion_service import (
    AutoDemotionService,
)
from autonomous_trading_platform.application.services.auto_promotion_service import (
    AutoPromotionService,
)
from autonomous_trading_platform.application.services.governance_audit_service import (
    GovernanceAuditService,
)
from autonomous_trading_platform.application.services.strategy_governance_service import (
    StrategyGovernanceService,
)
from autonomous_trading_platform.application.services.strategy_health_lifecycle_service import (
    StrategyHealthLifecycleService,
)
from autonomous_trading_platform.cli.formatters import print_error, print_header, print_json
from autonomous_trading_platform.db import get_session
from autonomous_trading_platform.storage.sor.models.promotion_rules import PromotionRules
from autonomous_trading_platform.storage.sor.models.strategy_governance import StrategyGovernance
from autonomous_trading_platform.storage.sor.repositories.core.audit_logs_repository import (
    AuditLogRepository,
)
from autonomous_trading_platform.storage.sor.repositories.core.governance_repository import (
    GovernanceRepository,
)
from autonomous_trading_platform.storage.sor.repositories.core.promotion_rules_repository import (
    PromotionRulesRepository,
)
from autonomous_trading_platform.storage.sor.repositories.core.strategy_health_state_repository import (
    StrategyHealthStateRepository,
)
from autonomous_trading_platform.storage.sor.repositories.core.strategy_health_transition_repository import (
    StrategyHealthTransitionRepository,
)

# State alias normalization - mirrors StrategyGovernanceService._STATE_ALIASES
_STATE_ALIASES: dict[str, str] = {
    "research": "approved_research",
    "approved_research": "approved_research",
    "approved_for_research": "approved_research",
    "paper": "approved_for_paper_trading",
    "approved_paper": "approved_for_paper_trading",
    "approved_for_paper_trading": "approved_for_paper_trading",
    "live": "approved_for_live_trading",
    "approved_live": "approved_for_live_trading",
    "approved_for_live_trading": "approved_for_live_trading",
    "retired": "retired",
}

# Valid from/to values for promotion rule configuration
_RULE_STATUS_VALUES = {
    "approved_research",
    "approved_paper",
    "approved_live",
}

_VALID_ACTOR_ROLES = {"researcher", "risk_manager", "system_risk", "operator", "admin"}


def _normalize_state(value: str) -> str | None:
    return _STATE_ALIASES.get(value.strip().lower())


def _governance_row_to_dict(row: StrategyGovernance) -> dict[str, Any]:
    return {
        "strategy_id": row.strategy_id,
        "config_hash": row.config_hash,
        "current_state": row.current_state,
        "experiment_id": row.experiment_id,
        "source_run_id": str(row.source_run_id) if row.source_run_id else None,
        "submitted_by": row.submitted_by,
        "submitted_at": str(row.submitted_at),
        "updated_at": str(row.updated_at),
    }


def _rule_to_dict(rule: PromotionRules) -> dict[str, Any]:
    return {
        "rule_id": rule.rule_id,
        "from_status": rule.from_status,
        "to_status": rule.to_status,
        "min_sharpe": rule.min_sharpe,
        "max_drawdown": rule.max_drawdown,
        "min_days_tested": rule.min_days_tested,
        "min_trade_count": rule.min_trade_count,
        "min_cagr": rule.min_cagr,
        "min_win_rate": rule.min_win_rate,
        "lookback_window_bars": rule.lookback_window_bars,
        "lookback_window_trades": rule.lookback_window_trades,
        "maintenance_min_sharpe": rule.maintenance_min_sharpe,
        "maintenance_min_win_rate": rule.maintenance_min_win_rate,
        "maintenance_max_drawdown": rule.maintenance_max_drawdown,
        "is_active": rule.is_active,
        "notes": rule.notes,
        "created_at": str(rule.created_at),
    }


def _health_state_to_dict(row: Any) -> dict[str, Any]:
    return {
        "strategy_id": row.strategy_id,
        "health_status": row.health_status,
        "previous_health_status": row.previous_health_status,
        "health_reason": row.health_reason,
        "allocation_penalty": float(row.allocation_penalty)
        if row.allocation_penalty is not None
        else 0.0,
        "lifecycle_mode": row.lifecycle_mode,
        "consecutive_critical_count": row.consecutive_critical_count,
        "operator_review_required": bool(row.operator_review_required),
        "suspended_at": str(row.suspended_at) if row.suspended_at else None,
        "suspension_reason": row.suspension_reason,
        "cooldown_expires_at": str(row.cooldown_expires_at) if row.cooldown_expires_at else None,
        "last_health_evaluated_at": str(row.last_health_evaluated_at)
        if row.last_health_evaluated_at
        else None,
        "last_transition_at": str(row.last_transition_at) if row.last_transition_at else None,
        "updated_at": str(row.updated_at) if row.updated_at else None,
    }


def _health_transition_to_dict(row: Any) -> dict[str, Any]:
    return {
        "transition_id": str(row.transition_id) if hasattr(row, "transition_id") else None,
        "strategy_id": row.strategy_id,
        "from_status": row.from_status,
        "to_status": row.to_status,
        "reason": row.reason,
        "triggered_by": row.triggered_by,
        "allocation_penalty": float(row.allocation_penalty)
        if row.allocation_penalty is not None
        else None,
        "run_id": row.run_id,
        "created_at": str(row.created_at) if hasattr(row, "created_at") else None,
    }


def _audit_record_to_dict(record: Any) -> dict[str, Any]:
    if hasattr(record, "model_dump"):
        return {
            k: str(v) if not isinstance(v, (str, int, float, bool, dict, list, type(None))) else v
            for k, v in record.model_dump().items()
        }
    return {
        "governance_audit_id": record.governance_audit_id,
        "strategy_id": record.strategy_id,
        "event_type": record.event_type,
        "trigger_source": record.trigger_source,
        "actor": record.actor,
        "decision_outcome": record.decision_outcome,
        "decision_rationale": record.decision_rationale,
        "recorded_at": str(record.recorded_at),
        "transition_from": record.transition_from,
        "transition_to": record.transition_to,
        "source_run_id": record.source_run_id,
        "promotion_rule_version": record.promotion_rule_version,
        "governance_policy_version": record.governance_policy_version,
    }


# ---------------------------------------------------------------------------
# register
# ---------------------------------------------------------------------------


def register(subparsers) -> None:
    parser = subparsers.add_parser(
        "governance",
        help="Inspect and manage strategy lifecycle governance, promotions, demotions, and audit.",
    )
    gov_sub = parser.add_subparsers(dest="governance_command", required=True)

    # ------------------------------------------------------------------
    # state
    # ------------------------------------------------------------------
    state_parser = gov_sub.add_parser("state", help="Inspect strategy governance state.")
    state_sub = state_parser.add_subparsers(dest="state_command", required=True)

    state_list = state_sub.add_parser(
        "list", help="List governance records, optionally filtered by state."
    )
    state_list.add_argument(
        "--state",
        help=(
            "Filter by lifecycle state. Accepts aliases: research, paper, live, retired, "
            "approved_research, approved_for_paper_trading, approved_for_live_trading."
        ),
    )
    state_list.add_argument("--json", action="store_true")
    state_list.set_defaults(func=handle_state_list)

    state_show = state_sub.add_parser("show", help="Show latest governance state for one strategy.")
    state_show.add_argument("--strategy-id", required=True)
    state_show.add_argument("--json", action="store_true")
    state_show.set_defaults(func=handle_state_show)

    # ------------------------------------------------------------------
    # transition
    # ------------------------------------------------------------------
    transition_parser = gov_sub.add_parser(
        "transition",
        help=(
            "Apply or preview a manual lifecycle transition. "
            "Use --dry-run to validate without writing; --enforce to apply."
        ),
    )
    transition_parser.add_argument("--strategy-id", required=True)
    transition_parser.add_argument(
        "--to-state",
        required=True,
        help="Target state. Accepts aliases: paper, live, research, retired, approved_paper, etc.",
    )
    transition_parser.add_argument("--updated-by", required=True, help="Actor identifier.")
    transition_parser.add_argument(
        "--actor-role",
        required=True,
        choices=sorted(_VALID_ACTOR_ROLES),
        help="Role of the actor performing the transition.",
    )
    transition_parser.add_argument("--reason", required=True)
    transition_parser.add_argument(
        "--source-run-id",
        help="Simulation run ID backing this transition (required for capital-bearing promotions).",
    )
    _add_dry_run_enforce(transition_parser)
    transition_parser.add_argument("--json", action="store_true")
    transition_parser.set_defaults(func=handle_transition)

    # ------------------------------------------------------------------
    # rules
    # ------------------------------------------------------------------
    rules_parser = gov_sub.add_parser("rules", help="Inspect and manage promotion rules.")
    rules_sub = rules_parser.add_subparsers(dest="rules_command", required=True)

    rules_list = rules_sub.add_parser("list", help="List all active promotion rules.")
    rules_list.add_argument("--json", action="store_true")
    rules_list.set_defaults(func=handle_rules_list)

    rules_show = rules_sub.add_parser(
        "show", help="Show promotion rule for a state transition pair."
    )
    rules_show.add_argument(
        "--from",
        dest="from_status",
        required=True,
        help="From status (e.g. approved_research, approved_paper).",
    )
    rules_show.add_argument(
        "--to",
        dest="to_status",
        required=True,
        help="To status (e.g. approved_paper, approved_live).",
    )
    rules_show.add_argument("--json", action="store_true")
    rules_show.set_defaults(func=handle_rules_show)

    rules_validate = rules_sub.add_parser(
        "validate",
        help="Validate a promotion rules config file without persisting.",
    )
    rules_validate.add_argument(
        "--config",
        required=True,
        type=Path,
        help="Path to governance rules YAML (list of rule definitions).",
    )
    rules_validate.set_defaults(func=handle_rules_validate)

    rules_seed = rules_sub.add_parser(
        "seed",
        help="Seed/upsert promotion rules from a YAML config file.",
    )
    rules_seed.add_argument(
        "--config",
        required=True,
        type=Path,
        help="Path to governance rules YAML.",
    )
    _add_dry_run_enforce(rules_seed)
    rules_seed.add_argument("--json", action="store_true")
    rules_seed.set_defaults(func=handle_rules_seed)

    rules_deactivate = rules_sub.add_parser(
        "deactivate",
        help="Deactivate a promotion rule by rule_id.",
    )
    rules_deactivate.add_argument("--rule-id", required=True)
    rules_deactivate.add_argument("--reason", required=True)
    rules_deactivate.add_argument("--enforce", action="store_true", help="Apply the deactivation.")
    rules_deactivate.add_argument("--json", action="store_true")
    rules_deactivate.set_defaults(func=handle_rules_deactivate)

    # ------------------------------------------------------------------
    # promotion
    # ------------------------------------------------------------------
    promo_parser = gov_sub.add_parser("promotion", help="Auto-promotion scan and run.")
    promo_sub = promo_parser.add_subparsers(dest="promotion_command", required=True)

    promo_scan = promo_sub.add_parser(
        "scan",
        help="Show auto-promotion candidates without applying transitions.",
    )
    promo_scan.add_argument("--json", action="store_true")
    promo_scan.set_defaults(func=handle_promotion_scan)

    promo_run = promo_sub.add_parser(
        "run",
        help="Execute auto-promotion. Requires --enforce to apply transitions.",
    )
    promo_run.add_argument(
        "--actor",
        default="governance-cli",
        help="Actor identifier recorded in the audit log.",
    )
    promo_run.add_argument(
        "--enforce",
        action="store_true",
        help="Apply promotion transitions. Without this flag the command reports candidates only.",
    )
    promo_run.add_argument("--json", action="store_true")
    promo_run.set_defaults(func=handle_promotion_run)

    # ------------------------------------------------------------------
    # demotion
    # ------------------------------------------------------------------
    demotion_parser = gov_sub.add_parser("demotion", help="Auto-demotion scan and run.")
    demotion_sub = demotion_parser.add_subparsers(dest="demotion_command", required=True)

    demotion_scan = demotion_sub.add_parser(
        "scan",
        help="Show auto-demotion candidates without applying side effects.",
    )
    demotion_scan.add_argument("--json", action="store_true")
    demotion_scan.set_defaults(func=handle_demotion_scan)

    demotion_run = demotion_sub.add_parser(
        "run",
        help=(
            "Evaluate and optionally apply demotion decisions. "
            "Use --dry-run to emit evidence without state changes; --enforce to apply."
        ),
    )
    demotion_run.add_argument(
        "--actor",
        default="governance-cli",
        help="Actor identifier recorded in the audit log.",
    )
    _add_dry_run_enforce(demotion_run)
    demotion_run.add_argument("--json", action="store_true")
    demotion_run.set_defaults(func=handle_demotion_run)

    # ------------------------------------------------------------------
    # health
    # ------------------------------------------------------------------
    health_parser = gov_sub.add_parser("health", help="Inspect and manage health lifecycle state.")
    health_sub = health_parser.add_subparsers(dest="health_command", required=True)

    health_pending = health_sub.add_parser(
        "pending-review",
        help="List strategies currently requiring operator health review.",
    )
    health_pending.add_argument("--json", action="store_true")
    health_pending.set_defaults(func=handle_health_pending_review)

    health_show = health_sub.add_parser(
        "show",
        help="Show current health lifecycle status for one strategy.",
    )
    health_show.add_argument("--strategy-id", required=True)
    health_show.add_argument("--json", action="store_true")
    health_show.set_defaults(func=handle_health_show)

    health_transitions = health_sub.add_parser(
        "transitions",
        help="List recent health lifecycle transitions for one strategy.",
    )
    health_transitions.add_argument("--strategy-id", required=True)
    health_transitions.add_argument("--limit", type=int, default=20)
    health_transitions.add_argument("--json", action="store_true")
    health_transitions.set_defaults(func=handle_health_transitions)

    health_penalty = health_sub.add_parser(
        "allocation-penalty",
        help="Show current allocation penalty and scalar from health lifecycle.",
    )
    health_penalty.add_argument("--strategy-id", required=True)
    health_penalty.add_argument("--json", action="store_true")
    health_penalty.set_defaults(func=handle_health_allocation_penalty)

    health_run = health_sub.add_parser(
        "run",
        help=(
            "Evaluate health lifecycle for all monitorable strategies. "
            "Use --persist to write state changes; omit for a dry preview."
        ),
    )
    health_run.add_argument(
        "--persist",
        action="store_true",
        help="Persist health lifecycle state transitions.",
    )
    health_run.add_argument(
        "--trigger-source",
        default="cli",
        help="Trigger source label recorded in audit events.",
    )
    health_run.add_argument("--json", action="store_true")
    health_run.set_defaults(func=handle_health_run)

    health_clear = health_sub.add_parser(
        "clear-suspension",
        help="Clear a governance health suspension for a strategy (operator recovery).",
    )
    health_clear.add_argument("--strategy-id", required=True)
    health_clear.add_argument("--actor", required=True, help="Operator identifier.")
    health_clear.add_argument("--reason", required=True)
    health_clear.add_argument("--enforce", action="store_true", help="Apply the suspension clear.")
    health_clear.add_argument("--json", action="store_true")
    health_clear.set_defaults(func=handle_health_clear_suspension)

    # ------------------------------------------------------------------
    # audit
    # ------------------------------------------------------------------
    audit_parser = gov_sub.add_parser("audit", help="Query governance decision evidence.")
    audit_sub = audit_parser.add_subparsers(dest="audit_command", required=True)

    audit_list = audit_sub.add_parser("list", help="List governance audit decisions.")
    audit_list.add_argument("--strategy-id")
    audit_list.add_argument(
        "--event-type", help="Filter by event type (e.g. GOVERNANCE_PROMOTION_APPROVED)."
    )
    audit_list.add_argument("--trigger-source", help="Filter by trigger source.")
    audit_list.add_argument("--decision-outcome", help="Filter by decision outcome.")
    audit_list.add_argument("--page", type=int, default=1)
    audit_list.add_argument("--page-size", type=int, default=50)
    audit_list.add_argument("--json", action="store_true")
    audit_list.set_defaults(func=handle_audit_list)

    audit_show = audit_sub.add_parser("show", help="Show one governance audit decision record.")
    audit_show.add_argument("--governance-audit-id", required=True)
    audit_show.add_argument("--json", action="store_true")
    audit_show.set_defaults(func=handle_audit_show)

    audit_chain = audit_sub.add_parser(
        "chain",
        help="Show the supersession chain for a governance audit decision.",
    )
    audit_chain.add_argument("--governance-audit-id", required=True)
    audit_chain.add_argument("--json", action="store_true")
    audit_chain.set_defaults(func=handle_audit_chain)

    audit_supersede = audit_sub.add_parser(
        "supersede",
        help="Supersede a governance audit decision with a correction.",
    )
    audit_supersede.add_argument("--governance-audit-id", required=True)
    audit_supersede.add_argument("--reason", required=True)
    audit_supersede.add_argument("--actor", required=True)
    audit_supersede.add_argument("--superseded-by", help="ID of the replacement audit event.")
    audit_supersede.add_argument("--enforce", action="store_true", help="Apply the supersession.")
    audit_supersede.add_argument("--json", action="store_true")
    audit_supersede.set_defaults(func=handle_audit_supersede)

    # ------------------------------------------------------------------
    # verify commands (wrapping backtesting handlers)
    # ------------------------------------------------------------------
    verify_promo = gov_sub.add_parser(
        "verify-auto-promotion",
        help=(
            "Run deterministic auto-promotion probes and verify that auto_promote_enabled "
            "gates PromotionRules-based promotion."
        ),
    )
    verify_promo.add_argument(
        "--settings",
        required=True,
        type=Path,
        help="Path to settings YAML fixture.",
    )
    verify_promo.add_argument("--json", action="store_true")
    verify_promo.set_defaults(func=handle_verify_auto_promotion)

    verify_demotion = gov_sub.add_parser(
        "verify-auto-demotion",
        help=(
            "Run deterministic demotion probes and verify that drawdown breaches "
            "change governance state, controls, allocation, and audit records."
        ),
    )
    verify_demotion.add_argument(
        "--settings",
        required=True,
        type=Path,
        help="Path to settings YAML fixture.",
    )
    verify_demotion.add_argument("--json", action="store_true")
    verify_demotion.set_defaults(func=handle_verify_auto_demotion)

    verify_alloc_alias = gov_sub.add_parser(
        "verify-allocation",
        help=(
            "Seed governance, allocation, promotion rules, and metrics fixtures, "
            "then verify which controls are wired to runtime allocation behavior."
        ),
    )
    verify_alloc_alias.add_argument("--controls", required=True, type=Path)
    verify_alloc_alias.add_argument("--settings", required=True, type=Path)
    verify_alloc_alias.add_argument(
        "--total-capital",
        type=Decimal,
        default=Decimal("100000"),
    )
    verify_alloc_alias.set_defaults(func=handle_verify_allocation_source_of_truth)

    verify_alloc = gov_sub.add_parser(
        "verify-allocation-source-of-truth",
        help=(
            "Seed governance, allocation, promotion rules, and metrics fixtures, "
            "then verify which controls are wired to runtime allocation behavior."
        ),
    )
    verify_alloc.add_argument(
        "--controls",
        required=True,
        type=Path,
        help="Path to controls YAML fixture.",
    )
    verify_alloc.add_argument(
        "--settings",
        required=True,
        type=Path,
        help="Path to settings YAML fixture.",
    )
    verify_alloc.add_argument(
        "--total-capital",
        type=Decimal,
        default=Decimal("100000"),
        help="Total capital for PortfolioEngine allocation resolution.",
    )
    verify_alloc.set_defaults(func=handle_verify_allocation_source_of_truth)

    # ------------------------------------------------------------------
    # export
    # ------------------------------------------------------------------
    export_parser = gov_sub.add_parser(
        "export",
        help="Emit a governance evidence bundle for a strategy.",
    )
    export_parser.add_argument("--strategy-id", required=True)
    export_parser.add_argument(
        "--output",
        type=Path,
        help="Output path for the JSON bundle. Defaults to artifacts/governance/<strategy_id>.json.",
    )
    export_parser.set_defaults(func=handle_export)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _add_dry_run_enforce(parser: argparse.ArgumentParser) -> None:
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview the action without writing to the database.",
    )
    group.add_argument(
        "--enforce",
        action="store_true",
        help="Apply the action and persist changes.",
    )


# ---------------------------------------------------------------------------
# state handlers
# ---------------------------------------------------------------------------


def handle_state_list(args: argparse.Namespace) -> int:
    state_filter: str | None = args.state
    normalized: str | None = None
    if state_filter is not None:
        normalized = _normalize_state(state_filter)
        if normalized is None:
            print_error(
                f"Unknown state: '{state_filter}'. Valid aliases: {sorted(_STATE_ALIASES.keys())}"
            )
            return 1

    session = get_session()
    try:
        stmt = select(StrategyGovernance)
        if normalized is not None:
            stmt = stmt.where(StrategyGovernance.current_state == normalized)
        stmt = stmt.order_by(StrategyGovernance.updated_at.desc())
        rows = list(session.scalars(stmt).all())
    except Exception as exc:
        session.rollback()
        print_error(f"Failed to query governance state: {exc}")
        return 1
    finally:
        session.close()

    payload = [_governance_row_to_dict(r) for r in rows]
    if getattr(args, "json", False):
        print_json({"state_filter": normalized, "count": len(payload), "records": payload})
    else:
        print_header("Governance State List")
        if normalized:
            print(f"  Filter: {normalized}")
        print(f"  Total : {len(payload)}")
        print()
        for rec in payload:
            print(
                f"  {rec['strategy_id']:<40} {rec['current_state']:<32} updated={rec['updated_at']}"
            )
    return 0


def handle_state_show(args: argparse.Namespace) -> int:
    strategy_id: str = args.strategy_id

    session = get_session()
    try:
        repo = GovernanceRepository(session)
        row = repo.get_latest_by_strategy(strategy_id)
    except Exception as exc:
        session.rollback()
        print_error(f"Failed to query governance state: {exc}")
        return 1
    finally:
        session.close()

    if row is None:
        print_error(f"No governance record found for strategy: {strategy_id}")
        return 1

    payload = _governance_row_to_dict(row)
    if getattr(args, "json", False):
        print_json(payload)
    else:
        print_header(f"Governance State: {strategy_id}")
        for k, v in payload.items():
            print(f"  {k:<28} {v}")
    return 0


# ---------------------------------------------------------------------------
# transition handler
# ---------------------------------------------------------------------------


def handle_transition(args: argparse.Namespace) -> int:
    strategy_id: str = args.strategy_id
    to_state: str = args.to_state
    updated_by: str = args.updated_by
    actor_role: str = args.actor_role
    reason: str = args.reason
    source_run_id: str | None = args.source_run_id
    dry_run: bool = getattr(args, "dry_run", False)
    enforce: bool = getattr(args, "enforce", False)

    if not dry_run and not enforce:
        print_error("Specify --dry-run to preview or --enforce to apply the transition.")
        return 1

    normalized = _normalize_state(to_state)
    if normalized is None:
        print_error(
            f"Unknown target state: '{to_state}'. Valid aliases: {sorted(_STATE_ALIASES.keys())}"
        )
        return 1

    if dry_run:
        session = get_session()
        try:
            repo = GovernanceRepository(session)
            row = repo.get_latest_by_strategy(strategy_id)
            if row is None:
                print_error(f"No governance record found for strategy: {strategy_id}")
                return 1
            payload: dict[str, Any] = {
                "dry_run": True,
                "strategy_id": strategy_id,
                "from_state": row.current_state,
                "to_state": normalized,
                "updated_by": updated_by,
                "actor_role": actor_role,
                "reason": reason,
                "source_run_id": source_run_id,
                "status": "preview_only",
            }
        except Exception as exc:
            session.rollback()
            print_error(f"Failed to read governance state for preview: {exc}")
            return 1
        finally:
            session.close()

        if getattr(args, "json", False):
            print_json(payload)
        else:
            print_header("Governance Transition Preview (dry-run)")
            for k, v in payload.items():
                print(f"  {k:<24} {v}")
        return 0

    # enforce path
    session = get_session()
    try:
        service = StrategyGovernanceService(session=session)
        result = service.transition(
            strategy_id=strategy_id,
            to_state=normalized,
            reason=reason,
            updated_by=updated_by,
            actor_role=actor_role,
            source_run_id=source_run_id,
        )
    except (LookupError, ValueError, PermissionError) as exc:
        session.rollback()
        print_error(f"Transition rejected: {exc}")
        return 1
    except Exception as exc:
        session.rollback()
        print_error(f"Transition failed: {exc}")
        return 1
    finally:
        session.close()

    payload = {
        "strategy_id": result.strategy_id,
        "from_state": result.from_state,
        "to_state": result.to_state,
        "reason": result.reason,
        "updated_by": result.updated_by,
        "updated_at": str(result.updated_at),
        "status": "transitioned",
    }
    if getattr(args, "json", False):
        print_json(payload)
    else:
        print_header("Governance Transition Applied")
        for k, v in payload.items():
            print(f"  {k:<24} {v}")
    return 0


# ---------------------------------------------------------------------------
# rules handlers
# ---------------------------------------------------------------------------


def handle_rules_list(args: argparse.Namespace) -> int:
    session = get_session()
    try:
        repo = PromotionRulesRepository(session)
        rules = repo.get_all_active()
    except Exception as exc:
        session.rollback()
        print_error(f"Failed to query promotion rules: {exc}")
        return 1
    finally:
        session.close()

    payload = [_rule_to_dict(r) for r in rules]
    if getattr(args, "json", False):
        print_json({"count": len(payload), "rules": payload})
    else:
        print_header("Active Promotion Rules")
        print(f"  Total: {len(payload)}")
        print()
        for r in payload:
            print(f"  {r['rule_id']:<40} {r['from_status']:<24} -> {r['to_status']}")
    return 0


def handle_rules_show(args: argparse.Namespace) -> int:
    from_status: str = args.from_status
    to_status: str = args.to_status

    session = get_session()
    try:
        repo = PromotionRulesRepository(session)
        rule = repo.get_rules_for_transition(from_status=from_status, to_status=to_status)
    except Exception as exc:
        session.rollback()
        print_error(f"Failed to query promotion rule: {exc}")
        return 1
    finally:
        session.close()

    if rule is None:
        print_error(f"No active promotion rule found for {from_status} -> {to_status}")
        return 1

    payload = _rule_to_dict(rule)
    if getattr(args, "json", False):
        print_json(payload)
    else:
        print_header(f"Promotion Rule: {from_status} -> {to_status}")
        for k, v in payload.items():
            print(f"  {k:<36} {v}")
    return 0


def handle_rules_validate(args: argparse.Namespace) -> int:
    config_path: Path = args.config
    if not config_path.exists():
        print_error(f"Config file not found: {config_path}")
        return 1

    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        print_error(f"Failed to parse YAML: {exc}")
        return 1

    rules_raw: list[dict[str, Any]] = raw if isinstance(raw, list) else raw.get("rules", [])
    if not rules_raw:
        print_error("No rules found in config. Expected a list or a dict with a 'rules' key.")
        return 1

    errors: list[str] = []
    seen_transitions: set[tuple[str, str]] = set()
    for i, r in enumerate(rules_raw):
        rule_id = r.get("rule_id", "")
        if not rule_id:
            errors.append(f"Rule[{i}]: missing rule_id")
        from_s = r.get("from_status", "")
        to_s = r.get("to_status", "")
        if from_s not in _RULE_STATUS_VALUES:
            errors.append(
                f"Rule[{i}] '{rule_id}': invalid from_status='{from_s}'. "
                f"Valid: {sorted(_RULE_STATUS_VALUES)}"
            )
        if to_s not in _RULE_STATUS_VALUES:
            errors.append(
                f"Rule[{i}] '{rule_id}': invalid to_status='{to_s}'. "
                f"Valid: {sorted(_RULE_STATUS_VALUES)}"
            )
        if from_s and to_s and from_s == to_s:
            errors.append(f"Rule[{i}] '{rule_id}': from_status and to_status must differ")
        key = (from_s, to_s)
        if key in seen_transitions:
            errors.append(f"Rule[{i}] '{rule_id}': duplicate active transition {from_s} -> {to_s}")
        seen_transitions.add(key)
        # Numeric bounds
        for field in ("min_sharpe", "min_cagr", "min_win_rate"):
            val = r.get(field)
            if val is not None and not isinstance(val, (int, float)):
                errors.append(
                    f"Rule[{i}] '{rule_id}': {field} must be numeric, got {type(val).__name__}"
                )
        for field in ("min_days_tested", "min_trade_count"):
            val = r.get(field)
            if val is not None and (not isinstance(val, int) or val < 0):
                errors.append(f"Rule[{i}] '{rule_id}': {field} must be a non-negative integer")
        max_dd = r.get("max_drawdown")
        if max_dd is not None and not isinstance(max_dd, (int, float)):
            errors.append(f"Rule[{i}] '{rule_id}': max_drawdown must be numeric")

    if errors:
        print_header("Promotion Rules Validation: FAILED")
        for err in errors:
            print_error(err)
        return 1

    print_header("Promotion Rules Validation: PASSED")
    print(f"  Rules validated: {len(rules_raw)}")
    return 0


def handle_rules_seed(args: argparse.Namespace) -> int:
    config_path: Path = args.config
    dry_run: bool = getattr(args, "dry_run", False)
    enforce: bool = getattr(args, "enforce", False)

    if not dry_run and not enforce:
        print_error("Specify --dry-run to preview or --enforce to persist rules.")
        return 1

    if not config_path.exists():
        print_error(f"Config file not found: {config_path}")
        return 1

    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        print_error(f"Failed to parse YAML: {exc}")
        return 1

    rules_raw: list[dict[str, Any]] = raw if isinstance(raw, list) else raw.get("rules", [])
    if not rules_raw:
        print_error("No rules found in config.")
        return 1

    now = datetime.now(UTC)
    proposed: list[dict[str, Any]] = []
    for r in rules_raw:
        proposed.append(
            {
                "rule_id": r.get("rule_id") or str(uuid.uuid4()),
                "from_status": r["from_status"],
                "to_status": r["to_status"],
                "min_sharpe": r.get("min_sharpe"),
                "max_drawdown": r.get("max_drawdown"),
                "min_days_tested": r.get("min_days_tested"),
                "min_trade_count": r.get("min_trade_count"),
                "min_cagr": r.get("min_cagr"),
                "min_win_rate": r.get("min_win_rate"),
                "lookback_window_bars": r.get("lookback_window_bars"),
                "lookback_window_trades": r.get("lookback_window_trades"),
                "maintenance_min_sharpe": r.get("maintenance_min_sharpe"),
                "maintenance_min_win_rate": r.get("maintenance_min_win_rate"),
                "maintenance_max_drawdown": r.get("maintenance_max_drawdown"),
                "is_active": r.get("is_active", True),
                "notes": r.get("notes"),
                "created_at": now,
            }
        )

    if dry_run:
        if getattr(args, "json", False):
            print_json({"dry_run": True, "rules_to_seed": proposed})
        else:
            print_header("Rules Seed Preview (dry-run)")
            print(f"  Rules to insert: {len(proposed)}")
            for p in proposed:
                print(f"  {p['rule_id']:<40} {p['from_status']:<24} -> {p['to_status']}")
        return 0

    session = get_session()
    try:
        repo = PromotionRulesRepository(session)
        for p in proposed:
            existing = repo.get_by_rule_id(p["rule_id"])
            if existing is not None:
                for field, val in p.items():
                    if field != "rule_id":
                        setattr(existing, field, val)
            else:
                repo.insert(PromotionRules(**p))
        session.commit()
    except Exception as exc:
        session.rollback()
        print_error(f"Failed to seed promotion rules: {exc}")
        return 1
    finally:
        session.close()

    if getattr(args, "json", False):
        print_json({"seeded": len(proposed), "rules": [p["rule_id"] for p in proposed]})
    else:
        print_header("Promotion Rules Seeded")
        print(f"  Seeded: {len(proposed)}")
        for p in proposed:
            print(f"  {p['rule_id']}")
    return 0


def handle_rules_deactivate(args: argparse.Namespace) -> int:
    rule_id: str = args.rule_id
    reason: str = args.reason
    enforce: bool = getattr(args, "enforce", False)

    if not enforce:
        payload = {
            "rule_id": rule_id,
            "reason": reason,
            "enforced": False,
            "status": "preview_only",
        }
        if getattr(args, "json", False):
            print_json(payload)
        else:
            print_header(f"Deactivate Rule Preview: {rule_id}")
            print(f"  rule_id: {rule_id}")
            print(f"  reason : {reason}")
            print("  (dry-run: pass --enforce to apply)")
        return 0

    session = get_session()
    try:
        repo = PromotionRulesRepository(session)
        existing = repo.get_by_rule_id(rule_id)
        if existing is None:
            print_error(f"Promotion rule not found: {rule_id}")
            return 1
        repo.deactivate(rule_id)
        AuditLogRepository(session).record_operator_action(
            action="PROMOTION_RULE_DEACTIVATED",
            actor="governance-cli",
            reason=reason,
            occurred_at=datetime.now(UTC),
            component="governance",
            metadata={
                "rule_id": rule_id,
                "from_status": existing.from_status,
                "to_status": existing.to_status,
            },
        )
        session.commit()
    except Exception as exc:
        session.rollback()
        print_error(f"Failed to deactivate rule: {exc}")
        return 1
    finally:
        session.close()

    payload = {"rule_id": rule_id, "reason": reason, "status": "deactivated"}
    if getattr(args, "json", False):
        print_json(payload)
    else:
        print_header("Promotion Rule Deactivated")
        for k, v in payload.items():
            print(f"  {k:<16} {v}")
    return 0


# ---------------------------------------------------------------------------
# promotion handlers
# ---------------------------------------------------------------------------


def handle_promotion_scan(args: argparse.Namespace) -> int:
    session = get_session()
    try:
        service = AutoPromotionService(session=session)
        candidates = service.scan()
    except Exception as exc:
        session.rollback()
        print_error(f"Promotion scan failed: {exc}")
        return 1
    finally:
        session.close()

    payload: list[dict[str, Any]] = [
        {
            "strategy_id": c.strategy_id,
            "from_state": c.from_state,
            "to_state": c.to_state,
            "eligible": c.eligible,
            "status": c.status,
            "rule_id": c.rule_id,
            "reasons": c.reasons,
            "source_run_id": c.source_run_id,
        }
        for c in candidates
    ]
    if getattr(args, "json", False):
        print_json({"candidate_count": len(payload), "candidates": payload})
    else:
        print_header("Auto-Promotion Scan")
        print(f"  Candidates: {len(payload)}")
        print()
        for c in payload:
            mark = "[ELIGIBLE]" if c["eligible"] else "[skipped] "
            print(
                f"  {mark} {c['strategy_id']:<40} {c['from_state']:<32} -> {c['to_state'] or 'n/a'}"
            )
    return 0


def handle_promotion_run(args: argparse.Namespace) -> int:
    actor: str = args.actor
    enforce: bool = getattr(args, "enforce", False)

    if not enforce:
        payload = {
            "enforced": False,
            "status": "preview_only",
            "actor": actor,
            "promotions_executed": [],
            "note": "Pass --enforce to apply promotion transitions.",
        }
        if getattr(args, "json", False):
            print_json(payload)
        else:
            print_header("Auto-Promotion Run (no --enforce)")
            print("  Pass --enforce to apply promotion transitions.")
            print("  Use 'atp governance promotion scan' to preview candidates first.")
        return 0

    session = get_session()
    try:
        service = AutoPromotionService(session=session)
        result = service.run(actor=actor, run_id=str(uuid.uuid4()))
    except Exception as exc:
        session.rollback()
        print_error(f"Auto-promotion run failed: {exc}")
        return 1
    finally:
        session.close()

    payload = AutoPromotionService.result_to_jsonable(result)
    if getattr(args, "json", False):
        print_json(payload)
    else:
        print_header("Auto-Promotion Run")
        print(f"  auto_promote_enabled : {result.auto_promote_enabled}")
        print(f"  skipped_reason       : {result.skipped_reason}")
        print(f"  promotions_executed  : {len(result.promotions_executed)}")
        for p in result.promotions_executed:
            print(
                f"    {p['strategy_id']:<40} {p.get('from_state', '?'):<24} -> {p.get('to_state', '?')}  [{p.get('status', '?')}]"
            )
    return 0


# ---------------------------------------------------------------------------
# demotion handlers
# ---------------------------------------------------------------------------


def handle_demotion_scan(args: argparse.Namespace) -> int:
    session = get_session()
    try:
        service = AutoDemotionService(session=session)
        candidates = service.scan()
    except Exception as exc:
        session.rollback()
        print_error(f"Demotion scan failed: {exc}")
        return 1
    finally:
        session.close()

    payload: list[dict[str, Any]] = [
        {
            "strategy_id": c.strategy_id,
            "old_state": c.old_state,
            "recommended_state": c.recommended_state,
            "breach": c.breach,
            "breached_metric": c.breached_metric,
            "breached_value": c.breached_value,
            "threshold": c.threshold,
            "should_disable": c.should_disable,
            "should_zero_allocation": c.should_zero_allocation,
            "should_freeze": c.should_freeze,
            "status": c.status,
            "reasons": c.reasons,
        }
        for c in candidates
    ]
    if getattr(args, "json", False):
        print_json({"candidate_count": len(payload), "candidates": payload})
    else:
        print_header("Auto-Demotion Scan")
        print(f"  Candidates: {len(payload)}")
        print()
        for c in payload:
            mark = "[BREACH]  " if c["breach"] else "[ok]      "
            recommended_state = str(c["recommended_state"] or "n/a")
            print(
                f"  {mark} {c['strategy_id']:<40} {c['old_state']:<32} "
                f"{'-> ' + recommended_state if c['breach'] else ''}"
            )
    return 0


def handle_demotion_run(args: argparse.Namespace) -> int:
    actor: str = args.actor
    dry_run: bool = getattr(args, "dry_run", False)
    enforce: bool = getattr(args, "enforce", False)

    if not dry_run and not enforce:
        print_error("Specify --dry-run or --enforce.")
        return 1

    session = get_session()
    try:
        service = AutoDemotionService(session=session)
        result = service.run(
            actor=actor,
            run_id=str(uuid.uuid4()),
            dry_run=dry_run,
        )
    except Exception as exc:
        session.rollback()
        print_error(f"Auto-demotion run failed: {exc}")
        return 1
    finally:
        session.close()

    payload = AutoDemotionService.result_to_jsonable(result)
    if getattr(args, "json", False):
        print_json(payload)
    else:
        print_header(f"Auto-Demotion Run {'(dry-run)' if dry_run else '(enforce)'}")
        print(f"  auto_demote_on_breach: {result.auto_demote_on_breach}")
        print(f"  skipped_reason       : {result.skipped_reason}")
        print(f"  demotions_executed   : {len(result.demotions_executed)}")
        for d in result.demotions_executed:
            print(
                f"    {d['strategy_id']:<40} "
                f"{d.get('from_state', '?'):<24} -> {d.get('to_state', '?')}  [{d.get('status', '?')}]"
            )
    return 0


# ---------------------------------------------------------------------------
# health handlers
# ---------------------------------------------------------------------------


def handle_health_pending_review(args: argparse.Namespace) -> int:
    session = get_session()
    try:
        repo = StrategyHealthStateRepository(session)
        rows = repo.get_pending_operator_review()
    except Exception as exc:
        session.rollback()
        print_error(f"Failed to query pending health reviews: {exc}")
        return 1
    finally:
        session.close()

    payload = [_health_state_to_dict(r) for r in rows]
    if getattr(args, "json", False):
        print_json({"pending_review_count": len(payload), "records": payload})
    else:
        print_header("Health Pending Operator Review")
        print(f"  Pending: {len(payload)}")
        print()
        for r in payload:
            print(
                f"  {r['strategy_id']:<40} {r['health_status']:<16} suspended_at={r['suspended_at']}"
            )
    return 0


def handle_health_show(args: argparse.Namespace) -> int:
    strategy_id: str = args.strategy_id

    session = get_session()
    try:
        repo = StrategyHealthStateRepository(session)
        row = repo.get_for_strategy(strategy_id)
    except Exception as exc:
        session.rollback()
        print_error(f"Failed to query health state: {exc}")
        return 1
    finally:
        session.close()

    if row is None:
        print_error(f"No health state record found for strategy: {strategy_id}")
        return 1

    payload = _health_state_to_dict(row)
    if getattr(args, "json", False):
        print_json(payload)
    else:
        print_header(f"Health State: {strategy_id}")
        for k, v in payload.items():
            print(f"  {k:<40} {v}")
    return 0


def handle_health_transitions(args: argparse.Namespace) -> int:
    strategy_id: str = args.strategy_id
    limit: int = args.limit

    session = get_session()
    try:
        repo = StrategyHealthTransitionRepository(session)
        rows = repo.get_recent_for_strategy(strategy_id, limit=limit)
    except Exception as exc:
        session.rollback()
        print_error(f"Failed to query health transitions: {exc}")
        return 1
    finally:
        session.close()

    payload = [_health_transition_to_dict(r) for r in rows]
    if getattr(args, "json", False):
        print_json({"strategy_id": strategy_id, "count": len(payload), "transitions": payload})
    else:
        print_header(f"Health Transitions: {strategy_id}")
        print(f"  Total (limit={limit}): {len(payload)}")
        print()
        for t in payload:
            print(
                f"  {t.get('created_at', '?')!s:<28} "
                f"{t['from_status'] or 'none':<16} -> {t['to_status']:<16} {t['reason']}"
            )
    return 0


def handle_health_allocation_penalty(args: argparse.Namespace) -> int:
    strategy_id: str = args.strategy_id

    session = get_session()
    try:
        service = StrategyHealthLifecycleService(session=session)
        penalty = service.get_allocation_penalty(strategy_id)
        scalar = service.get_allocation_scalar(strategy_id)
    except Exception as exc:
        session.rollback()
        print_error(f"Failed to compute allocation penalty: {exc}")
        return 1
    finally:
        session.close()

    payload = {
        "strategy_id": strategy_id,
        "allocation_penalty": penalty,
        "allocation_scalar": scalar,
    }
    if getattr(args, "json", False):
        print_json(payload)
    else:
        print_header(f"Health Allocation Penalty: {strategy_id}")
        print(f"  allocation_penalty : {penalty:.4f}  ({penalty * 100:.1f}% reduction)")
        print(f"  allocation_scalar  : {scalar:.4f}  ({scalar * 100:.1f}% of target)")
    return 0


def handle_health_run(args: argparse.Namespace) -> int:
    persist: bool = args.persist
    trigger_source: str = args.trigger_source

    if not persist:
        preview_payload = {
            "persisted": False,
            "status": "preview_only",
            "trigger_source": trigger_source,
            "note": "Pass --persist to evaluate and write health state transitions.",
        }
        if getattr(args, "json", False):
            print_json(preview_payload)
        else:
            print_header("Health Lifecycle Run (preview)")
            print("  Pass --persist to evaluate and write health state transitions.")
            print("  Use 'atp governance health pending-review' to see strategies awaiting review.")
        return 0

    session = get_session()
    try:
        service = StrategyHealthLifecycleService(session=session)
        result = service.run(run_id=str(uuid.uuid4()))
    except Exception as exc:
        session.rollback()
        print_error(f"Health lifecycle run failed: {exc}")
        return 1
    finally:
        session.close()

    run_payload: dict[str, Any] = {
        "run_id": result.run_id,
        "lifecycle_enabled": result.lifecycle_enabled,
        "mode": result.mode,
        "strategies_evaluated": result.strategies_evaluated,
        "transitions_count": len(result.transitions),
        "alerts_emitted": result.alerts_emitted,
        "suspensions": result.suspensions,
        "skipped_count": result.skipped_count,
        "transitions": result.transitions,
        "trigger_source": trigger_source,
    }
    if getattr(args, "json", False):
        print_json(run_payload)
    else:
        print_header("Health Lifecycle Run")
        print(f"  mode                 : {result.mode}")
        print(f"  lifecycle_enabled    : {result.lifecycle_enabled}")
        print(f"  strategies_evaluated : {result.strategies_evaluated}")
        print(f"  transitions          : {len(result.transitions)}")
        print(f"  alerts_emitted       : {result.alerts_emitted}")
        print(f"  suspensions          : {result.suspensions}")
        if result.transitions:
            print()
            for t in result.transitions:
                print(
                    f"  {t['strategy_id']:<40} {t.get('from_status', '?'):<16} -> {t.get('to_status', '?')}"
                )
    return 0


def handle_health_clear_suspension(args: argparse.Namespace) -> int:
    strategy_id: str = args.strategy_id
    actor: str = args.actor
    reason: str = args.reason
    enforce: bool = getattr(args, "enforce", False)

    if not enforce:
        print_header(f"Clear Suspension Preview: {strategy_id}")
        print(f"  strategy_id : {strategy_id}")
        print(f"  actor       : {actor}")
        print(f"  reason      : {reason}")
        print("  (dry-run: pass --enforce to apply)")
        return 0

    session = get_session()
    try:
        service = StrategyHealthLifecycleService(session=session)
        result = service.clear_suspension(strategy_id, actor=actor, reason=reason)
    except ValueError as exc:
        session.rollback()
        print_error(f"Cannot clear suspension: {exc}")
        return 1
    except Exception as exc:
        session.rollback()
        print_error(f"Failed to clear suspension: {exc}")
        return 1
    finally:
        session.close()

    payload = {
        "strategy_id": result.strategy_id,
        "previous_health_status": result.previous_health_status,
        "new_health_status": result.new_health_status,
        "allocation_penalty": result.allocation_penalty,
        "allocation_scalar": result.allocation_scalar,
        "did_transition": result.did_transition,
        "status": "suspension_cleared",
    }
    if getattr(args, "json", False):
        print_json(payload)
    else:
        print_header(f"Suspension Cleared: {strategy_id}")
        for k, v in payload.items():
            print(f"  {k:<32} {v}")
    return 0


# ---------------------------------------------------------------------------
# audit handlers
# ---------------------------------------------------------------------------


def handle_audit_list(args: argparse.Namespace) -> int:
    strategy_id: str | None = args.strategy_id
    event_type: str | None = args.event_type
    trigger_source: str | None = args.trigger_source
    decision_outcome: str | None = args.decision_outcome
    page: int = args.page
    page_size: int = args.page_size

    session = get_session()
    try:
        service = GovernanceAuditService(session=session)
        result = service.list_decisions(
            page=page,
            page_size=page_size,
            strategy_id=strategy_id,
            trigger_source=trigger_source,
            event_type=event_type,
            decision_outcome=decision_outcome,
        )
    except Exception as exc:
        session.rollback()
        print_error(f"Failed to list audit decisions: {exc}")
        return 1
    finally:
        session.close()

    records = [_audit_record_to_dict(r) for r in result.records]
    if getattr(args, "json", False):
        print_json(
            {
                "total": result.total,
                "page": result.page,
                "page_size": result.page_size,
                "count": len(records),
                "records": records,
            }
        )
    else:
        print_header("Governance Audit List")
        print(
            f"  Total : {result.total}   Page {result.page}/{(result.total + page_size - 1) // page_size}"
        )
        print()
        for r in records:
            print(
                f"  {str(r.get('governance_audit_id', '?'))[:36]:<38} "
                f"{str(r.get('event_type', '?')):<42} "
                f"{str(r.get('strategy_id', '?'))}"
            )
    return 0


def handle_audit_show(args: argparse.Namespace) -> int:
    governance_audit_id: str = args.governance_audit_id

    session = get_session()
    try:
        service = GovernanceAuditService(session=session)
        record = service.get_decision(governance_audit_id)
    except Exception as exc:
        session.rollback()
        print_error(f"Failed to retrieve audit decision: {exc}")
        return 1
    finally:
        session.close()

    if record is None:
        print_error(f"No governance audit record found: {governance_audit_id}")
        return 1

    payload = _audit_record_to_dict(record)
    if getattr(args, "json", False):
        print_json(payload)
    else:
        print_header(f"Governance Audit: {governance_audit_id}")
        for k, v in payload.items():
            print(f"  {k:<36} {v}")
    return 0


def handle_audit_chain(args: argparse.Namespace) -> int:
    governance_audit_id: str = args.governance_audit_id

    session = get_session()
    try:
        service = GovernanceAuditService(session=session)
        chain = service.get_supersession_chain(governance_audit_id)
    except Exception as exc:
        session.rollback()
        print_error(f"Failed to retrieve supersession chain: {exc}")
        return 1
    finally:
        session.close()

    records = [_audit_record_to_dict(r) for r in chain]
    if getattr(args, "json", False):
        print_json(
            {
                "governance_audit_id": governance_audit_id,
                "chain_length": len(records),
                "chain": records,
            }
        )
    else:
        print_header(f"Audit Supersession Chain: {governance_audit_id}")
        print(f"  Chain length: {len(records)}")
        print()
        for i, r in enumerate(records):
            print(
                f"  [{i}] {str(r.get('governance_audit_id', '?'))[:36]:<38} {r.get('event_type', '?')}"
            )
    return 0


def handle_audit_supersede(args: argparse.Namespace) -> int:
    governance_audit_id: str = args.governance_audit_id
    reason: str = args.reason
    actor: str = args.actor
    superseded_by: str = getattr(args, "superseded_by", None) or str(uuid.uuid4())
    enforce: bool = getattr(args, "enforce", False)

    if not enforce:
        payload = {
            "governance_audit_id": governance_audit_id,
            "superseded_by": superseded_by,
            "actor": actor,
            "reason": reason,
            "enforced": False,
            "status": "preview_only",
        }
        if getattr(args, "json", False):
            print_json(payload)
        else:
            print_header(f"Supersede Audit Preview: {governance_audit_id}")
            print(f"  governance_audit_id : {governance_audit_id}")
            print(f"  superseded_by       : {superseded_by}")
            print(f"  actor               : {actor}")
            print(f"  reason              : {reason}")
            print("  (dry-run: pass --enforce to apply)")
        return 0

    session = get_session()
    try:
        service = GovernanceAuditService(session=session)
        row = service.supersede(
            governance_audit_id=governance_audit_id,
            superseded_by=superseded_by,
            actor=actor,
            reason=reason,
        )
        session.commit()
    except Exception as exc:
        session.rollback()
        print_error(f"Failed to supersede audit decision: {exc}")
        return 1
    finally:
        session.close()

    if row is None:
        print_error(f"Audit record not found or supersession failed: {governance_audit_id}")
        return 1

    payload = {
        "governance_audit_id": governance_audit_id,
        "superseded_by": superseded_by,
        "actor": actor,
        "reason": reason,
        "status": "superseded",
    }
    if getattr(args, "json", False):
        print_json(payload)
    else:
        print_header("Audit Decision Superseded")
        for k, v in payload.items():
            print(f"  {k:<28} {v}")
    return 0


# ---------------------------------------------------------------------------
# verify handlers (delegate to backtesting)
# ---------------------------------------------------------------------------


def handle_verify_auto_promotion(args: argparse.Namespace) -> int:
    from autonomous_trading_platform.cli.commands.backtesting import (
        handle_verify_auto_promotion as _backtesting_handler,
    )

    return _backtesting_handler(args)


def handle_verify_auto_demotion(args: argparse.Namespace) -> int:
    from autonomous_trading_platform.cli.commands.backtesting import (
        handle_verify_auto_demotion as _backtesting_handler,
    )

    return _backtesting_handler(args)


def handle_verify_allocation_source_of_truth(args: argparse.Namespace) -> int:
    from autonomous_trading_platform.cli.commands.backtesting import (
        handle_verify_governance_allocation as _backtesting_handler,
    )

    return _backtesting_handler(args)


# ---------------------------------------------------------------------------
# export handler
# ---------------------------------------------------------------------------


def handle_export(args: argparse.Namespace) -> int:
    strategy_id: str = args.strategy_id
    output: Path | None = args.output

    if output is None:
        output = Path("artifacts/governance") / f"{strategy_id}.json"

    session = get_session()
    try:
        gov_repo = GovernanceRepository(session)
        gov_row = gov_repo.get_latest_by_strategy(strategy_id)

        health_repo = StrategyHealthStateRepository(session)
        health_row = health_repo.get_for_strategy(strategy_id)

        transition_repo = StrategyHealthTransitionRepository(session)
        health_transitions = transition_repo.get_recent_for_strategy(strategy_id, limit=50)

        audit_service = GovernanceAuditService(session=session)
        audit_result = audit_service.list_decisions(strategy_id=strategy_id, page=1, page_size=100)
    except Exception as exc:
        session.rollback()
        print_error(f"Failed to build governance export: {exc}")
        return 1
    finally:
        session.close()

    bundle: dict[str, Any] = {
        "export_id": str(uuid.uuid4()),
        "strategy_id": strategy_id,
        "exported_at": datetime.now(UTC).isoformat(),
        "governance_state": _governance_row_to_dict(gov_row) if gov_row else None,
        "health_state": _health_state_to_dict(health_row) if health_row else None,
        "health_transitions": [_health_transition_to_dict(r) for r in health_transitions],
        "audit_decisions": {
            "total": audit_result.total,
            "records": [_audit_record_to_dict(r) for r in audit_result.records],
        },
    }

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(bundle, indent=2, default=str), encoding="utf-8")
    print(f"Governance bundle saved: {output}")
    return 0
