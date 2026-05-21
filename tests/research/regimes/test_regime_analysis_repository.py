from __future__ import annotations

from pathlib import Path

import pandas as pd
import pyarrow.dataset as ds

from autonomous_trading_platform.research.analysis.regimes.regime_analysis_repository import (
    RegimeAnalysisParquetRepository,
)
from autonomous_trading_platform.research.analysis.regimes.regime_analysis_service import (
    RegimeAnalysisRequest,
    RegimeAnalysisService,
)
from autonomous_trading_platform.research.simulation.artifact_identity import (
    SimulationArtifactIdentity,
)


def _make_identity() -> SimulationArtifactIdentity:
    return SimulationArtifactIdentity(
        run_id="run-regime-test",
        experiment_id="exp-regime-test",
        strategy_id="momentum",
        dataset_version="v1",
        stage_name="adhoc",
        window_role="default",
    )


def _make_equity_curve(n: int = 30) -> pd.DataFrame:
    equity = [100_000.0 * (1.001**i) for i in range(n)]
    timestamps = pd.date_range("2023-01-03", periods=n, freq="B", tz="UTC")
    return pd.DataFrame(
        {
            "timestamp": timestamps,
            "equity": equity,
            "cash": [50_000.0] * n,
            "positions_value": [50_000.0] * n,
            "drawdown": [0.0] * n,
        }
    )


def _make_regime_data(equity: pd.DataFrame) -> pd.DataFrame:
    n = len(equity)
    rows = []
    for i, ts in enumerate(equity["timestamp"]):
        rows.append(
            {
                "symbol": "AAPL",
                "timestamp": ts,
                "regime_trend": "bull" if i < n // 2 else "bear",
                "regime_volatility": "normal_volatility",
                "regime_liquidity": "high_liquidity",
                "regime_mean_reversion": "trending",
                "regime_risk": "risk_on",
            }
        )
    return pd.DataFrame(rows)


class TestRegimeAnalysisRepository:
    def test_persist_creates_parquet_files(self, tmp_path: Path) -> None:
        equity = _make_equity_curve()
        regime_data = _make_regime_data(equity)
        identity = _make_identity()

        repo = RegimeAnalysisParquetRepository(base_path=str(tmp_path))
        svc = RegimeAnalysisService(repository=repo)

        request = RegimeAnalysisRequest(
            equity_curve=equity,
            trade_logs=pd.DataFrame(),
            regime_data=regime_data,
            identity=identity,
        )
        svc.analyze(request, persist=True)

        metrics_root = tmp_path / "simulations" / "regime_analysis" / "metrics"
        assert metrics_root.exists()
        parquet_files = list(metrics_root.rglob("*.parquet"))
        assert len(parquet_files) > 0

    def test_persisted_metrics_readable(self, tmp_path: Path) -> None:
        equity = _make_equity_curve()
        regime_data = _make_regime_data(equity)
        identity = _make_identity()

        repo = RegimeAnalysisParquetRepository(base_path=str(tmp_path))
        svc = RegimeAnalysisService(repository=repo)

        request = RegimeAnalysisRequest(
            equity_curve=equity,
            trade_logs=pd.DataFrame(),
            regime_data=regime_data,
            identity=identity,
        )
        result = svc.analyze(request, persist=True)

        metrics_root = tmp_path / "simulations" / "regime_analysis" / "metrics"
        dataset = ds.dataset(str(metrics_root), format="parquet", partitioning="hive")
        table = dataset.to_table(filter=ds.field("run_id") == identity.run_id)
        df = table.to_pandas()

        assert len(df) == len(result.all_regime_metrics)
        assert "regime_dimension" in df.columns
        assert "regime_label" in df.columns
        assert "sharpe" in df.columns

    def test_persisted_transitions_readable(self, tmp_path: Path) -> None:
        equity = _make_equity_curve()
        regime_data = _make_regime_data(equity)
        identity = _make_identity()

        repo = RegimeAnalysisParquetRepository(base_path=str(tmp_path))
        svc = RegimeAnalysisService(repository=repo)

        request = RegimeAnalysisRequest(
            equity_curve=equity,
            trade_logs=pd.DataFrame(),
            regime_data=regime_data,
            identity=identity,
        )
        svc.analyze(request, persist=True)

        transitions_root = tmp_path / "simulations" / "regime_analysis" / "transitions"
        assert transitions_root.exists()

    def test_persisted_profiles_readable(self, tmp_path: Path) -> None:
        equity = _make_equity_curve()
        regime_data = _make_regime_data(equity)
        identity = _make_identity()

        repo = RegimeAnalysisParquetRepository(base_path=str(tmp_path))
        svc = RegimeAnalysisService(repository=repo)

        request = RegimeAnalysisRequest(
            equity_curve=equity,
            trade_logs=pd.DataFrame(),
            regime_data=regime_data,
            identity=identity,
        )
        svc.analyze(request, persist=True)

        profiles_root = tmp_path / "simulations" / "regime_analysis" / "profiles"
        assert profiles_root.exists()
        dataset = ds.dataset(str(profiles_root), format="parquet", partitioning="hive")
        table = dataset.to_table(filter=ds.field("run_id") == identity.run_id)
        df = table.to_pandas()

        assert len(df) == 5  # one row per dimension
        assert "regime_dimension" in df.columns
        assert "is_regime_robust" in df.columns

    def test_metrics_tied_to_run_id(self, tmp_path: Path) -> None:
        equity = _make_equity_curve()
        regime_data = _make_regime_data(equity)

        repo = RegimeAnalysisParquetRepository(base_path=str(tmp_path))
        svc = RegimeAnalysisService(repository=repo)

        for run_id in ["run-A", "run-B"]:
            identity = SimulationArtifactIdentity(
                run_id=run_id,
                experiment_id="exp-001",
                strategy_id="momentum",
                dataset_version="v1",
                stage_name="adhoc",
                window_role="default",
            )
            request = RegimeAnalysisRequest(
                equity_curve=equity,
                trade_logs=pd.DataFrame(),
                regime_data=regime_data,
                identity=identity,
            )
            svc.analyze(request, persist=True)

        metrics_root = tmp_path / "simulations" / "regime_analysis" / "metrics"
        dataset = ds.dataset(str(metrics_root), format="parquet", partitioning="hive")

        df_a = dataset.to_table(filter=ds.field("run_id") == "run-A").to_pandas()
        df_b = dataset.to_table(filter=ds.field("run_id") == "run-B").to_pandas()

        assert len(df_a) > 0
        assert len(df_b) > 0
        assert set(df_a["run_id"]) == {"run-A"}
        assert set(df_b["run_id"]) == {"run-B"}

    def test_no_persist_when_flag_false(self, tmp_path: Path) -> None:
        equity = _make_equity_curve()
        regime_data = _make_regime_data(equity)
        identity = _make_identity()

        repo = RegimeAnalysisParquetRepository(base_path=str(tmp_path))
        svc = RegimeAnalysisService(repository=repo)

        request = RegimeAnalysisRequest(
            equity_curve=equity,
            trade_logs=pd.DataFrame(),
            regime_data=regime_data,
            identity=identity,
        )
        svc.analyze(request, persist=False)

        metrics_root = tmp_path / "simulations" / "regime_analysis" / "metrics"
        assert not metrics_root.exists()
