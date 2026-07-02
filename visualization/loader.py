"""
Artifact loader for PlatformBacktestArtifact JSON bundles.

Parses the output of `atp platform backtest run --output <path>.json`
into clean pandas DataFrames and typed dicts ready for visualization.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd

# ---------------------------------------------------------------------------
# Parsed artifact container
# ---------------------------------------------------------------------------


@dataclass
class ArtifactData:
    # Identity
    replay_id: str
    run_id: str
    fixture_name: str | None
    symbols: list[str]
    start_date: date
    end_date: date
    started_at: str
    completed_at: str

    # Runtime totals
    ticks_attempted: int
    ticks_ok: int
    ticks_failed: int
    total_orders: int
    total_fills: int

    # Final domain summaries (raw dicts, as-is from artifact)
    portfolio_summary: dict[str, Any]
    risk_summary: dict[str, Any]
    governance_summary: dict[str, Any]
    execution_summary: dict[str, Any]
    research_summary: dict[str, Any]
    strategy_catalog: dict[str, Any]
    controls_summary: dict[str, Any]
    settings_summary: dict[str, Any]
    safety_summary: dict[str, Any]
    operations_summary: dict[str, Any]

    # Parsed time-series DataFrame — one row per trading day
    # Columns: date, portfolio_value, cash_balance, open_positions,
    #          drawdown_pct, is_blocked, strategies_in_breach_count,
    #          promotions, demotions, health_transitions,
    #          system_health, has_errors
    tick_df: pd.DataFrame

    # Timeline events applied during the run
    timeline_events: list[dict[str, Any]]

    # Warnings and errors
    warnings: list[str]
    errors: list[str]

    # Raw artifact for any ad-hoc access
    raw: dict[str, Any] = field(repr=False, default_factory=dict)

    # Whether financial data is synthetic (trading not yet wired)
    is_synthetic: bool = False


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------


def load(path: str | Path) -> ArtifactData:
    """
    Load a PlatformBacktestArtifact JSON file and return an ArtifactData.

    Automatically detects flat portfolio values (no real trading) and marks
    is_synthetic=True so callers can augment with synthetic financial data.
    """
    path = Path(path)
    raw = json.loads(path.read_text(encoding="utf-8"))

    tick_df = _build_tick_df(raw.get("tick_results", []))

    portfolio = raw.get("portfolio") or {}
    risk = raw.get("risk") or {}
    governance = raw.get("governance") or {}
    execution = raw.get("execution") or {}
    research = raw.get("research") or {}
    catalog = raw.get("strategy_catalog") or {}
    controls = raw.get("controls") or {}
    settings = raw.get("settings") or {}
    safety = raw.get("safety") or {}
    ops = raw.get("operations") or {}
    runtime = raw.get("runtime") or {}

    # Detect whether real trading happened
    total_orders = runtime.get("total_orders", 0) or 0
    total_fills = runtime.get("total_fills", 0) or 0
    pnl_pct = _safe_float(portfolio.get("total_pnl_pct", "0"))
    is_synthetic = total_orders == 0 and total_fills == 0 and pnl_pct == 0.0

    return ArtifactData(
        replay_id=raw.get("replay_id", ""),
        run_id=raw.get("run_id", ""),
        fixture_name=raw.get("fixture_name"),
        symbols=raw.get("symbols", []),
        start_date=date.fromisoformat(raw["start_date"]),
        end_date=date.fromisoformat(raw["end_date"]),
        started_at=raw.get("started_at", ""),
        completed_at=raw.get("completed_at", ""),
        ticks_attempted=runtime.get("ticks_attempted", 0) or 0,
        ticks_ok=runtime.get("ticks_ok", 0) or 0,
        ticks_failed=runtime.get("ticks_failed", 0) or 0,
        total_orders=total_orders,
        total_fills=total_fills,
        portfolio_summary=portfolio,
        risk_summary=risk,
        governance_summary=governance,
        execution_summary=execution,
        research_summary=research,
        strategy_catalog=catalog,
        controls_summary=controls,
        settings_summary=settings,
        safety_summary=safety,
        operations_summary=ops,
        tick_df=tick_df,
        timeline_events=raw.get("timeline_events_applied") or [],
        warnings=raw.get("warnings") or [],
        errors=raw.get("errors") or [],
        raw=raw,
        is_synthetic=is_synthetic,
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _build_tick_df(tick_results: list[dict]) -> pd.DataFrame:
    rows = []
    for t in tick_results:
        port = t.get("portfolio") or {}
        risk = t.get("risk") or {}
        gov = t.get("governance") or {}
        ops = t.get("operations") or {}

        res = t.get("research") or {}
        rows.append(
            {
                "date": pd.Timestamp(t["tick_date"]),
                "portfolio_value": _safe_float(port.get("portfolio_value")),
                "cash_balance": _safe_float(port.get("cash_balance")),
                "open_positions": int(port.get("open_positions", 0) or 0),
                "equity_curve_points": int(port.get("equity_curve_points", 0) or 0),
                "drawdown_pct": _safe_float(risk.get("drawdown_pct")),
                "is_blocked": bool(risk.get("is_blocked", False)),
                "block_reasons": risk.get("block_reasons") or [],
                "drawdown_ladder_state_count": int(risk.get("drawdown_ladder_state_count", 0) or 0),
                "strategies_evaluated": int(gov.get("strategies_evaluated", 0) or 0),
                "promotions": int(gov.get("promotions_executed", 0) or 0),
                "demotions": int(gov.get("demotions_executed", 0) or 0),
                "health_transitions": int(gov.get("health_transitions", 0) or 0),
                "strategies_in_breach": gov.get("strategies_in_breach") or [],
                "strategies_in_breach_count": len(gov.get("strategies_in_breach") or []),
                "system_health": ops.get("system_health_status", "unknown") or "unknown",
                "active_alerts": int(ops.get("active_alerts_count", 0) or 0),
                "critical_alerts": int(ops.get("critical_alerts_count", 0) or 0),
                "has_errors": len(t.get("errors") or []) > 0,
                "timeline_events_this_tick": len(t.get("timeline_events") or []),
                # Research fields — only populated on months when research fires
                "research_fired": bool(res.get("experiment_id")),
                "research_total_runs": int(res.get("total_runs", 0) or 0)
                if res.get("experiment_id")
                else None,
                "research_stage1": int(res.get("stage_1_survivors", 0) or 0)
                if res.get("experiment_id")
                else None,
                "research_stage2": int(res.get("stage_2_survivors", 0) or 0)
                if res.get("experiment_id")
                else None,
                "research_stage3": int(res.get("stage_3_survivors", 0) or 0)
                if res.get("experiment_id")
                else None,
                "research_deployable": int(res.get("deployable_seeded", 0) or 0)
                if res.get("experiment_id")
                else None,
                "research_top_score": _safe_float(res.get("top_composite_score"))
                if res.get("experiment_id")
                else None,
                "research_cluster_count": int(res.get("cluster_count", 0) or 0)
                if res.get("experiment_id")
                else None,
                "research_regime_diversity": _safe_float(res.get("regime_diversity_score"))
                if res.get("experiment_id")
                else None,
                "research_experiment_id": res.get("experiment_id"),
            }
        )

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    df.set_index("date", inplace=True)
    df.index = pd.DatetimeIndex(df.index)
    df.sort_index(inplace=True)
    return df


def _safe_float(val: Any) -> float | None:
    if val is None:
        return None
    try:
        f = float(str(val))
        return f if f == f else None  # NaN check
    except (ValueError, TypeError):
        return None
