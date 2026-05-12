from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, cast
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from autonomous_trading_platform.storage.parquet.repositories.parquet_simulation_repository import (
    ParquetSimulationRepository,
)
from autonomous_trading_platform.storage.sor.models.audit_logs import AuditLogRow
from autonomous_trading_platform.storage.sor.models.experiments import Experiments
from autonomous_trading_platform.storage.sor.models.metrics_summary import MetricsSummary
from autonomous_trading_platform.storage.sor.models.runtime_job_runs import RuntimeJobRuns
from autonomous_trading_platform.storage.sor.models.simulation_runs import SimulationRuns
from autonomous_trading_platform.storage.sor.models.strategy_configs import StrategyConfigs
from autonomous_trading_platform.storage.sor.models.strategy_control_states import (
    StrategyControlState,
)
from autonomous_trading_platform.storage.sor.models.strategy_governance import StrategyGovernance

_STATUS_BY_GOVERNANCE = {
    "approved_for_live_trading": "live",
    "approved_live": "live",
    "approved_for_paper_trading": "paper",
    "approved_paper": "paper",
    "approved_research": "research",
    "research": "research",
}


class StrategyCatalogService:
    def __init__(self, *, session: Session) -> None:
        self._session = session
        self._simulation_repository = ParquetSimulationRepository()

    def list_strategies(self, *, status_filter: str | None = None) -> list[dict[str, Any]]:
        rows = self._strategy_rows()
        strategies = [
            self._strategy_summary(config, governance, control)
            for config, governance, control in rows
        ]
        if status_filter is not None:
            strategies = [row for row in strategies if row["status"] == status_filter]
        return sorted(strategies, key=lambda row: row["composite_score"], reverse=True)

    def get_strategy_detail(self, *, strategy_id: str) -> dict[str, Any]:
        row = self._strategy_row(strategy_id)
        if row is None:
            raise LookupError(f"Strategy not found: {strategy_id}")
        config, governance, control = row
        summary = self._strategy_summary(config, governance, control)
        metrics = self._latest_metrics(strategy_id)
        return {
            **summary,
            "approval_status": governance.current_state if governance is not None else "unknown",
            "configuration_summary": self._configuration_summary(config.config_json),
            "configuration": config.config_json,
            "metrics": self._metrics_payload(metrics),
            "deployment_history": self._deployment_history(strategy_id, governance),
        }

    def compare_strategies(self, *, strategy_ids: list[str]) -> dict[str, Any]:
        rows = [self.get_strategy_detail(strategy_id=strategy_id) for strategy_id in strategy_ids]
        matrix = [
            {
                "strategy_id": row["strategy_id"],
                "display_name": row["display_name"],
                "total_return": row["metrics"]["total_return"],
                "sharpe_ratio": row["metrics"]["sharpe_ratio"],
                "max_drawdown": row["metrics"]["max_drawdown"],
                "win_rate": row["metrics"]["win_rate"],
                "trade_count": row["metrics"]["trade_count"],
                "consistency_score": row["metrics"]["consistency_score"],
            }
            for row in rows
        ]
        return {"rows": matrix, "metadata": self._comparison_metadata(matrix)}

    def get_strategy_equity_curve(self, *, strategy_id: str) -> dict[str, Any]:
        run = self._latest_run(strategy_id)
        if run is None:
            if self._strategy_row(strategy_id) is None:
                raise LookupError(f"Strategy not found: {strategy_id}")
            return {"strategy_id": strategy_id, "run_id": None, "points": []}

        metrics = self._metrics_for_run(run.run_id)
        points = self._simulation_repository.read_equity_curve(
            experiment_id=run.experiment_id or "",
            strategy_id=strategy_id,
            run_id=run.run_id,
        )
        if not points:
            points = self._equity_points_from_metrics(metrics)
        return {
            "strategy_id": strategy_id,
            "run_id": run.run_id,
            "points": sorted(points, key=lambda point: point["timestamp"]),
        }

    def _strategy_rows(
        self,
    ) -> list[tuple[StrategyConfigs, StrategyGovernance | None, StrategyControlState | None]]:
        configs = list(self._session.scalars(select(StrategyConfigs)).all())
        return [self._join_strategy_state(config) for config in configs]

    def _strategy_row(
        self,
        strategy_id: str,
    ) -> tuple[StrategyConfigs, StrategyGovernance | None, StrategyControlState | None] | None:
        config = self._session.get(StrategyConfigs, strategy_id)
        if config is None:
            return None
        return self._join_strategy_state(config)

    def _join_strategy_state(
        self,
        config: StrategyConfigs,
    ) -> tuple[StrategyConfigs, StrategyGovernance | None, StrategyControlState | None]:
        governance = self._latest_governance(config.strategy_id)
        control = self._session.get(StrategyControlState, config.strategy_id)
        return config, governance, control

    def _strategy_summary(
        self,
        config: StrategyConfigs,
        governance: StrategyGovernance | None,
        control: StrategyControlState | None,
    ) -> dict[str, Any]:
        metrics = self._latest_metrics(config.strategy_id)
        metric_values = self._metrics_payload(metrics)
        display_name = (config.metadata_json or {}).get("display_name") or config.strategy_id
        status_value = self._status(governance, control)
        return {
            "strategy_id": config.strategy_id,
            "display_name": str(display_name),
            "strategy_type": config.strategy_type or "unknown",
            "status": status_value,
            "current_return": metric_values["total_return"],
            "sharpe_ratio": metric_values["sharpe_ratio"],
            "max_drawdown": metric_values["max_drawdown"],
            "win_rate": metric_values["win_rate"],
            "trade_count": metric_values["trade_count"],
            "composite_score": self._composite_score(metric_values),
        }

    def _status(
        self,
        governance: StrategyGovernance | None,
        control: StrategyControlState | None,
    ) -> str:
        if control is not None and not control.enabled:
            return "off"
        if governance is None:
            return "off"
        return _STATUS_BY_GOVERNANCE.get(governance.current_state, "off")

    def _latest_governance(self, strategy_id: str) -> StrategyGovernance | None:
        stmt = (
            select(StrategyGovernance)
            .where(StrategyGovernance.strategy_id == strategy_id)
            .order_by(StrategyGovernance.updated_at.desc())
            .limit(1)
        )
        return cast(StrategyGovernance | None, self._session.scalars(stmt).one_or_none())

    def _latest_run(self, strategy_id: str) -> SimulationRuns | None:
        stmt = (
            select(SimulationRuns)
            .where(SimulationRuns.strategy_id == strategy_id)
            .order_by(SimulationRuns.start_time.desc())
            .limit(1)
        )
        return cast(SimulationRuns | None, self._session.scalars(stmt).one_or_none())

    def _latest_metrics(self, strategy_id: str) -> MetricsSummary | None:
        stmt = (
            select(MetricsSummary)
            .join(SimulationRuns, MetricsSummary.run_id == SimulationRuns.run_id)
            .where(SimulationRuns.strategy_id == strategy_id)
            .order_by(MetricsSummary.created_at.desc())
            .limit(1)
        )
        return cast(MetricsSummary | None, self._session.scalars(stmt).one_or_none())

    def _metrics_for_run(self, run_id: str) -> MetricsSummary | None:
        stmt = select(MetricsSummary).where(MetricsSummary.run_id == run_id).limit(1)
        return cast(MetricsSummary | None, self._session.scalars(stmt).one_or_none())

    def _metrics_payload(self, metrics: MetricsSummary | None) -> dict[str, Any]:
        if metrics is None:
            return {
                "total_return": 0.0,
                "sharpe_ratio": 0.0,
                "max_drawdown": 0.0,
                "win_rate": 0.0,
                "trade_count": 0,
                "consistency_score": 0.0,
            }
        metrics_json = metrics.metrics_json or {}
        trade_count = int(metrics.trade_count or 0)
        win_rate = metrics_json.get("win_rate")
        if win_rate is None and trade_count > 0 and metrics.winning_trade_count is not None:
            win_rate = metrics.winning_trade_count / trade_count
        return {
            "total_return": float(metrics.total_return or 0.0),
            "sharpe_ratio": float(metrics.sharpe_ratio or 0.0),
            "max_drawdown": float(metrics.max_drawdown or 0.0),
            "win_rate": float(win_rate or 0.0),
            "trade_count": trade_count,
            "consistency_score": float(metrics_json.get("consistency_score") or 0.0),
        }

    def _composite_score(self, metrics: dict[str, Any]) -> float:
        score = (
            float(metrics["sharpe_ratio"]) * 0.4
            + float(metrics["total_return"]) * 0.3
            - abs(float(metrics["max_drawdown"])) * 0.2
            + float(metrics["consistency_score"]) * 0.1
        )
        return round(score, 6)

    def _configuration_summary(self, config: dict[str, Any]) -> str:
        if not config:
            return "Default configuration"
        parts = [f"{key}: {value}" for key, value in sorted(config.items())]
        return "; ".join(parts)

    def _deployment_history(
        self,
        strategy_id: str,
        governance: StrategyGovernance | None,
    ) -> list[dict[str, Any]]:
        stmt = (
            select(AuditLogRow)
            .where(AuditLogRow.event_type == "STRATEGY_GOVERNANCE_TRANSITIONED")
            .order_by(AuditLogRow.event_timestamp.asc())
        )
        events = []
        for row in self._session.scalars(stmt):
            metadata = row.event_metadata or {}
            if metadata.get("strategy_id") != strategy_id:
                continue
            events.append(
                {
                    "from_state": metadata.get("from_state"),
                    "to_state": metadata.get("to_state"),
                    "transitioned_at": row.event_timestamp,
                    "reason": metadata.get("reason"),
                    "updated_by": metadata.get("actor"),
                }
            )
        if events or governance is None:
            return events
        return [
            {
                "from_state": None,
                "to_state": governance.current_state,
                "transitioned_at": governance.updated_at,
                "reason": "current governance state",
                "updated_by": governance.submitted_by,
            }
        ]

    def _equity_points_from_metrics(self, metrics: MetricsSummary | None) -> list[dict[str, Any]]:
        metrics_json = metrics.metrics_json if metrics is not None else None
        raw_points = (metrics_json or {}).get("equity_curve") or []
        points = []
        for raw in raw_points:
            points.append(
                {
                    "timestamp": raw["timestamp"],
                    "value": float(raw.get("value", raw.get("equity", 0.0))),
                    "drawdown": float(raw.get("drawdown", 0.0)),
                }
            )
        return points

    def _comparison_metadata(self, rows: list[dict[str, Any]]) -> dict[str, Any]:
        metrics = [
            "total_return",
            "sharpe_ratio",
            "max_drawdown",
            "win_rate",
            "trade_count",
            "consistency_score",
        ]
        metadata: dict[str, Any] = {}
        for metric in metrics:
            ordered = sorted(rows, key=lambda row: row[metric])
            metadata[metric] = {
                "best_strategy_id": ordered[-1]["strategy_id"],
                "worst_strategy_id": ordered[0]["strategy_id"],
            }
        return metadata


