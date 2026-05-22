from __future__ import annotations

from pathlib import Path

import pandas as pd
import pyarrow.dataset as ds
import pytest

from autonomous_trading_platform.research.simulation.artifact_identity import (
    SimulationArtifactIdentity,
)
from autonomous_trading_platform.storage.parquet.repositories.parquet_simulation_repository import (
    ParquetSimulationRepository,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_identity(
    run_id: str = "run-001",
    experiment_id: str = "exp-001",
    strategy_id: str = "strat-001",
    dataset_version: str = "v1",
    stage_name: str = "adhoc",
    window_role: str = "default",
) -> SimulationArtifactIdentity:
    return SimulationArtifactIdentity(
        run_id=run_id,
        experiment_id=experiment_id,
        strategy_id=strategy_id,
        dataset_version=dataset_version,
        stage_name=stage_name,
        window_role=window_role,
    )


def _equity_frame(run_id: str = "run-001") -> pd.DataFrame:
    """Minimal equity curve — stage_name/window_role/dataset_version are injected."""
    return pd.DataFrame(
        {
            "run_id": [run_id],
            "experiment_id": ["exp-001"],
            "strategy_id": ["strat-001"],
            "timestamp": [pd.Timestamp("2023-01-03 10:00:00", tz="UTC")],
            "equity": [100_000.0],
            "cash": [100_000.0],
            "positions_value": [0.0],
            "drawdown": [0.0],
        }
    )


def _trade_frame(run_id: str = "run-001") -> pd.DataFrame:
    return pd.DataFrame(
        {
            "run_id": [run_id],
            "experiment_id": ["exp-001"],
            "strategy_id": ["strat-001"],
            "symbol": ["AAPL"],
            "timestamp": [pd.Timestamp("2023-01-03 10:00:00", tz="UTC")],
            "side": ["BUY"],
            "quantity": [10.0],
            "price": [150.0],
            "notional": [1500.0],
            "fees": [1.5],
        }
    )


# ---------------------------------------------------------------------------
# Artifact identity — unit tests (no I/O)
# ---------------------------------------------------------------------------


class TestSimulationArtifactIdentity:
    def test_required_fields_validation(self) -> None:
        with pytest.raises(ValueError, match="missing required fields"):
            SimulationArtifactIdentity(
                run_id="",
                experiment_id="exp-1",
                strategy_id="strat-1",
                dataset_version="v1",
                stage_name="adhoc",
                window_role="default",
            )

    def test_partition_path_format(self) -> None:
        identity = _make_identity(
            run_id="run-abc",
            experiment_id="exp-xyz",
            strategy_id="strat-s1",
            dataset_version="v2",
            stage_name="cheap",
            window_role="train",
        )
        path = identity.partition_path()
        assert "experiment_id=exp-xyz" in path
        assert "run_id=run-abc" in path
        assert "stage_name=cheap" in path
        assert "window_role=train" in path
        assert "strategy_id=strat-s1" in path
        assert "dataset_version=v2" in path

    def test_different_run_ids_produce_different_partition_paths(self) -> None:
        id_a = _make_identity(run_id="run-aaa")
        id_b = _make_identity(run_id="run-bbb")
        assert id_a.partition_path() != id_b.partition_path()

    def test_different_window_roles_produce_different_paths(self) -> None:
        id_train = _make_identity(window_role="fold_0_train")
        id_test = _make_identity(window_role="fold_0_test")
        assert id_train.partition_path() != id_test.partition_path()

    def test_different_stage_names_produce_different_paths(self) -> None:
        id_cheap = _make_identity(stage_name="cheap")
        id_wf = _make_identity(stage_name="walk_forward")
        assert id_cheap.partition_path() != id_wf.partition_path()

    def test_to_manifest_dict_contains_required_fields(self) -> None:
        identity = _make_identity(
            run_id="run-1",
            experiment_id="exp-1",
            strategy_id="strat-1",
            dataset_version="v1",
            stage_name="cheap",
            window_role="train",
        )
        d = identity.to_manifest_dict()
        assert d["run_id"] == "run-1"
        assert d["experiment_id"] == "exp-1"
        assert d["strategy_id"] == "strat-1"
        assert d["dataset_version"] == "v1"
        assert d["stage_name"] == "cheap"
        assert d["window_role"] == "train"

    def test_to_manifest_dict_omits_none_optional_fields(self) -> None:
        identity = _make_identity()
        d = identity.to_manifest_dict()
        assert "seed" not in d
        assert "window_id" not in d
        assert "universe_version" not in d

    def test_to_manifest_dict_includes_optional_fields_when_set(self) -> None:
        from datetime import date

        identity = SimulationArtifactIdentity(
            run_id="run-1",
            experiment_id="exp-1",
            strategy_id="strat-1",
            dataset_version="v1",
            stage_name="mc",
            window_role="mc_run_3",
            seed=42,
            universe_version="universe_v2",
            start_date=date(2023, 1, 1),
            end_date=date(2023, 6, 30),
        )
        d = identity.to_manifest_dict()
        assert d["seed"] == 42
        assert d["universe_version"] == "universe_v2"
        assert d["start_date"] == "2023-01-01"
        assert d["end_date"] == "2023-06-30"


# ---------------------------------------------------------------------------
# Parquet repository — I/O tests using tmp_path
# ---------------------------------------------------------------------------


class TestParquetSimulationRepository:
    def test_two_different_run_ids_write_separate_partitions(self, tmp_path: Path) -> None:
        repo = ParquetSimulationRepository(base_path=str(tmp_path))

        id_a = _make_identity(run_id="run-aaa")
        id_b = _make_identity(run_id="run-bbb")

        repo.write_equity_curve(frame=_equity_frame("run-aaa"), identity=id_a)
        repo.write_equity_curve(frame=_equity_frame("run-bbb"), identity=id_b)

        dataset_root = tmp_path / "simulations" / "equity_curve"
        exp_dir = dataset_root / "experiment_id=exp-001"
        assert (exp_dir / "run_id=run-aaa").exists()
        assert (exp_dir / "run_id=run-bbb").exists()

    def test_walk_forward_train_and_test_write_separate_partitions(self, tmp_path: Path) -> None:
        repo = ParquetSimulationRepository(base_path=str(tmp_path))

        id_train = _make_identity(run_id="run-t0", window_role="f0_train", stage_name="wf")
        id_test = _make_identity(run_id="run-t1", window_role="f0_test", stage_name="wf")

        repo.write_equity_curve(frame=_equity_frame("run-t0"), identity=id_train)
        repo.write_equity_curve(frame=_equity_frame("run-t1"), identity=id_test)

        dataset_root = tmp_path / "simulations" / "equity_curve"
        exp_dir = dataset_root / "experiment_id=exp-001"
        assert (exp_dir / "run_id=run-t0").exists()
        assert (exp_dir / "run_id=run-t1").exists()

        # window_role dirs under each run partition are distinct
        train_dirs = list((exp_dir / "run_id=run-t0").rglob("window_role=f0_train"))
        test_dirs = list((exp_dir / "run_id=run-t1").rglob("window_role=f0_test"))
        assert train_dirs, "train window_role partition not found"
        assert test_dirs, "test window_role partition not found"

    def test_monte_carlo_runs_write_separate_partitions(self, tmp_path: Path) -> None:
        repo = ParquetSimulationRepository(base_path=str(tmp_path))

        n_runs = 3
        for i in range(n_runs):
            identity = SimulationArtifactIdentity(
                run_id=f"run-mc-{i}",
                experiment_id="exp-001",
                strategy_id="strat-001",
                dataset_version="v1",
                stage_name="mc",
                window_role=f"mc_{i}",
                seed=100 + i,
            )
            repo.write_equity_curve(
                frame=_equity_frame(f"run-mc-{i}"),
                identity=identity,
            )

        dataset_root = tmp_path / "simulations" / "equity_curve"
        exp_dir = dataset_root / "experiment_id=exp-001"
        for i in range(n_runs):
            assert (exp_dir / f"run_id=run-mc-{i}").exists(), f"mc run {i} partition missing"

    def test_staged_pipeline_distinct_stage_partitions(self, tmp_path: Path) -> None:
        repo = ParquetSimulationRepository(base_path=str(tmp_path))

        for stage in ["cheap", "mid", "wf"]:
            identity = SimulationArtifactIdentity(
                run_id=f"run-{stage}",
                experiment_id="exp-001",
                strategy_id="strat-001",
                dataset_version="v1",
                stage_name=stage,
                window_role="default",
            )
            repo.write_equity_curve(
                frame=_equity_frame(f"run-{stage}"),
                identity=identity,
            )

        dataset_root = tmp_path / "simulations" / "equity_curve"
        exp_dir = dataset_root / "experiment_id=exp-001"
        for stage in ["cheap", "mid", "wf"]:
            stage_dirs = list((exp_dir / f"run_id=run-{stage}").rglob(f"stage_name={stage}"))
            assert stage_dirs, f"stage_name={stage} partition not found"

    def test_identity_columns_injected_into_parquet_data(self, tmp_path: Path) -> None:
        repo = ParquetSimulationRepository(base_path=str(tmp_path))

        identity = SimulationArtifactIdentity(
            run_id="run-inject-test",
            experiment_id="exp-001",
            strategy_id="strat-001",
            dataset_version="v1",
            stage_name="cheap",
            window_role="train",
        )

        # Frame does NOT include stage_name/window_role/dataset_version
        frame = _equity_frame("run-inject-test")
        repo.write_equity_curve(frame=frame, identity=identity)

        # Read back and verify injected columns are present
        dataset_root = tmp_path / "simulations" / "equity_curve"
        dataset = ds.dataset(str(dataset_root), format="parquet", partitioning="hive")
        table = dataset.to_table()
        df = table.to_pandas()

        assert "stage_name" in df.columns
        assert "window_role" in df.columns
        assert "dataset_version" in df.columns
        assert df["stage_name"].iloc[0] == "cheap"
        assert df["window_role"].iloc[0] == "train"
        assert df["dataset_version"].iloc[0] == "v1"

    def test_repeated_same_config_different_run_id_no_collision(self, tmp_path: Path) -> None:
        repo = ParquetSimulationRepository(base_path=str(tmp_path))

        ids = [_make_identity(run_id=f"run-rep-{i}") for i in range(3)]

        for i, identity in enumerate(ids):
            repo.write_equity_curve(
                frame=_equity_frame(f"run-rep-{i}"),
                identity=identity,
            )

        dataset_root = tmp_path / "simulations" / "equity_curve"
        exp_dir = dataset_root / "experiment_id=exp-001"
        for i in range(3):
            assert (exp_dir / f"run_id=run-rep-{i}").exists()

    def test_write_trade_logs_uses_identity(self, tmp_path: Path) -> None:
        repo = ParquetSimulationRepository(base_path=str(tmp_path))
        identity = _make_identity(run_id="run-tl-test", stage_name="cheap", window_role="train")

        repo.write_trade_logs(frame=_trade_frame("run-tl-test"), identity=identity)

        dataset_root = tmp_path / "simulations" / "trade_logs"
        assert (dataset_root / "experiment_id=exp-001" / "run_id=run-tl-test").exists()

    def test_missing_timestamp_raises(self, tmp_path: Path) -> None:
        repo = ParquetSimulationRepository(base_path=str(tmp_path))
        identity = _make_identity()
        bad_frame = pd.DataFrame({"equity": [100.0]})

        with pytest.raises(ValueError, match="missing required column: timestamp"):
            repo.write_equity_curve(frame=bad_frame, identity=identity)
