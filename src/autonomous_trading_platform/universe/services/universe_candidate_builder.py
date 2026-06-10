from __future__ import annotations

import hashlib
import json
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from statistics import stdev
from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from autonomous_trading_platform.contracts.common.enums import (
    BarInterval,
    UniverseSource,
    UniverseStatus,
)
from autonomous_trading_platform.observability.log_context import LogContext
from autonomous_trading_platform.observability.logging import get_logger
from autonomous_trading_platform.observability.metrics import (
    record_universe_candidate_generation_metrics,
)
from autonomous_trading_platform.observability.runtime_context import get_runtime_context
from autonomous_trading_platform.storage.sor.models.market_bars import MarketBar
from autonomous_trading_platform.storage.sor.models.raw_market_pool import RawMarketPoolSnapshot
from autonomous_trading_platform.storage.sor.models.universe_versions import (
    UniverseMember,
    UniverseVersion,
)
from autonomous_trading_platform.universe.types import CandidateGenerationConfig

logger = get_logger(__name__)

_EXPECTED_BARS_PER_US_EQUITY_DAY = 78  # 6.5 trading hours × 12 five-minute bars


class RawMarketPoolQuery(Protocol):
    def get_snapshot_as_of(self, as_of: datetime) -> RawMarketPoolSnapshot | None: ...

    def get_active_tradable_symbols(
        self,
        as_of: datetime | None = None,
        *,
        asset_type: str | None = None,
        exchange: str | None = None,
    ) -> list[str]: ...


@dataclass(frozen=True)
class CandidateLiquidityMetrics:
    last_price: Decimal
    average_daily_dollar_volume: Decimal
    average_daily_volume: Decimal
    median_daily_dollar_volume: Decimal


@dataclass(frozen=True)
class CandidateQualityMetrics:
    history_days: int
    bar_completeness_pct: float
    trading_consistency: float
    daily_return_volatility: float | None
    average_trade_count: float | None


@dataclass
class _SymbolMetrics:
    symbol: str
    liquidity: CandidateLiquidityMetrics
    quality: CandidateQualityMetrics


@dataclass
class CandidateBuildResult:
    version: UniverseVersion
    included_members: list[UniverseMember]
    excluded_members: list[UniverseMember]
    raw_pool_snapshot_id: str | None
    generation_metadata: dict[str, Any]
    diagnostics: dict[str, Any]


