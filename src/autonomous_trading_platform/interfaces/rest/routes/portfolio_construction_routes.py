# autonomous_trading_platform/interfaces/rest/routes/portfolio_construction_routes.py
"""
REST API for portfolio construction pipeline artifacts — Recommendation 6.5.

Provides read-only endpoints for querying the artifacts produced by the
two-phase portfolio construction pipeline for governance review, replay
debugging, and shadow-mode comparison.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from autonomous_trading_platform.api.dependencies import get_request_id, require_operator_or_admin
from autonomous_trading_platform.api.envelope import SuccessEnvelope, success_response
from autonomous_trading_platform.db import get_session
from autonomous_trading_platform.storage.sor.repositories.portfolio_construction_repository import (
    PortfolioConstructionRepository,
)

router = APIRouter(prefix="/portfolio/construction", tags=["portfolio-construction"])

_session_dep = Depends(get_session)
_actor_dep = Depends(require_operator_or_admin)
_request_id_dep = Depends(get_request_id)


@router.get(
    "/runs",
    response_model=SuccessEnvelope[list[dict[str, Any]]],
    summary="List recent portfolio construction runs",
)
def list_construction_runs(
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    request_id: Annotated[str, Depends(get_request_id)] = "",
    session: Annotated[Session, Depends(get_session)] = None,
    _actor: Annotated[str, Depends(require_operator_or_admin)] = "",
) -> SuccessEnvelope[list[dict[str, Any]]]:
    repo = PortfolioConstructionRepository(session=session)
    rows = repo.list_runs(limit=limit)
    return success_response(
        data=[_run_row_to_dict(r) for r in rows],
        request_id=request_id,
    )


@router.get(
    "/runs/by-run-id/{run_id}",
    response_model=SuccessEnvelope[dict[str, Any]],
    summary="Get the latest construction run diagnostics for a cycle run_id",
)
def get_construction_run_by_run_id(
    run_id: str,
    request_id: Annotated[str, Depends(get_request_id)] = "",
    session: Annotated[Session, Depends(get_session)] = None,
    _actor: Annotated[str, Depends(require_operator_or_admin)] = "",
) -> SuccessEnvelope[dict[str, Any]]:
    repo = PortfolioConstructionRepository(session=session)
    row = repo.get_run(run_id=run_id)
    if row is None:
        from fastapi import HTTPException

        raise HTTPException(
            status_code=404, detail=f"No construction run found for run_id={run_id}"
        )
    return success_response(data=_run_row_to_dict(row), request_id=request_id)


@router.get(
    "/runs/{batch_id}",
    response_model=SuccessEnvelope[dict[str, Any]],
    summary="Get construction run diagnostics by batch_id",
)
def get_construction_run(
    batch_id: str,
    request_id: Annotated[str, Depends(get_request_id)] = "",
    session: Annotated[Session, Depends(get_session)] = None,
    _actor: Annotated[str, Depends(require_operator_or_admin)] = "",
) -> SuccessEnvelope[dict[str, Any]]:
    repo = PortfolioConstructionRepository(session=session)
    row = repo.get_run_by_batch(batch_id=batch_id)
    if row is None:
        from fastapi import HTTPException

        raise HTTPException(
            status_code=404, detail=f"No construction run found for batch_id={batch_id}"
        )
    return success_response(data=_run_row_to_dict(row), request_id=request_id)


@router.get(
    "/runs/{batch_id}/signals",
    response_model=SuccessEnvelope[list[dict[str, Any]]],
    summary="List raw strategy signals for a construction batch",
)
def list_raw_signals(
    batch_id: str,
    request_id: Annotated[str, Depends(get_request_id)] = "",
    session: Annotated[Session, Depends(get_session)] = None,
    _actor: Annotated[str, Depends(require_operator_or_admin)] = "",
) -> SuccessEnvelope[list[dict[str, Any]]]:
    repo = PortfolioConstructionRepository(session=session)
    rows = repo.list_raw_signals(batch_id=batch_id)
    return success_response(
        data=[_batch_item_to_dict(r) for r in rows],
        request_id=request_id,
    )


@router.get(
    "/runs/{batch_id}/netted",
    response_model=SuccessEnvelope[list[dict[str, Any]]],
    summary="List aggregated (netted) portfolio signals for a construction batch",
)
def list_netted_signals(
    batch_id: str,
    request_id: Annotated[str, Depends(get_request_id)] = "",
    session: Annotated[Session, Depends(get_session)] = None,
    _actor: Annotated[str, Depends(require_operator_or_admin)] = "",
) -> SuccessEnvelope[list[dict[str, Any]]]:
    repo = PortfolioConstructionRepository(session=session)
    rows = repo.list_netted_signals(batch_id=batch_id)
    return success_response(
        data=[_netted_signal_to_dict(r) for r in rows],
        request_id=request_id,
    )


@router.get(
    "/runs/{batch_id}/intents",
    response_model=SuccessEnvelope[list[dict[str, Any]]],
    summary="List constraint-gated signal intents for a construction batch",
)
def list_signal_intents(
    batch_id: str,
    constraint_status: Annotated[str | None, Query()] = None,
    request_id: Annotated[str, Depends(get_request_id)] = "",
    session: Annotated[Session, Depends(get_session)] = None,
    _actor: Annotated[str, Depends(require_operator_or_admin)] = "",
) -> SuccessEnvelope[list[dict[str, Any]]]:
    repo = PortfolioConstructionRepository(session=session)
    rows = repo.list_signal_intents(batch_id=batch_id)
    if constraint_status is not None:
        rows = [r for r in rows if r.constraint_status == constraint_status]
    return success_response(
        data=[_signal_intent_to_dict(r) for r in rows],
        request_id=request_id,
    )


@router.get(
    "/runs/{batch_id}/conflicts",
    response_model=SuccessEnvelope[list[dict[str, Any]]],
    summary="List cross-strategy signal conflicts detected for a construction batch",
)
def list_conflicts(
    batch_id: str,
    request_id: Annotated[str, Depends(get_request_id)] = "",
    session: Annotated[Session, Depends(get_session)] = None,
    _actor: Annotated[str, Depends(require_operator_or_admin)] = "",
) -> SuccessEnvelope[list[dict[str, Any]]]:
    repo = PortfolioConstructionRepository(session=session)
    rows = repo.list_conflicts(batch_id=batch_id)
    return success_response(
        data=[_netted_signal_to_dict(r) for r in rows],
        request_id=request_id,
    )


# ------------------------------------------------------------------
# Serializers
# ------------------------------------------------------------------


def _run_row_to_dict(row: Any) -> dict[str, Any]:
    return {
        "run_id": row.run_id,
        "batch_id": row.batch_id,
        "bar_timestamp": row.bar_timestamp.isoformat() if row.bar_timestamp else None,
        "constructed_at": row.constructed_at.isoformat() if row.constructed_at else None,
        "netting_policy": row.netting_policy,
        "strategy_count": row.strategy_count,
        "symbol_count": row.symbol_count,
        "raw_signal_count": row.raw_signal_count,
        "netted_signal_count": row.netted_signal_count,
        "suppressed_count": row.suppressed_count,
        "suppressed_notional_usd": row.suppressed_notional_usd,
        "conflict_count": row.conflict_count,
        "constraint_applied_count": row.constraint_applied_count,
        "constraint_rejected_count": row.constraint_rejected_count,
        "raw_gross_signal_exposure": row.raw_gross_signal_exposure,
        "net_signal_exposure": row.net_signal_exposure,
        "exposure_before_constraints": row.exposure_before_constraints,
        "exposure_after_constraints": row.exposure_after_constraints,
        "final_order_count": row.final_order_count,
        "construction_duration_seconds": row.construction_duration_seconds,
    }


def _batch_item_to_dict(row: Any) -> dict[str, Any]:
    return {
        "batch_id": row.batch_id,
        "run_id": row.run_id,
        "signal_id": row.signal_id,
        "strategy_id": row.strategy_id,
        "symbol": row.symbol,
        "direction": row.direction,
        "confidence": row.confidence,
        "bar_timestamp": row.bar_timestamp.isoformat() if row.bar_timestamp else None,
        "strategy_version": row.strategy_version,
        "feature_snapshot_ref": row.feature_snapshot_ref,
    }


def _netted_signal_to_dict(row: Any) -> dict[str, Any]:
    return {
        "portfolio_signal_id": row.portfolio_signal_id,
        "batch_id": row.batch_id,
        "run_id": row.run_id,
        "symbol": row.symbol,
        "net_direction": row.net_direction,
        "net_confidence": row.net_confidence,
        "conflict_detected": row.conflict_detected,
        "constraint_status": row.constraint_status,
        "netting_policy": row.netting_policy,
        "gross_buy_notional": row.gross_buy_notional,
        "gross_sell_notional": row.gross_sell_notional,
        "net_notional": row.net_notional,
        "contributing_strategy_ids": row.contributing_strategy_ids,
        "attribution": row.attribution_json,
        "bar_timestamp": row.bar_timestamp.isoformat() if row.bar_timestamp else None,
    }


def _signal_intent_to_dict(row: Any) -> dict[str, Any]:
    return {
        "intent_id": row.intent_id,
        "portfolio_signal_id": row.portfolio_signal_id,
        "batch_id": row.batch_id,
        "run_id": row.run_id,
        "symbol": row.symbol,
        "direction": row.direction,
        "confidence": row.confidence,
        "constraint_status": row.constraint_status,
        "constraints_triggered": row.constraints_triggered,
        "attribution": row.attribution_json,
        "bar_timestamp": row.bar_timestamp.isoformat() if row.bar_timestamp else None,
    }