_EXPERIMENT_STATUS_NORMALISE = {
    "queued": "pending",
    "complete": "completed",
    "RUNNING": "running",
    "COMPLETED": "completed",
    "ACTIVE": "completed",
    "FAILED": "failed",
    "CANCELLED": "cancelled",
    "PENDING": "pending",
}

_EXPERIMENT_TYPE_NORMALISE = {
    "sweep": "parameter_sweep",
    "ab": "ab_comparison",
    "rolling": "rolling_window",
}


class ExperimentCatalogService:
    def __init__(self, *, session: Session) -> None:
        self._session = session

    def create_experiment(
        self,
        *,
        name: str,
        experiment_type: str,
        symbols: list[str],
        start_date: str,
        end_date: str,
        price_basis: str,
        strategy_count: int,
        parameter_ranges: dict[str, Any],
        actor: str,
    ) -> dict[str, Any]:
        now = datetime.now(UTC)
        experiment_id = str(uuid4())
        self._session.add(
            Experiments(
                experiment_id=experiment_id,
                experiment_name=name,
                created_at=now,
                description=None,
                status="pending",
                strategy_set_json={"strategy_count": strategy_count},
                parameter_grid_json={"parameter_ranges": parameter_ranges},
                dataset_version=None,
                universe_version=None,
                start_time=None,
                end_time=None,
                metadata_json={
                    "created_by": actor,
                    "experiment_type": experiment_type,
                    "symbols": symbols,
                    "start_date": start_date,
                    "end_date": end_date,
                    "price_basis": price_basis,
                    "strategy_count": strategy_count,
                    "parameter_ranges": parameter_ranges,
                },
            )
        )
        self._session.add(
            RuntimeJobRuns(
                job_run_id=str(uuid4()),
                job_name="experiment_pipeline_cycle",
                parent_job_run_id=None,
                status="queued",
                trigger_type="manual",
                started_at=now,
                completed_at=None,
                duration_ms=None,
                error_message=None,
                correlation_id=experiment_id,
                input_summary_json={
                    "experiment_id": experiment_id,
                    "experiment_type": experiment_type,
                    "symbols": symbols,
                    "start_date": start_date,
                    "end_date": end_date,
                },
                output_summary_json=None,
            )
        )
        self._session.flush()
        return {"experiment_id": experiment_id, "status": "pending"}

    def cancel_experiment(self, *, experiment_id: str) -> None:
        experiment = self._session.get(Experiments, experiment_id)
        if experiment is None:
            raise LookupError(f"Experiment not found: {experiment_id}")
        if experiment.status not in ("pending", "queued", "running"):
            raise ValueError(f"Cannot cancel experiment with status '{experiment.status}'")
        experiment.status = "cancelled"
        self._session.flush()

    def list_experiments(self) -> list[dict[str, Any]]:
        stmt = select(Experiments).order_by(Experiments.created_at.desc())
        return [self._experiment_summary(row) for row in self._session.scalars(stmt)]

    def get_experiment_detail(self, *, experiment_id: str) -> dict[str, Any]:
        experiment = self._session.get(Experiments, experiment_id)
        if experiment is None:
            raise LookupError(f"Experiment not found: {experiment_id}")
        metadata = experiment.metadata_json or {}
        strategies = self.get_experiment_strategies(experiment_id=experiment_id)
        return {
            **self._experiment_summary(experiment),
            "strategies": strategies,
            "parameter_ranges": metadata.get("parameter_ranges"),
            "price_basis": metadata.get("price_basis"),
        }

    def get_experiment_strategies(self, *, experiment_id: str) -> list[dict[str, Any]]:
        experiment = self._session.get(Experiments, experiment_id)
        if experiment is None:
            raise LookupError(f"Experiment not found: {experiment_id}")
        runs = self._runs_for_experiment(experiment_id)
        results = [self._strategy_row_for_run(run) for run in runs]
        return sorted(results, key=lambda row: row["composite_score"], reverse=True)

    def _experiment_summary(self, row: Experiments) -> dict[str, Any]:
        metadata = row.metadata_json or {}
        runs = self._runs_for_experiment(row.experiment_id)
        strategy_results = [self._run_metrics(run) for run in runs]
        ranked = sorted(strategy_results, key=lambda r: r["sharpe_ratio"], reverse=True)
        best = ranked[0] if ranked else None
        passed = len([r for r in strategy_results if r["sharpe_ratio"] > 0])

        raw_status = row.status or "pending"
        status = _EXPERIMENT_STATUS_NORMALISE.get(raw_status, raw_status.lower())

        # Recover stale "running" experiments: if the DB says running but no
        # simulation run is still in-flight, derive the real status from the runs.
        if status == "running" and runs:
            active_run_statuses = {(r.status or "").upper() for r in runs}
            if "RUNNING" not in active_run_statuses:
                status = "failed" if "FAILED" in active_run_statuses else "completed"

        raw_type = metadata.get("experiment_type", "backtest") or "backtest"
        experiment_type = _EXPERIMENT_TYPE_NORMALISE.get(str(raw_type), str(raw_type))

        return {
            "experiment_id": row.experiment_id,
            "experiment_name": row.experiment_name,
            "experiment_type": experiment_type,
            "status": status,
            "created_at": row.created_at,
            "symbols": metadata.get("symbols") or [],
            "start_date": metadata.get("start_date"),
            "end_date": metadata.get("end_date"),
            "dataset_version": row.dataset_version,
            "total_strategies": len(strategy_results),
            "strategies_passed_filters": passed,
            "best_sharpe": best["sharpe_ratio"] if best else None,
            "best_return": best["total_return"] if best else None,
            "progress": metadata.get("progress"),
        }

    def _runs_for_experiment(self, experiment_id: str) -> list[SimulationRuns]:
        stmt = (
            select(SimulationRuns)
            .where(SimulationRuns.experiment_id == experiment_id)
            .order_by(SimulationRuns.start_time.desc())
        )
        return list(self._session.scalars(stmt).all())

    def _run_metrics(self, run: SimulationRuns) -> dict[str, Any]:
        catalog = StrategyCatalogService(session=self._session)
        metrics = catalog._metrics_for_run(run.run_id)
        return catalog._metrics_payload(metrics)

    def _strategy_row_for_run(self, run: SimulationRuns) -> dict[str, Any]:
        catalog = StrategyCatalogService(session=self._session)
        metrics = catalog._metrics_for_run(run.run_id)
        payload = catalog._metrics_payload(metrics)
        score = catalog._composite_score(payload)
        governance = catalog._latest_governance(run.strategy_id)
        return {
            "strategy_id": run.strategy_id,
            "sharpe_ratio": payload["sharpe_ratio"],
            "total_return": payload["total_return"],
            "max_drawdown": payload["max_drawdown"],
            "simulation_stage": None,
            "governance_state": governance.current_state if governance else "unknown",
            "composite_score": score,
            "status": run.status,
        }