class UniverseCandidateBuilder:
    """
    Canonical candidate universe generation service.

    Consumes the raw market pool and internal bar data to produce a ranked,
    reproducible candidate UniverseVersion with per-symbol metric lineage.
    """

    def __init__(
        self,
        session: Session,
        raw_pool_query_service: RawMarketPoolQuery,
        *,
        dataset_version_id: str | None = None,
    ) -> None:
        self.session = session
        self.raw_pool_query_service = raw_pool_query_service
        self.dataset_version_id = dataset_version_id

    def build_candidate(
        self,
        *,
        config: CandidateGenerationConfig,
        name: str,
        source: str = UniverseSource.CUSTOM,
        rebalance_reason: str | None = None,
    ) -> CandidateBuildResult:
        logger.disabled = False
        logger.propagate = True
        config = _normalize_config(config)
        ctx = get_runtime_context()
        base_log = LogContext(
            component="universe_candidate_generation",
            job="candidate_generation",
            correlation_id=ctx.correlation_id if ctx else None,
            job_run_id=ctx.job_run_id if ctx else None,
        ).to_extra()
        logger.info(
            "candidate generation started",
            extra={**base_log, "step": "candidate_generation_started"},
        )

        snapshot = self.raw_pool_query_service.get_snapshot_as_of(config.as_of)
        snapshot_id = snapshot.snapshot_id if snapshot else None

        candidate_symbols = self.raw_pool_query_service.get_active_tradable_symbols(
            config.as_of, asset_type=config.asset_type
        )

        metrics_by_symbol: dict[str, _SymbolMetrics] = {}
        rejected: list[
            tuple[str, str, CandidateLiquidityMetrics | None, CandidateQualityMetrics | None]
        ] = []

        for symbol in candidate_symbols:
            outcome = self._compute_metrics(symbol, config)
            if outcome is None:
                rejected.append((symbol, "no_market_data", None, None))
                logger.info(
                    "universe candidate rejected",
                    extra={
                        **base_log,
                        "step": "rejection_reason",
                        "symbol": symbol,
                        "reason": "no_market_data",
                    },
                )
                continue
            metrics, rejection_reason = outcome
            if rejection_reason is not None:
                rejected.append((symbol, rejection_reason, metrics.liquidity, metrics.quality))
                logger.info(
                    "universe candidate rejected",
                    extra={
                        **base_log,
                        "step": "rejection_reason",
                        "symbol": symbol,
                        "reason": rejection_reason,
                    },
                )
                continue
            metrics_by_symbol[symbol] = metrics

        eligible_symbols = sorted(metrics_by_symbol.keys())
        ranking_started = time.perf_counter()
        scores = self._compute_scores(eligible_symbols, metrics_by_symbol, config)

        ranked = sorted(eligible_symbols, key=lambda s: (-scores[s], s))
        ranking_duration = time.perf_counter() - ranking_started
        included = ranked[: config.max_symbols]
        beyond_max = ranked[config.max_symbols :]

        now_utc = datetime.now(UTC)
        version_id = str(uuid.uuid4())
        config_hash = _compute_config_hash(included, config)

        generation_metadata = _build_generation_metadata(
            config=config,
            snapshot_id=snapshot_id,
            candidate_pool_size=len(candidate_symbols),
            eligible_count=len(eligible_symbols),
            included_count=len(included),
            excluded_count=len(rejected) + len(beyond_max),
            generation_timestamp=now_utc,
        )

        version = UniverseVersion(
            universe_version_id=version_id,
            name=name,
            source=source,
            created_at=now_utc,
            effective_from=config.as_of,
            effective_to=None,
            status=UniverseStatus.CANDIDATE,
            rebalance_reason=rebalance_reason,
            config_hash=config_hash,
            generation_metadata_json=generation_metadata,
        )

        included_members = [
            UniverseMember(
                universe_version_id=version_id,
                symbol=symbol,
                rank=rank,
                score=scores[symbol],
                included_reason=f"ranked_{rank}_of_{len(included)}",
                excluded_reason=None,
                liquidity_metrics_json=_liq_to_dict(metrics_by_symbol[symbol].liquidity),
                quality_metrics_json=_qual_to_dict(metrics_by_symbol[symbol].quality),
            )
            for rank, symbol in enumerate(included, start=1)
        ]

        excluded_members: list[UniverseMember] = []
        for symbol in beyond_max:
            m = metrics_by_symbol[symbol]
            excluded_members.append(
                UniverseMember(
                    universe_version_id=version_id,
                    symbol=symbol,
                    rank=None,
                    score=scores.get(symbol),
                    included_reason=None,
                    excluded_reason="beyond_max_universe_size",
                    liquidity_metrics_json=_liq_to_dict(m.liquidity),
                    quality_metrics_json=_qual_to_dict(m.quality),
                )
            )
        for symbol, reason, liq, qual in rejected:
            excluded_members.append(
                UniverseMember(
                    universe_version_id=version_id,
                    symbol=symbol,
                    rank=None,
                    score=None,
                    included_reason=None,
                    excluded_reason=reason,
                    liquidity_metrics_json=_liq_to_dict(liq) if liq else None,
                    quality_metrics_json=_qual_to_dict(qual) if qual else None,
                )
            )

        diagnostics = _build_diagnostics(
            included=included,
            rejected=rejected,
            beyond_max=beyond_max,
            scores=scores,
            metrics_by_symbol=metrics_by_symbol,
        )

        record_universe_candidate_generation_metrics(
            candidate_count=len(candidate_symbols),
            rejected_count=len(rejected) + len(beyond_max),
            ranking_duration_seconds=ranking_duration,
        )
        logger.info(
            "candidate generation completed",
            extra={
                **base_log,
                "step": "candidate_generation_completed",
                "candidate_universe_version_id": version_id,
                "universe_version_id": version_id,
                "candidate_count": len(candidate_symbols),
                "included_count": len(included),
                "rejected_count": len(rejected) + len(beyond_max),
                "duration_seconds": ranking_duration,
            },
        )
        member_rank = {m.symbol: m.rank for m in included_members}
        for symbol in included[:25]:
            logger.info(
                "universe ranking decision",
                extra={
                    **base_log,
                    "step": "ranking_decision",
                    "candidate_universe_version_id": version_id,
                    "universe_version_id": version_id,
                    "symbol": symbol,
                    "rank": member_rank[symbol],
                    "score": scores[symbol],
                },
            )

        return CandidateBuildResult(
            version=version,
            included_members=included_members,
            excluded_members=excluded_members,
            raw_pool_snapshot_id=snapshot_id,
            generation_metadata=generation_metadata,
            diagnostics=diagnostics,
        )

    def _compute_metrics(
        self,
        symbol: str,
        config: CandidateGenerationConfig,
    ) -> tuple[_SymbolMetrics, str | None] | None:
        window_start = config.as_of - timedelta(days=config.lookback_days * 3)

        if self.dataset_version_id is not None:
            rows = self._fetch_parquet_rows(symbol, window_start, config.as_of)
        else:
            stmt = (
                select(
                    MarketBar.timestamp,
                    MarketBar.close,
                    MarketBar.volume,
                    MarketBar.trade_count,
                )
                .where(
                    MarketBar.symbol == symbol,
                    MarketBar.interval == BarInterval.FIVE_MIN,
                    MarketBar.timestamp <= config.as_of,
                    MarketBar.timestamp >= window_start,
                )
                .order_by(MarketBar.timestamp.asc())
            )
            rows = self.session.execute(stmt).all()

        if not rows:
            return None

        daily_dollar_volume: dict[date, Decimal] = defaultdict(Decimal)
        daily_volume: dict[date, Decimal] = defaultdict(Decimal)
        daily_bar_count: dict[date, int] = defaultdict(int)
        daily_last_close: dict[date, Decimal] = {}
        daily_trade_count_sum: dict[date, int] = defaultdict(int)
        daily_trade_count_bars: dict[date, int] = defaultdict(int)

        for ts, close, volume, trade_count in rows:
            if close is None or volume is None:
                continue
            bar_close = Decimal(str(close))
            bar_volume = Decimal(str(volume))
            trade_date = ts.date()
            daily_dollar_volume[trade_date] += bar_close * bar_volume
            daily_volume[trade_date] += bar_volume
            daily_bar_count[trade_date] += 1
            daily_last_close[trade_date] = bar_close
            if trade_count is not None:
                daily_trade_count_sum[trade_date] += trade_count
                daily_trade_count_bars[trade_date] += 1

        if not daily_last_close:
            return None

        all_dates = sorted(daily_last_close.keys())
        selected_dates = all_dates[-config.lookback_days :]
        history_days = len(selected_dates)

        liq, qual = _compute_liq_qual(
            selected_dates=selected_dates,
            daily_dollar_volume=daily_dollar_volume,
            daily_volume=daily_volume,
            daily_bar_count=daily_bar_count,
            daily_last_close=daily_last_close,
            daily_trade_count_sum=daily_trade_count_sum,
            daily_trade_count_bars=daily_trade_count_bars,
            history_days=history_days,
            lookback_days=config.lookback_days,
        )

        if history_days < config.min_history_days:
            return _SymbolMetrics(
                symbol=symbol, liquidity=liq, quality=qual
            ), "insufficient_history"

        if liq.last_price < config.min_price:
            return _SymbolMetrics(symbol=symbol, liquidity=liq, quality=qual), "price_below_minimum"

        if liq.average_daily_dollar_volume < config.min_average_daily_dollar_volume:
            return _SymbolMetrics(
                symbol=symbol, liquidity=liq, quality=qual
            ), "insufficient_liquidity"

        missing_pct = 1.0 - qual.bar_completeness_pct
        if missing_pct > config.max_missing_bar_pct:
            return _SymbolMetrics(
                symbol=symbol, liquidity=liq, quality=qual
            ), "excessive_missing_bars"

        return _SymbolMetrics(symbol=symbol, liquidity=liq, quality=qual), None

    def _fetch_parquet_rows(
        self,
        symbol: str,
        window_start: datetime,
        as_of: datetime,
    ) -> list[tuple]:
        try:
            from autonomous_trading_platform.storage.parquet.datasets import RAW_BARS_DATASET
            from autonomous_trading_platform.storage.parquet.paths import get_data_root
            from autonomous_trading_platform.storage.parquet.reader import (
                HistoricalBarDatasetReader,
            )

            if self.dataset_version_id is None:
                return []
            reader = HistoricalBarDatasetReader(base_path=get_data_root(), session=self.session)
            table = reader.read(
                dataset=RAW_BARS_DATASET,
                dataset_version=self.dataset_version_id,
                symbol=symbol,
                start_date=window_start.date(),
                end_date=as_of.date(),
                engine="duckdb",
            )
            if table.num_rows == 0:
                return []
            result = []
            as_of_aware = as_of if as_of.tzinfo is not None else as_of.replace(tzinfo=UTC)
            window_start_aware = (
                window_start
                if window_start.tzinfo is not None
                else window_start.replace(tzinfo=UTC)
            )
            for row in table.to_pylist():
                ts = row.get("timestamp")
                if ts is None:
                    continue
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=UTC)
                if ts < window_start_aware or ts > as_of_aware:
                    continue
                result.append((ts, row.get("close"), row.get("volume"), row.get("trade_count")))
            return sorted(result, key=lambda r: r[0])
        except Exception as exc:
            logger.warning(
                "universe_candidate_builder.parquet_fetch_failed",
                extra={
                    "symbol": symbol,
                    "dataset_version_id": self.dataset_version_id,
                    "error": str(exc),
                },
            )
            return []

    def _compute_scores(
        self,
        symbols: list[str],
        metrics_by_symbol: dict[str, _SymbolMetrics],
        config: CandidateGenerationConfig,
    ) -> dict[str, float]:
        if not symbols:
            return {}

        by_addv = sorted(
            symbols,
            key=lambda s: metrics_by_symbol[s].liquidity.average_daily_dollar_volume,
        )
        addv_rank = {s: i for i, s in enumerate(by_addv)}

        symbols_with_vol = [
            s for s in symbols if metrics_by_symbol[s].quality.daily_return_volatility is not None
        ]
        by_vol = sorted(
            symbols_with_vol,
            key=lambda s: _required_volatility(metrics_by_symbol[s]),
        )
        vol_rank: dict[str, int] = {s: i for i, s in enumerate(by_vol)}
        for s in symbols:
            if s not in vol_rank:
                vol_rank[s] = 0

        n = len(symbols)
        addv_denom = max(n - 1, 1)
        vol_denom = max(len(by_vol) - 1, 1) if by_vol else 1

        scores: dict[str, float] = {}
        for symbol in symbols:
            m = metrics_by_symbol[symbol]
            addv_pct = addv_rank[symbol] / addv_denom
            vol_pct = vol_rank[symbol] / vol_denom if by_vol else 0.0
            score = (
                config.weight_dollar_volume * addv_pct
                + config.weight_trading_consistency * m.quality.trading_consistency
                + config.weight_bar_completeness * m.quality.bar_completeness_pct
                - config.weight_volatility_penalty * vol_pct
            )
            scores[symbol] = round(score, 8)

        return scores


