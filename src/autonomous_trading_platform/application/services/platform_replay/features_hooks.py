"""Features domain replay hook."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any  # noqa: F401 — used for base dict annotation

from sqlalchemy.orm import Session

from autonomous_trading_platform.contracts.common.enums import PriceBasis
from autonomous_trading_platform.contracts.runtime.platform_replay import (
    FeatureReplayResult,
    FeatureSummary,
    PlatformReplayContext,
)
from autonomous_trading_platform.scheduler.cycles.run_feature_pipeline_cycle import (
    run_feature_pipeline_cycle,
)
from autonomous_trading_platform.storage.sor.repositories.core.dataset_versions_repository import (
    DatasetVersionsRepository,
)
from autonomous_trading_platform.storage.sor.repositories.core.feature_dataset_versions_repository import (
    FeatureDatasetVersionsRepository,
)


def run_features_at_timestamp(
    *,
    session: Session,
    timestamp: datetime,
    dataset_version_id: str | None = None,
    symbols: list[str] | None = None,
    replay_context: PlatformReplayContext,
    price_basis: PriceBasis = PriceBasis.ADJUSTED,
    dry_run: bool = False,
) -> FeatureReplayResult:
    """Run the feature pipeline for the given timestamp.

    The platform runner must supply dataset_version_id (resolved from ingestion
    result) or this hook resolves the latest validated version automatically.
    """
    base = dict(
        domain="features",
        timestamp=timestamp,
        run_id=str(replay_context.run_id),
    )

    if dry_run or replay_context.dry_run:
        return FeatureReplayResult(
            **base,
            status="dry_run",
            summary={"dry_run": True, "timestamp": timestamp.isoformat()},
        )

    effective_symbols = symbols or replay_context.symbols
    if not effective_symbols:
        return FeatureReplayResult(
            **base,
            status="skipped",
            warnings=["No symbols provided — feature pipeline skipped"],
        )

    # Resolve dataset version from latest validated raw/adjusted bars if not given
    if not dataset_version_id:
        repo = DatasetVersionsRepository(session)
        ds = repo.get_latest_validated(
            dataset_name="adjusted_bars" if price_basis == PriceBasis.ADJUSTED else "raw_bars",
            price_basis=price_basis,
        )
        if ds is None:
            return FeatureReplayResult(
                **base,
                status="skipped",
                warnings=["No validated dataset version found — feature pipeline skipped"],
            )
        dataset_version_id = ds.dataset_version_id

    ts_date: date = timestamp.date()
    warnings: list[str] = []

    try:
        result: dict[str, Any] = run_feature_pipeline_cycle(
            price_basis=price_basis,
            dataset_version_id=dataset_version_id,
            symbols=effective_symbols,
            universe_version_id=None,
            start_date=ts_date,
            end_date=ts_date,
            include_returns=True,
            include_volatility=True,
            include_moving_average=True,
            include_liquidity=True,
            include_regime=True,
            include_regime_classification=True,
        )
    except Exception as exc:
        return FeatureReplayResult(
            **base,
            status="failed",
            errors=[str(exc)],
        )

    feature_version_ids: dict[str, str] = {}
    features_computed: list[str] = []
    features_reused: list[str] = []
    lineage_ok = True

    for step in result.get("steps", []):
        fname = step.get("feature_name", step.get("step", "unknown"))
        fid = step.get("feature_dataset_version_id")
        if fid:
            feature_version_ids[fname] = fid
        if step.get("action") == "reuse":
            features_reused.append(fname)
        else:
            features_computed.append(fname)

    for w in result.get("warnings", []):
        warnings.append(str(w))
        if "lineage" in str(w).lower() or "mixed" in str(w).lower():
            lineage_ok = False

    return FeatureReplayResult(
        **base,
        status="ok",
        warnings=warnings,
        feature_version_ids=feature_version_ids,
        features_computed=features_computed,
        features_reused=features_reused,
        lineage_ok=lineage_ok,
        summary={
            "dataset_version_id": dataset_version_id,
            "price_basis": price_basis.value,
            "features_computed": features_computed,
            "features_reused": features_reused,
            "lineage_ok": lineage_ok,
            "timestamp": timestamp.isoformat(),
        },
    )


def build_feature_summary(*, session: Session) -> FeatureSummary:
    """Read latest feature state for the platform artifact bundle."""
    repo = FeatureDatasetVersionsRepository(session)
    feature_names = [
        "returns",
        "volatility",
        "moving_average",
        "liquidity",
        "regime",
        "regime_classification",
    ]
    versions: dict[str, str] = {}
    warnings: list[str] = []

    for name in feature_names:
        for basis in (PriceBasis.ADJUSTED, PriceBasis.RAW):
            row = repo.get_latest_validated(feature_name=name, underlying_price_basis=basis)
            if row:
                versions[f"{name}/{basis.value}"] = row.dataset_version_id
                break

    return FeatureSummary(
        feature_versions=versions,
        lineage_ok=len(warnings) == 0,
        mixed_lineage_warnings=warnings,
    )
