from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from autonomous_trading_platform.contracts.common.enums import UniverseSource, UniverseStatus
from autonomous_trading_platform.observability.log_context import LogContext
from autonomous_trading_platform.observability.logging import get_logger
from autonomous_trading_platform.observability.metrics import record_universe_rebalance_metrics
from autonomous_trading_platform.observability.runtime_context import get_runtime_context
from autonomous_trading_platform.storage.sor.models.rebalance_runs import UniverseRebalanceRun
from autonomous_trading_platform.storage.sor.models.universe_versions import (
    UniverseMember,
    UniverseVersion,
)
from autonomous_trading_platform.storage.sor.repositories.core.universe_version_repository import (
    UniverseVersionRepository,
)
from autonomous_trading_platform.universe.types import UniverseRebalanceConfig

logger = get_logger(__name__)


@dataclass
class RebalanceResult:
    proposed_version: UniverseVersion
    proposed_members: list[UniverseMember]
    rebalance_run: UniverseRebalanceRun
    added: list[str]
    removed: list[str]
    retained: list[str]
    skipped_adds: list[str]
    skipped_removes: list[str]
    churn_pct: float
    audit_summary: dict[str, Any]


class UniverseRebalanceService:
    def __init__(self, session: Session) -> None:
        self._session = session
        self._repo = UniverseVersionRepository(session)

    def propose_rebalance(
        self,
        *,
        active_version_id: str | None,
        candidate_version_id: str,
        config: UniverseRebalanceConfig,
        name: str | None = None,
    ) -> RebalanceResult:
        started = time.perf_counter()
        ctx = get_runtime_context()
        base_log = LogContext(
            component="universe_rebalance",
            job="universe_rebalance",
            candidate_universe_version_id=candidate_version_id,
            active_universe_version_id=active_version_id,
            correlation_id=ctx.correlation_id if ctx else None,
            job_run_id=ctx.job_run_id if ctx else None,
        ).to_extra()
        candidate_version = self._repo.get_by_version_id(candidate_version_id)
        if candidate_version is None:
            raise ValueError(f"Candidate version '{candidate_version_id}' not found.")

        candidate_members = self._repo.get_members(candidate_version_id)

        active_version: UniverseVersion | None = None
        active_members: list[UniverseMember] = []
        if active_version_id is not None:
            active_version = self._repo.get_by_version_id(active_version_id)
            if active_version is None:
                raise ValueError(f"Active version '{active_version_id}' not found.")
            active_members = self._repo.get_included_members(active_version_id)

        result = _compute_rebalance(
            active_version=active_version,
            active_members=active_members,
            candidate_version=candidate_version,
            candidate_members=candidate_members,
            config=config,
            name=name,
        )
        duration = time.perf_counter() - started
        record_universe_rebalance_metrics(
            duration_seconds=duration,
            churn_pct=result.churn_pct,
            added_count=len(result.added),
            removed_count=len(result.removed),
            retained_count=len(result.retained),
        )
        logger.info(
            "universe rebalance decision",
            extra={
                **base_log,
                "step": "rebalance_decision",
                "universe_version_id": result.proposed_version.universe_version_id,
                "rebalance_run_id": result.rebalance_run.rebalance_run_id,
                "added_count": len(result.added),
                "removed_count": len(result.removed),
                "retained_count": len(result.retained),
                "churn_pct": result.churn_pct,
                "duration_seconds": duration,
            },
        )
        return result


def _compute_rebalance(
    *,
    active_version: UniverseVersion | None,
    active_members: list[UniverseMember],
    candidate_version: UniverseVersion,
    candidate_members: list[UniverseMember],
    config: UniverseRebalanceConfig,
    name: str | None,
) -> RebalanceResult:
    now_utc = datetime.now(UTC)

    # Split candidate members into included vs excluded.
    candidate_included: dict[str, UniverseMember] = {}
    candidate_excluded: dict[str, str] = {}
    for m in candidate_members:
        if m.excluded_reason is None:
            candidate_included[m.symbol] = m
        else:
            candidate_excluded[m.symbol] = m.excluded_reason

    active_symbols: set[str] = {m.symbol for m in active_members}

    if active_version is None:
        if not config.allow_empty_active_universe_bootstrap:
            raise ValueError(
                "No active universe provided and allow_empty_active_universe_bootstrap is False."
            )
        return _bootstrap_rebalance(
            candidate_version=candidate_version,
            candidate_included=candidate_included,
            config=config,
            name=name,
            now_utc=now_utc,
        )

    return _hysteresis_rebalance(
        active_version=active_version,
        active_symbols=active_symbols,
        candidate_version=candidate_version,
        candidate_included=candidate_included,
        candidate_excluded=candidate_excluded,
        config=config,
        name=name,
        now_utc=now_utc,
    )