def _normalize_config(config: CandidateGenerationConfig) -> CandidateGenerationConfig:
    as_of = config.as_of
    as_of = as_of.replace(tzinfo=UTC) if as_of.tzinfo is None else as_of.astimezone(UTC)
    if as_of is config.as_of:
        return config
    return CandidateGenerationConfig(
        as_of=as_of,
        lookback_days=config.lookback_days,
        min_history_days=config.min_history_days,
        min_price=config.min_price,
        min_average_daily_dollar_volume=config.min_average_daily_dollar_volume,
        max_missing_bar_pct=config.max_missing_bar_pct,
        max_symbols=config.max_symbols,
        asset_type=config.asset_type,
        weight_dollar_volume=config.weight_dollar_volume,
        weight_trading_consistency=config.weight_trading_consistency,
        weight_bar_completeness=config.weight_bar_completeness,
        weight_volatility_penalty=config.weight_volatility_penalty,
    )


def _required_volatility(metrics: _SymbolMetrics) -> float:
    volatility = metrics.quality.daily_return_volatility
    if volatility is None:
        raise ValueError("Expected daily_return_volatility to be populated.")
    return volatility


def _compute_liq_qual(
    *,
    selected_dates: list[date],
    daily_dollar_volume: dict[date, Decimal],
    daily_volume: dict[date, Decimal],
    daily_bar_count: dict[date, int],
    daily_last_close: dict[date, Decimal],
    daily_trade_count_sum: dict[date, int],
    daily_trade_count_bars: dict[date, int],
    history_days: int,
    lookback_days: int,
) -> tuple[CandidateLiquidityMetrics, CandidateQualityMetrics]:
    last_date = selected_dates[-1]
    last_price = daily_last_close[last_date]

    ddv_values = [daily_dollar_volume[d] for d in selected_dates]
    vol_values = [daily_volume[d] for d in selected_dates]
    bar_counts = [daily_bar_count[d] for d in selected_dates]

    addv = _mean_decimal(ddv_values)
    adv = _mean_decimal(vol_values)
    med_ddv = _median_decimal(ddv_values)
    avg_bars = sum(bar_counts) / max(len(bar_counts), 1)
    completeness = min(1.0, avg_bars / _EXPECTED_BARS_PER_US_EQUITY_DAY)
    consistency = history_days / max(lookback_days, 1)

    closes = [daily_last_close[d] for d in selected_dates]
    vol_ann: float | None = None
    if len(closes) >= 3:
        returns = [float(closes[i] / closes[i - 1] - 1) for i in range(1, len(closes))]
        if len(returns) >= 2:
            vol_ann = round(stdev(returns) * (252**0.5), 6)

    days_with_tc = [d for d in selected_dates if daily_trade_count_bars[d] > 0]
    avg_tc: float | None = None
    if days_with_tc:
        avg_tc = round(
            sum(daily_trade_count_sum[d] / daily_trade_count_bars[d] for d in days_with_tc)
            / len(days_with_tc),
            2,
        )

    liq = CandidateLiquidityMetrics(
        last_price=last_price,
        average_daily_dollar_volume=addv,
        average_daily_volume=adv,
        median_daily_dollar_volume=med_ddv,
    )
    qual = CandidateQualityMetrics(
        history_days=history_days,
        bar_completeness_pct=round(completeness, 6),
        trading_consistency=round(consistency, 6),
        daily_return_volatility=vol_ann,
        average_trade_count=avg_tc,
    )
    return liq, qual


