"""
RegimeStage — eliminates strategies whose performance depends on one market regime.

Regimes are auto-classified, not hand-picked date windows: the window's own
bars are classified day by day (trend / volatility / liquidity / mean
reversion / risk) by the deterministic TASK-2.2 classifiers — see
gates/regime_labels.py for why this happens on the fly. A hardcoded-window
design ("2020 crash", "2022 bear") could never fall inside the ~90-day
research window used by the monthly platform replay.

Per surviving strategy:
  1. Take a full-window run — reused from an earlier stage (Monte Carlo's
     representative run) when the window matches, otherwise simulated once.
  2. Split its bar returns by regime label and compute per-regime metrics
     (RegimeAnalysisService, TASK-2.3, in-memory — nothing persisted).
  3. Apply the regime gate (gates/regime_gate.py).

Cost: at most one simulation per survivor, plus one bar read + classification
per stage run (shared by every strategy).

YAML block example
------------------
  - name: regime_robustness
    type: regime
    start_date: "2024-01-02"
    end_date: "2024-04-01"
    symbols: [SPY, QQQ, AAPL]
    dimensions: [trend, volatility]
    min_bars_per_regime: 20
    min_regime_sharpe: -0.5
    max_regime_drawdown: -0.25
    min_positive_regime_fraction: 0.5
    on_insufficient_coverage: pass
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Any

import pandas as pd

from autonomous_trading_platform.common.annualisation import BARS_PER_YEAR, TRADING_DAYS_PER_YEAR
from autonomous_trading_platform.contracts.common.enums import PriceBasis
from autonomous_trading_platform.research.analysis.regimes.regime_analysis_service import (
    RegimeAnalysisRequest,
    RegimeAnalysisService,
)
from autonomous_trading_platform.research.analysis.regimes.strategy_regime_profile import (
    StrategyRegimeProfile,
)
from autonomous_trading_platform.research.execution import ExecutionMode
from autonomous_trading_platform.research.experiments.filtering.services.filter_score_service import (
    FilterScoreOutput,
)
from autonomous_trading_platform.research.pipeline.gates.regime_gate import (
    RegimeGateConfig,
    RegimeGateVerdict,
    evaluate_regime_gate,
)
from autonomous_trading_platform.research.pipeline.gates.regime_labels import (
    OnTheFlyRegimeLabelProvider,
    RegimeClassifierWindows,
    RegimeLabelProvider,
    attach_daily_regimes,
)
from autonomous_trading_platform.research.simulation.artifact_identity import (
    SimulationArtifactIdentity,
)
from autonomous_trading_platform.research.simulation.simulation_runner import (
    SimulationRunner,
    SimulationRunResult,
)
from autonomous_trading_platform.strategy.configs.strategy_config import StrategyConfig

from ._gate_stage_support import (
    GateSimulationRunner,
    GateWindow,
    RunContext,
    find_reusable_reference,
    gate_filter_output,
)
from .base_stage import BaseStage, StageDiagnostics, StageResult

logger = logging.getLogger(__name__)

_REFERENCE_ROLE = "regime_reference"


@dataclass
class RegimeStageConfig:
    name: str
    symbols: list[str]
    start_date: date
    end_date: date
    gate: RegimeGateConfig = field(default_factory=RegimeGateConfig)
    execution_mode: ExecutionMode = ExecutionMode.SERIAL
    max_workers: int = 1
    fail_fast: bool = False

    def __post_init__(self) -> None:
        if self.start_date >= self.end_date:
            raise ValueError("start_date must be before end_date")
        if not self.symbols:
            raise ValueError("symbols must not be empty")


def _infer_bars_per_year(equity_curve: pd.DataFrame) -> int:
    """Daily-resampled runs get daily annualisation; intraday runs keep BARS_PER_YEAR."""
    if len(equity_curve) < 3:
        return BARS_PER_YEAR
    ts = pd.to_datetime(equity_curve["timestamp"], utc=True).sort_values()
    median_gap = ts.diff().dropna().median()
    return TRADING_DAYS_PER_YEAR if median_gap >= pd.Timedelta(hours=12) else BARS_PER_YEAR


class RegimeStage(BaseStage):
    def __init__(
        self,
        *,
        stage_config: RegimeStageConfig,
        simulation_runner: SimulationRunner,
        label_provider: RegimeLabelProvider | None,
        regime_analysis_service: RegimeAnalysisService | None = None,
    ) -> None:
        self._cfg = stage_config
        self._label_provider = label_provider
        self._analysis = regime_analysis_service or RegimeAnalysisService()
        self._sims = GateSimulationRunner(
            simulation_runner=simulation_runner,
            stage_name=stage_config.name,
            execution_mode=stage_config.execution_mode,
            max_workers=stage_config.max_workers,
            fail_fast=stage_config.fail_fast,
        )
        self._prior_results: list[StageResult] = []

    @classmethod
    def from_dict(cls, raw: dict[str, Any], simulation_runner: SimulationRunner) -> RegimeStage:
        from autonomous_trading_platform.research.config.stage_configs import (
            RegimeStageConfigModel,
        )

        v = RegimeStageConfigModel.model_validate(raw)
        windows = RegimeClassifierWindows(**v.classifier_windows.model_dump())
        stage_cfg = RegimeStageConfig(
            name=v.name,
            symbols=list(v.symbols),
            start_date=v.start_date,
            end_date=v.end_date,
            gate=RegimeGateConfig(
                dimensions=tuple(v.dimensions),
                min_bars_per_regime=v.min_bars_per_regime,
                min_evaluable_regimes=v.min_evaluable_regimes,
                min_regime_sharpe=v.min_regime_sharpe,
                max_regime_drawdown=v.max_regime_drawdown,
                min_positive_regime_fraction=v.min_positive_regime_fraction,
                on_insufficient_coverage=v.on_insufficient_coverage,  # type: ignore[arg-type]
            ),
            execution_mode=ExecutionMode(v.execution_mode),
            max_workers=v.max_workers,
            fail_fast=v.fail_fast,
        )
        provider = OnTheFlyRegimeLabelProvider.from_simulation_runner(
            simulation_runner,
            windows=windows,
            warmup_calendar_days=v.label_warmup_calendar_days,
        )
        return cls(
            stage_config=stage_cfg, simulation_runner=simulation_runner, label_provider=provider
        )

    @property
    def stage_name(self) -> str:
        return self._cfg.name

    def bind_prior_results(self, prior_results: Sequence[StageResult]) -> None:
        self._prior_results = list(prior_results)

    def run(
        self,
        survivors: list[StrategyConfig],
        experiment_id: str,
        dataset_version: str,
        random_seed: int,
        price_basis: PriceBasis,
        initial_cash: float,
        resample_to_daily: bool = False,
    ) -> StageResult:
        if not survivors:
            logger.warning("Stage %s received empty survivor list — skipping.", self.stage_name)
            return StageResult(stage_name=self.stage_name)

        window = GateWindow(self._cfg.symbols, self._cfg.start_date, self._cfg.end_date)
        ctx = RunContext(
            experiment_id=experiment_id,
            dataset_version=dataset_version,
            random_seed=random_seed,
            price_basis=price_basis,
            initial_cash=initial_cash,
            resample_to_daily=resample_to_daily,
        )

        daily_regimes, unavailable_reason = self._load_daily_regimes(ctx)

        references: dict[str, SimulationRunResult] = {}
        reused: set[str] = set()
        to_simulate: list[StrategyConfig] = []
        for config in survivors:
            ref = find_reusable_reference(self._prior_results, config.strategy_id, window)
            if ref is not None:
                references[config.strategy_id] = ref
                reused.add(config.strategy_id)
            else:
                to_simulate.append(config)
        simulated = self._sims.run_many(
            jobs=[(c, _REFERENCE_ROLE, 1.0) for c in to_simulate], window=window, ctx=ctx
        )
        for (sid, _role), result in simulated.items():
            references[sid] = result

        logger.info(
            "Stage %-20s | %d strategies | %d reused runs | %d new runs | labelled days=%d",
            self.stage_name,
            len(survivors),
            len(reused),
            len(simulated),
            0
            if daily_regimes is None
            else int(daily_regimes.iloc[:, 1:].notna().any(axis=1).sum()),
        )

        filter_outputs: list[FilterScoreOutput] = []
        diagnostics: dict[str, StageDiagnostics] = {}
        final_survivors: list[StrategyConfig] = []

        for config in survivors:
            sid = config.strategy_id
            ref = references.get(sid)
            profile, reason = self._profile_for(ref, daily_regimes, unavailable_reason, ctx)
            verdict = evaluate_regime_gate(
                strategy_id=sid,
                profile=profile,
                config=self._cfg.gate,
                unavailable_reason=reason,
            )
            self._log_verdict(verdict)
            filter_outputs.append(
                gate_filter_output(
                    sid, passed=verdict.passed, failure_reasons=verdict.failure_reasons
                )
            )
            diagnostics[sid] = StageDiagnostics(
                regime_profile=profile,
                regime_verdict=verdict,
                reference_result=ref,
            )
            if verdict.passed:
                final_survivors.append(config)

        logger.info(
            "Stage %-20s | entered %d | survived %d | eliminated %d",
            self.stage_name,
            len(survivors),
            len(final_survivors),
            len(survivors) - len(final_survivors),
        )

        return StageResult(
            stage_name=self.stage_name,
            simulation_results=list(simulated.values()),
            filter_outputs=filter_outputs,
            survivors=final_survivors,
            diagnostics=diagnostics,
        )

    # ------------------------------------------------------------------

    def _load_daily_regimes(self, ctx: RunContext) -> tuple[pd.DataFrame | None, str | None]:
        if self._label_provider is None:
            return None, "no regime label provider configured"
        try:
            daily = self._label_provider.load_daily_regimes(
                dataset_version=ctx.dataset_version,
                price_basis=ctx.price_basis,
                symbols=self._cfg.symbols,
                start_date=self._cfg.start_date,
                end_date=self._cfg.end_date,
            )
        except Exception as exc:
            logger.warning(
                "Stage %s: regime labelling failed (%s) — insufficient-coverage policy applies",
                self.stage_name,
                exc,
                exc_info=True,
            )
            return None, f"regime labelling failed: {exc}"
        if daily.empty:
            return None, "no bars available to classify regimes"
        return daily, None

    def _profile_for(
        self,
        ref: SimulationRunResult | None,
        daily_regimes: pd.DataFrame | None,
        unavailable_reason: str | None,
        ctx: RunContext,
    ) -> tuple[StrategyRegimeProfile | None, str | None]:
        if daily_regimes is None:
            return None, unavailable_reason
        if ref is None or ref.equity_curve is None or ref.equity_curve.empty:
            return None, "no full-window run available"

        equity = ref.equity_curve
        identity = SimulationArtifactIdentity(
            run_id=str(ref.run_id),
            experiment_id=ctx.experiment_id,
            strategy_id=ref.strategy_id,
            dataset_version=ctx.dataset_version,
            stage_name=self.stage_name,
            window_role=_REFERENCE_ROLE,
            seed=ref.random_seed,
            price_basis=ctx.price_basis.value,
            start_date=ref.start_date,
            end_date=ref.end_date,
        )
        result = self._analysis.analyze(
            RegimeAnalysisRequest(
                equity_curve=equity,
                trade_logs=pd.DataFrame(),
                regime_data=attach_daily_regimes(equity, daily_regimes),
                identity=identity,
                analyze_transitions=False,
                bars_per_year=_infer_bars_per_year(equity),
            ),
            persist=False,
        )
        return result.profile, None

    def _log_verdict(self, verdict: RegimeGateVerdict) -> None:
        if verdict.passed:
            logger.debug(
                "  PASSED  %s | evaluated=%s | warnings: %s",
                verdict.strategy_id,
                ",".join(verdict.evaluated_dimensions) or "none",
                "; ".join(verdict.warnings) or "none",
            )
        else:
            logger.debug(
                "  FAILED  %s | reasons: %s",
                verdict.strategy_id,
                "; ".join(verdict.failure_reasons),
            )