def _bootstrap_rebalance(
    *,
    candidate_version: UniverseVersion,
    candidate_included: dict[str, UniverseMember],
    config: UniverseRebalanceConfig,
    name: str | None,
    now_utc: datetime,
) -> RebalanceResult:
    sorted_symbols = sorted(
        candidate_included.keys(),
        key=lambda s: (candidate_included[s].rank or 99999, s),
    )
    bootstrap_included = sorted_symbols[: config.target_universe_size]

    version_id = str(uuid.uuid4())
    proposed_name = (
        name or f"proposed_bootstrap_{candidate_version.effective_from.date().isoformat()}"
    )
    config_hash = _compute_rebalance_config_hash(bootstrap_included, config)

    generation_metadata: dict[str, Any] = {
        "schema_version": 1,
        "rebalance_type": "bootstrap",
        "previous_universe_version_id": None,
        "candidate_universe_version_id": candidate_version.universe_version_id,
        "rebalance_config": _config_to_dict(config),
        "added_count": len(bootstrap_included),
        "removed_count": 0,
        "retained_count": 0,
        "churn_pct": 1.0,
        "target_universe_size": config.target_universe_size,
        "churn_limited": False,
        "generation_timestamp": now_utc.isoformat(),
    }

    proposed_version = UniverseVersion(
        universe_version_id=version_id,
        name=proposed_name,
        source=UniverseSource.REBALANCE,
        created_at=now_utc,
        effective_from=candidate_version.effective_from,
        effective_to=None,
        status=UniverseStatus.PROPOSED,
        rebalance_reason="bootstrap",
        config_hash=config_hash,
        generation_metadata_json=generation_metadata,
    )

    proposed_members = [
        UniverseMember(
            universe_version_id=version_id,
            symbol=s,
            rank=candidate_included[s].rank,
            score=candidate_included[s].score,
            included_reason="bootstrap",
            excluded_reason=None,
            liquidity_metrics_json=candidate_included[s].liquidity_metrics_json,
            quality_metrics_json=candidate_included[s].quality_metrics_json,
        )
        for s in sorted(bootstrap_included)
    ]

    audit_summary: dict[str, Any] = {
        "previous_universe_size": 0,
        "candidate_universe_size": len(candidate_included),
        "proposed_universe_size": len(bootstrap_included),
        "added_count": len(bootstrap_included),
        "removed_count": 0,
        "retained_count": 0,
        "churn_pct": 1.0,
        "churn_limited": False,
        "top_additions": bootstrap_included[:10],
        "top_removals": [],
        "symbols_retained_by_threshold": [],
        "symbols_retained_by_churn_limit": [],
        "symbols_removed_due_to_exclusion": [],
        "symbols_skipped_add_threshold": [],
        "symbols_skipped_add_churn_limit": [],
        "symbols_skipped_remove_churn_limit": [],
        "symbols_removed_to_target_size": [],
    }

    run_id = str(uuid.uuid4())
    rebalance_run = UniverseRebalanceRun(
        rebalance_run_id=run_id,
        previous_universe_version_id=None,
        candidate_universe_version_id=candidate_version.universe_version_id,
        proposed_universe_version_id=version_id,
        added_symbols_json=bootstrap_included,
        removed_symbols_json=[],
        retained_symbols_json=[],
        churn_pct=1.0,
        target_universe_size=config.target_universe_size,
        max_churn_pct=config.max_churn_pct,
        config_json=_config_to_dict(config),
        summary_json=audit_summary,
        created_at=now_utc,
        status="accepted",
        rejection_reason=None,
    )

    return RebalanceResult(
        proposed_version=proposed_version,
        proposed_members=proposed_members,
        rebalance_run=rebalance_run,
        added=bootstrap_included,
        removed=[],
        retained=[],
        skipped_adds=[],
        skipped_removes=[],
        churn_pct=1.0,
        audit_summary=audit_summary,
    )


