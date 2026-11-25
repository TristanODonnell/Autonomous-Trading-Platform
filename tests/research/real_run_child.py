"""One research experiment on real simulations, run as a process of its own (plan R7).

    python -m tests.research.real_run_child <data_root> <summary.json>

Seeds synthetic bars under data_root, runs the research hook's six-stage
experiment on them with the real SimulationRunner, and writes a summary of
everything the run produced: who entered and left each stage, every
simulation's numbers, and a checksum of each output file.

tests/research/test_real_run_across_processes.py starts this twice and compares
the two summaries. Not a test module: pytest does not collect it.
"""

from __future__ import annotations

import json
import sys
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SHAPE = "falls_then_rises"
# Fixed configs, not a draw (real_run_support.PINNED_STRATEGIES), resolved in main().
FAMILIES = ("momentum", "mean_reversion", "moving_average_crossover")


def _file_checksums(data_root: Path) -> dict[str, str]:
    """A checksum of the rows of every output file, by its path under data_root.

    The rows, not the bytes: a Parquet file also carries writer metadata, which
    may change without the data changing. The path is part of the result too:
    it holds the run id, so two runs agree only if they derive the same ids.
    """
    import pyarrow.parquet as pq

    from autonomous_trading_platform.storage.parquet.compute_checksum import compute_table_checksum

    root = data_root / "simulations"
    return {
        path.relative_to(data_root).as_posix(): compute_table_checksum(pq.read_table(path))
        for path in sorted(root.rglob("*.parquet"))
    }


def _summary(result: Any, data_root: Path) -> dict[str, Any]:
    stages: list[dict[str, Any]] = []
    simulations: dict[str, dict[str, Any]] = {}
    gate_numbers: dict[str, dict[str, float]] = {}
    for stage in result.stage_results:
        stages.append(
            {
                "stage": stage.stage_name,
                "entered": stage.n_entered,
                "survivors": sorted(config.strategy_id for config in stage.survivors),
            }
        )
        for run in stage.simulation_results:
            simulations[str(run.run_id)] = {
                "stage": stage.stage_name,
                "strategy_id": run.strategy_id,
                "window": [run.start_date.isoformat(), run.end_date.isoformat()],
                "trade_count": run.trade_count,
                "sharpe": run.risk_metrics.sharpe_ratio,
                "max_drawdown": run.risk_metrics.max_drawdown,
                "total_return": run.return_metrics.total_return,
            }
        for strategy_id, evidence in stage.diagnostics.items():
            numbers = gate_numbers.setdefault(strategy_id, {})
            if evidence.mc_aggregation is not None:
                # Resampling draws random paths: the seed has to carry across processes.
                numbers["monte_carlo_pass_rate"] = evidence.mc_aggregation.pass_rate
                numbers["monte_carlo_mean_sharpe"] = evidence.mc_aggregation.sharpe_dist.mean
            if evidence.overfitting_verdict is not None:
                numbers["overfitting_probability"] = (
                    evidence.overfitting_verdict.analysis.overfitting_probability
                )
    return {
        "stages": stages,
        "final_survivors": sorted(config.strategy_id for config in result.final_survivors),
        "simulations": simulations,
        "gate_numbers": gate_numbers,
        "files": _file_checksums(data_root),
    }


def main(data_root: Path, summary_path: Path) -> None:
    # The test suite's own setup: environment defaults, the SQLite type shims
    # and the in-memory engine. Imported first, as pytest does.
    from tests import conftest
    from tests.research.real_run_support import (
        START,
        SYMBOLS,
        build_context,
        hook_experiment,
        pinned_strategy_set,
        seed_dataset,
    )
    from tests.utilities.universe_seeding import seed_universe_version

    conftest.Base.metadata.create_all(bind=conftest._engine)
    session = conftest._TestingSessionLocal()
    try:
        seed_universe_version(
            session,
            symbols=SYMBOLS,
            effective_from=datetime.combine(START, datetime.min.time(), tzinfo=UTC),
        )
        version = seed_dataset(data_root, session, SHAPE)
        ctx = build_context(session, data_root)
        experiment = replace(
            hook_experiment(session, ctx.simulation_runner, version),
            strategy_set=pinned_strategy_set(*FAMILIES),
            parameter_space=None,
        )
        result = ctx.experiment_orchestration_service.run_staged_experiment(experiment)
        summary = _summary(result, data_root)
    finally:
        session.close()
    summary_path.write_text(json.dumps(summary, indent=1, sort_keys=True), encoding="utf-8")


if __name__ == "__main__":
    main(Path(sys.argv[1]), Path(sys.argv[2]))