def _compute_config_hash(included_symbols: list[str], config: CandidateGenerationConfig) -> str:
    payload = {
        "symbols": sorted(included_symbols),
        "lookback_days": config.lookback_days,
        "min_history_days": config.min_history_days,
        "min_price": str(config.min_price),
        "min_addv": str(config.min_average_daily_dollar_volume),
        "max_missing_bar_pct": config.max_missing_bar_pct,
        "max_symbols": config.max_symbols,
        "asset_type": config.asset_type,
        "weight_dollar_volume": config.weight_dollar_volume,
        "weight_trading_consistency": config.weight_trading_consistency,
        "weight_bar_completeness": config.weight_bar_completeness,
        "weight_volatility_penalty": config.weight_volatility_penalty,
    }
    serialized = json.dumps(payload, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _build_generation_metadata(
    *,
    config: CandidateGenerationConfig,
    snapshot_id: str | None,
    candidate_pool_size: int,
    eligible_count: int,
    included_count: int,
    excluded_count: int,
    generation_timestamp: datetime,
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "as_of": config.as_of.isoformat(),
        "lookback_days": config.lookback_days,
        "min_history_days": config.min_history_days,
        "raw_pool_snapshot_id": snapshot_id,
        "filter_config": {
            "min_price": str(config.min_price),
            "min_average_daily_dollar_volume": str(config.min_average_daily_dollar_volume),
            "max_missing_bar_pct": config.max_missing_bar_pct,
            "max_symbols": config.max_symbols,
            "asset_type": config.asset_type,
        },
        "ranking_config": {
            "weight_dollar_volume": config.weight_dollar_volume,
            "weight_trading_consistency": config.weight_trading_consistency,
            "weight_bar_completeness": config.weight_bar_completeness,
            "weight_volatility_penalty": config.weight_volatility_penalty,
        },
        "candidate_pool_size": candidate_pool_size,
        "eligible_count": eligible_count,
        "included_count": included_count,
        "excluded_count": excluded_count,
        "generation_timestamp": generation_timestamp.isoformat(),
    }


def _build_diagnostics(
    *,
    included: list[str],
    rejected: list[
        tuple[str, str, CandidateLiquidityMetrics | None, CandidateQualityMetrics | None]
    ],
    beyond_max: list[str],
    scores: dict[str, float],
    metrics_by_symbol: dict[str, _SymbolMetrics],
) -> dict[str, Any]:
    rejection_summary: dict[str, int] = defaultdict(int)
    for _, reason, _, _ in rejected:
        rejection_summary[reason] += 1

    included_scores = [scores[s] for s in included]
    score_stats: dict[str, Any] = {}
    if included_scores:
        score_stats = {
            "min": round(min(included_scores), 6),
            "max": round(max(included_scores), 6),
            "mean": round(sum(included_scores) / len(included_scores), 6),
        }

    addv_values = [
        float(metrics_by_symbol[s].liquidity.average_daily_dollar_volume) for s in included
    ]
    addv_stats: dict[str, Any] = {}
    if addv_values:
        addv_stats = {
            "min": round(min(addv_values), 2),
            "max": round(max(addv_values), 2),
            "mean": round(sum(addv_values) / len(addv_values), 2),
        }

    return {
        "included_count": len(included),
        "rejected_count": len(rejected),
        "beyond_max_count": len(beyond_max),
        "rejection_summary": dict(rejection_summary),
        "score_stats": score_stats,
        "addv_stats": addv_stats,
    }


def _mean_decimal(values: list[Decimal]) -> Decimal:
    if not values:
        return Decimal("0")
    return sum(values, Decimal("0")) / Decimal(str(len(values)))


def _median_decimal(values: list[Decimal]) -> Decimal:
    if not values:
        return Decimal("0")
    s = sorted(values)
    n = len(s)
    mid = n // 2
    if n % 2 == 0:
        return (s[mid - 1] + s[mid]) / Decimal("2")
    return s[mid]


def _liq_to_dict(liq: CandidateLiquidityMetrics) -> dict[str, Any]:
    return {
        "last_price": str(liq.last_price),
        "average_daily_dollar_volume": str(liq.average_daily_dollar_volume),
        "average_daily_volume": str(liq.average_daily_volume),
        "median_daily_dollar_volume": str(liq.median_daily_dollar_volume),
    }


def _qual_to_dict(qual: CandidateQualityMetrics) -> dict[str, Any]:
    return {
        "history_days": qual.history_days,
        "bar_completeness_pct": qual.bar_completeness_pct,
        "trading_consistency": qual.trading_consistency,
        "daily_return_volatility": qual.daily_return_volatility,
        "average_trade_count": qual.average_trade_count,
    }