def _hysteresis_rebalance(
    *,
    active_version: UniverseVersion,
    active_symbols: set[str],
    candidate_version: UniverseVersion,
    candidate_included: dict[str, UniverseMember],
    candidate_excluded: dict[str, str],
    config: UniverseRebalanceConfig,
    name: str | None,
    now_utc: datetime,
) -> RebalanceResult:
    # Classify active symbols.
    mandatory_removes: dict[str, str] = {}
    optional_removes: dict[str, int | None] = {}  # symbol → candidate rank
    retained_threshold: set[str] = set()

    for symbol in sorted(active_symbols):
        if symbol in candidate_excluded:
            mandatory_removes[symbol] = "excluded_from_candidate"
        elif symbol not in candidate_included:
            mandatory_removes[symbol] = "not_in_candidate_pool"
        else:
            rank = candidate_included[symbol].rank or 99999
            if rank > config.retain_until_rank_greater_than:
                optional_removes[symbol] = candidate_included[symbol].rank
            else:
                retained_threshold.add(symbol)

    # Symbols eligible to add (not currently in active, rank meets add threshold).
    eligible_adds_sorted: list[str] = []
    skipped_add_threshold: list[str] = []
    for symbol, member in sorted(
        candidate_included.items(), key=lambda kv: (kv[1].rank or 99999, kv[0])
    ):
        if symbol in active_symbols:
            continue
        rank = member.rank or 99999
        if rank <= config.add_only_if_rank_less_than_or_equal:
            eligible_adds_sorted.append(symbol)
        else:
            skipped_add_threshold.append(symbol)

    # Sort optional_removes worst-rank-first (highest rank number = lowest quality).
    optional_removes_sorted = sorted(
        optional_removes.keys(),
        key=lambda s: (-(optional_removes[s] or 0), s),
    )

    # Apply churn budget.
    active_size = max(len(active_symbols), 1)

    if config.force_rebalance:
        actual_optional_removes = set(optional_removes.keys())
        actual_adds = set(eligible_adds_sorted)
        skipped_remove_churn: list[str] = []
        skipped_add_churn: list[str] = []
    else:
        max_changes = int(config.max_churn_pct * active_size)
        remaining = max_changes - len(mandatory_removes)
        budget = max(0, remaining)

        actual_optional_removes = set[str]()
        actual_adds = set[str]()
        skipped_remove_churn = []
        skipped_add_churn = []

        for s in optional_removes_sorted:
            if budget > 0:
                actual_optional_removes.add(s)
                budget -= 1
            else:
                skipped_remove_churn.append(s)

        for s in eligible_adds_sorted:
            if budget > 0:
                actual_adds.add(s)
                budget -= 1
            else:
                skipped_add_churn.append(s)

    actual_removes = set(mandatory_removes.keys()) | actual_optional_removes
    retained_by_churn_limit = set(optional_removes.keys()) - actual_optional_removes
    proposed_included = retained_threshold | actual_adds | retained_by_churn_limit

    # Enforce target universe size by removing lowest-ranked symbols.
    removed_to_size: list[str] = []
    if len(proposed_included) > config.target_universe_size:
        ranked = sorted(
            proposed_included,
            key=lambda s: (
                -((candidate_included[s].rank or 0) if s in candidate_included else 0),
                s,
            ),
        )
        cut_count = len(proposed_included) - config.target_universe_size
        removed_to_size = [s for s in ranked[:cut_count]]
        proposed_included -= set(removed_to_size)
        actual_removes |= set(removed_to_size)

    final_added = sorted(s for s in proposed_included if s not in active_symbols)
    final_removed = sorted(actual_removes)
    final_retained = sorted(s for s in proposed_included if s in active_symbols)

    churn_count = len(actual_removes) + len(final_added)
    churn_pct = round(churn_count / active_size, 6)
    churn_limited = not config.force_rebalance and (
        bool(skipped_add_churn) or bool(skipped_remove_churn)
    )

    # Build generation metadata.
    generation_metadata: dict[str, Any] = {
        "schema_version": 1,
        "rebalance_type": "hysteresis",
        "previous_universe_version_id": active_version.universe_version_id,
        "candidate_universe_version_id": candidate_version.universe_version_id,
        "rebalance_config": _config_to_dict(config),
        "added_count": len(final_added),
        "removed_count": len(final_removed),
        "retained_count": len(final_retained),
        "churn_pct": churn_pct,
        "target_universe_size": config.target_universe_size,
        "churn_limited": churn_limited,
        "generation_timestamp": now_utc.isoformat(),
    }

    version_id = str(uuid.uuid4())
    proposed_name = name or f"proposed_{candidate_version.effective_from.date().isoformat()}"
    config_hash = _compute_rebalance_config_hash(sorted(proposed_included), config)

    proposed_version = UniverseVersion(
        universe_version_id=version_id,
        name=proposed_name,
        source=UniverseSource.REBALANCE,
        created_at=now_utc,
        effective_from=candidate_version.effective_from,
        effective_to=None,
        status=UniverseStatus.PROPOSED,
        rebalance_reason="hysteresis_rebalance",
        config_hash=config_hash,
        generation_metadata_json=generation_metadata,
    )

    # Build proposed member rows (included + removed for auditability).
    proposed_members: list[UniverseMember] = []

    for symbol in sorted(proposed_included):
        cand = candidate_included.get(symbol)
        if symbol in retained_threshold:
            reason = "retained_within_threshold"
        elif symbol in retained_by_churn_limit:
            reason = "retained_by_churn_limit"
        else:
            reason = "added_from_candidate"
        proposed_members.append(
            UniverseMember(
                universe_version_id=version_id,
                symbol=symbol,
                rank=cand.rank if cand else None,
                score=cand.score if cand else None,
                included_reason=reason,
                excluded_reason=None,
                liquidity_metrics_json=cand.liquidity_metrics_json if cand else None,
                quality_metrics_json=cand.quality_metrics_json if cand else None,
            )
        )

    for symbol in sorted(actual_removes):
        cand = candidate_included.get(symbol)
        if symbol in mandatory_removes:
            exc_reason = f"removed:{mandatory_removes[symbol]}"
        elif symbol in removed_to_size:
            exc_reason = "removed:target_size_exceeded"
        else:
            exc_reason = "removed:rank_degraded"
        proposed_members.append(
            UniverseMember(
                universe_version_id=version_id,
                symbol=symbol,
                rank=cand.rank if cand else None,
                score=cand.score if cand else None,
                included_reason=None,
                excluded_reason=exc_reason,
                liquidity_metrics_json=cand.liquidity_metrics_json if cand else None,
                quality_metrics_json=cand.quality_metrics_json if cand else None,
            )
        )

    audit_summary: dict[str, Any] = {
        "previous_universe_size": len(active_symbols),
        "candidate_universe_size": len(candidate_included),
        "proposed_universe_size": len(proposed_included),
        "added_count": len(final_added),
        "removed_count": len(final_removed),
        "retained_count": len(final_retained),
        "churn_pct": churn_pct,
        "churn_limited": churn_limited,
        "top_additions": final_added[:10],
        "top_removals": final_removed[:10],
        "symbols_retained_by_threshold": sorted(retained_threshold)[:10],
        "symbols_retained_by_churn_limit": sorted(retained_by_churn_limit)[:10],
        "symbols_removed_due_to_exclusion": sorted(
            s for s in mandatory_removes if s in candidate_excluded
        )[:10],
        "symbols_skipped_add_threshold": sorted(skipped_add_threshold)[:10],
        "symbols_skipped_add_churn_limit": skipped_add_churn[:10],
        "symbols_skipped_remove_churn_limit": skipped_remove_churn[:10],
        "symbols_removed_to_target_size": removed_to_size[:10],
    }

    run_id = str(uuid.uuid4())
    rebalance_run = UniverseRebalanceRun(
        rebalance_run_id=run_id,
        previous_universe_version_id=active_version.universe_version_id,
        candidate_universe_version_id=candidate_version.universe_version_id,
        proposed_universe_version_id=version_id,
        added_symbols_json=final_added,
        removed_symbols_json=final_removed,
        retained_symbols_json=final_retained,
        churn_pct=churn_pct,
        target_universe_size=config.target_universe_size,
        max_churn_pct=config.max_churn_pct,
        config_json=_config_to_dict(config),
        summary_json=audit_summary,
        created_at=now_utc,
        status="accepted",
        rejection_reason=None,
    )

    return RebalanceResult(
        proposed_version=proposed_version,
        proposed_members=proposed_members,
        rebalance_run=rebalance_run,
        added=final_added,
        removed=final_removed,
        retained=final_retained,
        skipped_adds=skipped_add_churn,
        skipped_removes=skipped_remove_churn,
        churn_pct=churn_pct,
        audit_summary=audit_summary,
    )


def _compute_rebalance_config_hash(symbols: list[str], config: UniverseRebalanceConfig) -> str:
    payload = {
        "symbols": sorted(symbols),
        "target_universe_size": config.target_universe_size,
        "max_churn_pct": config.max_churn_pct,
        "retain_until_rank_greater_than": config.retain_until_rank_greater_than,
        "add_only_if_rank_less_than_or_equal": config.add_only_if_rank_less_than_or_equal,
    }
    serialized = json.dumps(payload, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _config_to_dict(config: UniverseRebalanceConfig) -> dict[str, Any]:
    return {
        "target_universe_size": config.target_universe_size,
        "max_churn_pct": config.max_churn_pct,
        "retain_until_rank_greater_than": config.retain_until_rank_greater_than,
        "add_only_if_rank_less_than_or_equal": config.add_only_if_rank_less_than_or_equal,
        "min_candidate_rank_to_include": config.min_candidate_rank_to_include,
        "force_rebalance": config.force_rebalance,
        "allow_empty_active_universe_bootstrap": config.allow_empty_active_universe_bootstrap,
    }
