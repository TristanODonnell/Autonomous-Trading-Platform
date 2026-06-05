"""Tests for the strategy CLI commands.

Coverage:
- Parser registration for all commands
- Registry commands (no DB): list-types, inspect-type, validate-config
- Component registry commands (no DB): list-components, inspect-component
- DB-backed catalog commands: list, inspect, compare, equity-curve, active
- evaluate-bar: dry-run guard behavior
- inspect-readiness: output key names and domain note
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from autonomous_trading_platform.cli.commands import strategy as strategy_cmd
from autonomous_trading_platform.cli.main import build_parser
from autonomous_trading_platform.storage.sor.models.strategy_configs import StrategyConfigs
from autonomous_trading_platform.storage.sor.models.strategy_control_states import (
    StrategyControlState,
)
from autonomous_trading_platform.storage.sor.models.strategy_governance import StrategyGovernance

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _args(**kwargs) -> argparse.Namespace:
    defaults = dict(
        timestamp=None,
        dry_run=False,
        family=None,
        include_debug=False,
        include_experimental=False,
        format="json",
        strategy_type=None,
        parameters=None,
        parameters_file=None,
        strategy_id=None,
        strategy_ids=None,
        component_type=None,
        executable_only=False,
        metadata_only=False,
        component_name=None,
        status=None,
    )
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


def _patch_catalog_deps(monkeypatch, db_session: Session) -> None:
    monkeypatch.setattr(
        strategy_cmd,
        "build_catalog_dependencies",
        lambda: strategy_cmd.StrategyCatalogDependencies(session=db_session),
    )


def _seed_strategy(
    db_session: Session,
    *,
    strategy_id: str,
    state: str = "approved_for_paper_trading",
    enabled: bool = True,
) -> None:
    now = datetime.now(UTC)
    config_hash = f"{strategy_id}-hash"
    db_session.add(
        StrategyConfigs(
            strategy_id=strategy_id,
            config_hash=config_hash,
            config_json={"lookback": 20},
            created_at=now,
            strategy_type="momentum",
            metadata_json={"display_name": strategy_id.replace("_", " ").title()},
        )
    )
    db_session.add(
        StrategyGovernance(
            strategy_id=strategy_id,
            config_hash=config_hash,
            current_state=state,
            experiment_id=f"{strategy_id}-exp",
            source_run_id=None,
            submitted_at=now - timedelta(days=2),
            updated_at=now - timedelta(days=1),
            submitted_by="researcher",
        )
    )
    if not enabled:
        db_session.add(
            StrategyControlState(
                strategy_id=strategy_id,
                enabled=False,
                reason="paused",
                updated_by="operator",
                updated_at=now,
            )
        )
    db_session.flush()


# ---------------------------------------------------------------------------
# Parser registration
# ---------------------------------------------------------------------------


class TestParserRegistration:
    def test_evaluate_bar_registered(self):
        parser = build_parser()
        args = parser.parse_args(
            ["strategy", "evaluate-bar", "--timestamp", "2026-05-26T15:35:00Z"]
        )
        assert args.func is strategy_cmd.handle_evaluate_bar

    def test_evaluate_bar_dry_run_flag(self):
        parser = build_parser()
        args = parser.parse_args(
            ["strategy", "evaluate-bar", "--timestamp", "2026-05-26T15:35:00Z", "--dry-run"]
        )
        assert args.dry_run is True

    def test_inspect_readiness_registered(self):
        parser = build_parser()
        args = parser.parse_args(["strategy", "inspect-readiness"])
        assert args.func is strategy_cmd.handle_inspect_readiness

    def test_inspect_readiness_optional_timestamp(self):
        parser = build_parser()
        args = parser.parse_args(
            ["strategy", "inspect-readiness", "--timestamp", "2026-05-26T15:35:00Z"]
        )
        assert args.timestamp == "2026-05-26T15:35:00Z"

    def test_list_types_registered(self):
        parser = build_parser()
        args = parser.parse_args(["strategy", "list-types"])
        assert args.func is strategy_cmd.handle_list_types

    def test_list_types_format_flag(self):
        parser = build_parser()
        args = parser.parse_args(["strategy", "list-types", "--format", "json"])
        assert args.format == "json"

    def test_list_types_include_debug_flag(self):
        parser = build_parser()
        args = parser.parse_args(["strategy", "list-types", "--include-debug"])
        assert args.include_debug is True

    def test_inspect_type_registered(self):
        parser = build_parser()
        args = parser.parse_args(["strategy", "inspect-type", "--strategy-type", "momentum"])
        assert args.func is strategy_cmd.handle_inspect_type
        assert args.strategy_type == "momentum"

    def test_validate_config_registered(self):
        parser = build_parser()
        args = parser.parse_args(
            ["strategy", "validate-config", "--strategy-type", "momentum", "--parameters", "{}"]
        )
        assert args.func is strategy_cmd.handle_validate_config

    def test_list_registered(self):
        parser = build_parser()
        args = parser.parse_args(["strategy", "list"])
        assert args.func is strategy_cmd.handle_list

    def test_list_status_flag(self):
        parser = build_parser()
        args = parser.parse_args(["strategy", "list", "--status", "paper"])
        assert args.status == "paper"

    def test_inspect_registered(self):
        parser = build_parser()
        args = parser.parse_args(["strategy", "inspect", "--strategy-id", "strat_x"])
        assert args.func is strategy_cmd.handle_inspect
        assert args.strategy_id == "strat_x"

    def test_compare_registered(self):
        parser = build_parser()
        args = parser.parse_args(["strategy", "compare", "--strategy-ids", "strat_a,strat_b"])
        assert args.func is strategy_cmd.handle_compare
        assert args.strategy_ids == "strat_a,strat_b"

    def test_equity_curve_registered(self):
        parser = build_parser()
        args = parser.parse_args(["strategy", "equity-curve", "--strategy-id", "strat_x"])
        assert args.func is strategy_cmd.handle_equity_curve

    def test_list_components_registered(self):
        parser = build_parser()
        args = parser.parse_args(["strategy", "list-components"])
        assert args.func is strategy_cmd.handle_list_components

    def test_list_components_component_type_flag(self):
        parser = build_parser()
        args = parser.parse_args(["strategy", "list-components", "--component-type", "indicator"])
        assert args.component_type == "indicator"

    def test_inspect_component_registered(self):
        parser = build_parser()
        args = parser.parse_args(["strategy", "inspect-component", "--component-name", "momentum"])
        assert args.func is strategy_cmd.handle_inspect_component
        assert args.component_name == "momentum"

    def test_active_registered(self):
        parser = build_parser()
        args = parser.parse_args(["strategy", "active"])
        assert args.func is strategy_cmd.handle_active


# ---------------------------------------------------------------------------
# evaluate-bar: dry-run guard
# ---------------------------------------------------------------------------


class TestEvaluateBarDeprecated:
    def test_deprecated_message_printed(self, monkeypatch, capsys):
        from autonomous_trading_platform.cli.commands import runtime as _runtime

        monkeypatch.setattr(_runtime, "run_trading_evaluation_cycle", lambda **_: None)
        strategy_cmd.handle_evaluate_bar(_args(timestamp="2026-05-26T15:35:00Z", dry_run=True))
        out = capsys.readouterr().out
        assert "DEPRECATED" in out

    def test_forwards_to_runtime_evaluate_cycle(self, monkeypatch, capsys):
        from autonomous_trading_platform.cli.commands import runtime as _runtime

        called = []
        monkeypatch.setattr(
            _runtime, "handle_evaluate_cycle", lambda args: called.append(args) or 0
        )
        rc = strategy_cmd.handle_evaluate_bar(_args(timestamp="2026-05-26T15:35:00Z", dry_run=True))
        assert rc == 0
        assert len(called) == 1

    def test_dry_run_does_not_call_cycle(self, monkeypatch, capsys):
        from autonomous_trading_platform.cli.commands import runtime as _runtime

        called = []
        monkeypatch.setattr(
            _runtime, "run_trading_evaluation_cycle", lambda **_: called.append(True)
        )
        strategy_cmd.handle_evaluate_bar(_args(timestamp="2026-05-26T15:35:00Z", dry_run=True))
        assert called == []


# ---------------------------------------------------------------------------
# inspect-readiness: output keys and domain note
# ---------------------------------------------------------------------------


class TestInspectReadiness:
    def test_returns_zero(self, monkeypatch):
        from autonomous_trading_platform.scheduler.jobs.check_ingestion_readiness_job import (
            IngestionReadinessResult,
        )

        monkeypatch.setattr(
            strategy_cmd,
            "check_ingestion_readiness_job",
            lambda **_: IngestionReadinessResult(ready=True, safe_mode=False, reason="ok"),
        )
        rc = strategy_cmd.handle_inspect_readiness(_args(timestamp=None))
        assert rc == 0

    def test_output_uses_ingestion_ready_key(self, monkeypatch, capsys):
        from autonomous_trading_platform.scheduler.jobs.check_ingestion_readiness_job import (
            IngestionReadinessResult,
        )

        monkeypatch.setattr(
            strategy_cmd,
            "check_ingestion_readiness_job",
            lambda **_: IngestionReadinessResult(ready=True, safe_mode=False, reason="ok"),
        )
        strategy_cmd.handle_inspect_readiness(_args(timestamp=None))
        out = capsys.readouterr().out
        parsed = json.loads(out.strip().split("\n", 1)[1])
        assert "ingestion_ready" in parsed, "output must use 'ingestion_ready' not 'ready'"
        assert parsed["ingestion_ready"] is True

    def test_output_contains_domain_note(self, monkeypatch, capsys):
        from autonomous_trading_platform.scheduler.jobs.check_ingestion_readiness_job import (
            IngestionReadinessResult,
        )

        monkeypatch.setattr(
            strategy_cmd,
            "check_ingestion_readiness_job",
            lambda **_: IngestionReadinessResult(ready=False, safe_mode=True, reason="late"),
        )
        strategy_cmd.handle_inspect_readiness(_args(timestamp=None))
        out = capsys.readouterr().out
        assert "domain_note" in out or "operations" in out.lower()

    def test_header_says_ingestion_readiness(self, monkeypatch, capsys):
        from autonomous_trading_platform.scheduler.jobs.check_ingestion_readiness_job import (
            IngestionReadinessResult,
        )

        monkeypatch.setattr(
            strategy_cmd,
            "check_ingestion_readiness_job",
            lambda **_: IngestionReadinessResult(ready=True, safe_mode=False, reason="ok"),
        )
        strategy_cmd.handle_inspect_readiness(_args(timestamp=None))
        out = capsys.readouterr().out
        assert "Ingestion Readiness" in out


# ---------------------------------------------------------------------------
# list-types (registry — no DB)
# ---------------------------------------------------------------------------


class TestListTypes:
    def test_returns_zero(self, capsys):
        rc = strategy_cmd.handle_list_types(
            _args(family=None, include_debug=True, include_experimental=True)
        )
        assert rc == 0

    def test_output_is_parseable_json(self, capsys):
        strategy_cmd.handle_list_types(
            _args(family=None, include_debug=True, include_experimental=True)
        )
        out = capsys.readouterr().out
        parsed = json.loads(out.strip().split("\n", 1)[1])
        assert "count" in parsed
        assert "strategy_types" in parsed

    def test_count_matches_list(self, capsys):
        strategy_cmd.handle_list_types(
            _args(family=None, include_debug=True, include_experimental=True)
        )
        out = capsys.readouterr().out
        parsed = json.loads(out.strip().split("\n", 1)[1])
        assert parsed["count"] == len(parsed["strategy_types"])

    def test_filters_out_debug_by_default(self, capsys):
        strategy_cmd.handle_list_types(
            _args(family=None, include_debug=False, include_experimental=True)
        )
        out = capsys.readouterr().out
        parsed = json.loads(out.strip().split("\n", 1)[1])
        for row in parsed["strategy_types"]:
            assert row["debug"] is False

    def test_includes_debug_when_flag_set(self, capsys):
        strategy_cmd.handle_list_types(
            _args(family=None, include_debug=True, include_experimental=True)
        )
        out = capsys.readouterr().out
        parsed = json.loads(out.strip().split("\n", 1)[1])
        assert any(row["debug"] for row in parsed["strategy_types"])

    def test_required_persisted_features_key_present(self, capsys):
        strategy_cmd.handle_list_types(
            _args(family=None, include_debug=True, include_experimental=True)
        )
        out = capsys.readouterr().out
        parsed = json.loads(out.strip().split("\n", 1)[1])
        for row in parsed["strategy_types"]:
            assert "required_persisted_features" in row


# ---------------------------------------------------------------------------
# inspect-type (registry — no DB)
# ---------------------------------------------------------------------------


class TestInspectType:
    def test_returns_zero_for_momentum(self, capsys):
        rc = strategy_cmd.handle_inspect_type(_args(strategy_type="momentum"))
        assert rc == 0

    def test_output_contains_required_fields(self, capsys):
        strategy_cmd.handle_inspect_type(_args(strategy_type="momentum"))
        out = capsys.readouterr().out
        parsed = json.loads(out.strip().split("\n", 1)[1])
        for key in (
            "strategy_type",
            "family",
            "default_parameters",
            "parameter_specs",
            "warmup_bars",
            "compatibility",
        ):
            assert key in parsed

    def test_strategy_type_matches_request(self, capsys):
        strategy_cmd.handle_inspect_type(_args(strategy_type="mean_reversion"))
        out = capsys.readouterr().out
        parsed = json.loads(out.strip().split("\n", 1)[1])
        assert parsed["strategy_type"] == "mean_reversion"


# ---------------------------------------------------------------------------
# validate-config (registry — no DB)
# ---------------------------------------------------------------------------


class TestValidateConfig:
    def test_valid_parameters_returns_zero(self, capsys):
        rc = strategy_cmd.handle_validate_config(_args(strategy_type="momentum", parameters="{}"))
        assert rc == 0

    def test_none_parameters_defaults_to_empty_object(self, capsys):
        rc = strategy_cmd.handle_validate_config(_args(strategy_type="momentum", parameters=None))
        assert rc == 0

    def test_valid_parameters_output_contains_valid_true(self, capsys):
        strategy_cmd.handle_validate_config(_args(strategy_type="momentum", parameters="{}"))
        out = capsys.readouterr().out
        parsed = json.loads(out.strip().split("\n", 1)[1])
        assert parsed["valid"] is True
        assert "normalized_parameters" in parsed

    def test_invalid_json_returns_nonzero(self, capsys):
        rc = strategy_cmd.handle_validate_config(
            _args(strategy_type="momentum", parameters="{not valid json")
        )
        assert rc != 0

    def test_invalid_json_error_mentions_parameters_file(self, capsys):
        strategy_cmd.handle_validate_config(_args(strategy_type="momentum", parameters="{bad}"))
        out = capsys.readouterr().out
        parsed = json.loads(out.strip().split("\n", 1)[1])
        assert parsed["valid"] is False
        assert "parameters-file" in parsed["error"] or "parameters_file" in parsed["error"]

    def test_non_object_json_returns_nonzero(self, capsys):
        rc = strategy_cmd.handle_validate_config(
            _args(strategy_type="momentum", parameters="[1, 2]")
        )
        assert rc != 0

    def test_parameters_file_valid(self, tmp_path, capsys):
        params_file = tmp_path / "params.json"
        params_file.write_text('{"lookback": 20}', encoding="utf-8")
        rc = strategy_cmd.handle_validate_config(
            _args(strategy_type="momentum", parameters_file=str(params_file))
        )
        assert rc == 0

    def test_parameters_file_valid_output_contains_valid_true(self, tmp_path, capsys):
        params_file = tmp_path / "params.json"
        params_file.write_text('{"lookback": 20}', encoding="utf-8")
        strategy_cmd.handle_validate_config(
            _args(strategy_type="momentum", parameters_file=str(params_file))
        )
        out = capsys.readouterr().out
        parsed = json.loads(out.strip().split("\n", 1)[1])
        assert parsed["valid"] is True
        assert parsed["input_parameters"]["lookback"] == 20

    def test_parameters_file_missing_returns_nonzero(self, capsys):
        rc = strategy_cmd.handle_validate_config(
            _args(strategy_type="momentum", parameters_file="/no/such/file.json")
        )
        assert rc != 0

    def test_parameters_file_non_object_returns_nonzero(self, tmp_path, capsys):
        params_file = tmp_path / "params.json"
        params_file.write_text("[1, 2]", encoding="utf-8")
        rc = strategy_cmd.handle_validate_config(
            _args(strategy_type="momentum", parameters_file=str(params_file))
        )
        assert rc != 0

    def test_parser_registers_parameters_file_flag(self):
        parser = build_parser()
        args = parser.parse_args(
            [
                "strategy",
                "validate-config",
                "--strategy-type",
                "momentum",
                "--parameters-file",
                "p.json",
            ]
        )
        assert args.parameters_file == "p.json"
        assert args.parameters is None


# ---------------------------------------------------------------------------
# list-components (component registry — no DB)
# ---------------------------------------------------------------------------


class TestListComponents:
    def test_returns_zero(self, capsys):
        rc = strategy_cmd.handle_list_components(
            _args(component_type=None, executable_only=False, metadata_only=False)
        )
        assert rc == 0

    def test_output_is_parseable_json(self, capsys):
        strategy_cmd.handle_list_components(
            _args(component_type=None, executable_only=False, metadata_only=False)
        )
        out = capsys.readouterr().out
        parsed = json.loads(out.strip().split("\n", 1)[1])
        assert "count" in parsed
        assert "components" in parsed

    def test_count_matches_list(self, capsys):
        strategy_cmd.handle_list_components(
            _args(component_type=None, executable_only=False, metadata_only=False)
        )
        out = capsys.readouterr().out
        parsed = json.loads(out.strip().split("\n", 1)[1])
        assert parsed["count"] == len(parsed["components"])

    def test_filter_by_component_type_indicator(self, capsys):
        strategy_cmd.handle_list_components(
            _args(component_type="indicator", executable_only=False, metadata_only=False)
        )
        out = capsys.readouterr().out
        parsed = json.loads(out.strip().split("\n", 1)[1])
        for row in parsed["components"]:
            assert row["component_type"] == "indicator"


# ---------------------------------------------------------------------------
# inspect-component (component registry — no DB)
# ---------------------------------------------------------------------------


class TestInspectComponent:
    def test_known_component_returns_zero(self, capsys):
        from autonomous_trading_platform.strategy.components import get_component_registry

        component_names = [
            defn.component_name for defn in get_component_registry().list_components()
        ]
        rc = strategy_cmd.handle_inspect_component(_args(component_name=component_names[0]))
        assert rc == 0

    def test_unknown_component_returns_nonzero(self, capsys):
        rc = strategy_cmd.handle_inspect_component(
            _args(component_name="nonexistent_component_xyz")
        )
        assert rc != 0

    def test_unknown_component_output_contains_error(self, capsys):
        strategy_cmd.handle_inspect_component(_args(component_name="nonexistent_component_xyz"))
        out = capsys.readouterr().out
        parsed = json.loads(out.strip().split("\n", 1)[1])
        assert "error" in parsed

    def test_output_contains_required_fields(self, capsys):
        from autonomous_trading_platform.strategy.components import get_component_registry

        component_names = [
            defn.component_name for defn in get_component_registry().list_components()
        ]
        strategy_cmd.handle_inspect_component(_args(component_name=component_names[0]))
        out = capsys.readouterr().out
        parsed = json.loads(out.strip().split("\n", 1)[1])
        for key in ("component_name", "component_type", "is_executable", "warmup", "compatibility"):
            assert key in parsed


# ---------------------------------------------------------------------------
# list (DB-backed)
# ---------------------------------------------------------------------------


class TestHandleList:
    def test_returns_zero_empty_db(self, db_session: Session, monkeypatch, capsys):
        _patch_catalog_deps(monkeypatch, db_session)
        rc = strategy_cmd.handle_list(_args(status=None))
        assert rc == 0

    def test_output_is_parseable_json(self, db_session: Session, monkeypatch, capsys):
        _patch_catalog_deps(monkeypatch, db_session)
        strategy_cmd.handle_list(_args(status=None))
        out = capsys.readouterr().out
        parsed = json.loads(out.strip().split("\n", 1)[1])
        assert "count" in parsed
        assert "strategies" in parsed

    def test_seeded_strategy_appears(self, db_session: Session, monkeypatch, capsys):
        _patch_catalog_deps(monkeypatch, db_session)
        _seed_strategy(db_session, strategy_id="strat_test_list")
        strategy_cmd.handle_list(_args(status=None))
        out = capsys.readouterr().out
        parsed = json.loads(out.strip().split("\n", 1)[1])
        ids = [s["strategy_id"] for s in parsed["strategies"]]
        assert "strat_test_list" in ids

    def test_status_filter_paper(self, db_session: Session, monkeypatch, capsys):
        _patch_catalog_deps(monkeypatch, db_session)
        _seed_strategy(db_session, strategy_id="strat_paper", state="approved_for_paper_trading")
        strategy_cmd.handle_list(_args(status="paper"))
        out = capsys.readouterr().out
        parsed = json.loads(out.strip().split("\n", 1)[1])
        for row in parsed["strategies"]:
            assert row["status"] == "paper"


# ---------------------------------------------------------------------------
# inspect (DB-backed)
# ---------------------------------------------------------------------------


class TestHandleInspect:
    def test_found_strategy_returns_zero(self, db_session: Session, monkeypatch, capsys):
        _patch_catalog_deps(monkeypatch, db_session)
        _seed_strategy(db_session, strategy_id="strat_inspect_test")
        rc = strategy_cmd.handle_inspect(_args(strategy_id="strat_inspect_test"))
        assert rc == 0

    def test_found_strategy_output_contains_detail_fields(
        self, db_session: Session, monkeypatch, capsys
    ):
        _patch_catalog_deps(monkeypatch, db_session)
        _seed_strategy(db_session, strategy_id="strat_inspect_fields")
        strategy_cmd.handle_inspect(_args(strategy_id="strat_inspect_fields"))
        out = capsys.readouterr().out
        parsed = json.loads(out.strip().split("\n", 1)[1])
        for key in ("strategy_id", "metrics", "deployment_history"):
            assert key in parsed

    def test_missing_strategy_returns_nonzero(self, db_session: Session, monkeypatch, capsys):
        _patch_catalog_deps(monkeypatch, db_session)
        rc = strategy_cmd.handle_inspect(_args(strategy_id="does_not_exist"))
        assert rc != 0

    def test_missing_strategy_output_contains_error(self, db_session: Session, monkeypatch, capsys):
        _patch_catalog_deps(monkeypatch, db_session)
        strategy_cmd.handle_inspect(_args(strategy_id="does_not_exist"))
        out = capsys.readouterr().out
        parsed = json.loads(out.strip().split("\n", 1)[1])
        assert "error" in parsed


# ---------------------------------------------------------------------------
# compare (DB-backed)
# ---------------------------------------------------------------------------


class TestHandleCompare:
    def test_two_strategies_returns_zero(self, db_session: Session, monkeypatch, capsys):
        _patch_catalog_deps(monkeypatch, db_session)
        _seed_strategy(db_session, strategy_id="strat_cmp_a")
        _seed_strategy(db_session, strategy_id="strat_cmp_b")
        rc = strategy_cmd.handle_compare(_args(strategy_ids="strat_cmp_a,strat_cmp_b"))
        assert rc == 0

    def test_output_contains_rows_and_metadata(self, db_session: Session, monkeypatch, capsys):
        _patch_catalog_deps(monkeypatch, db_session)
        _seed_strategy(db_session, strategy_id="strat_cmp_c")
        _seed_strategy(db_session, strategy_id="strat_cmp_d")
        strategy_cmd.handle_compare(_args(strategy_ids="strat_cmp_c,strat_cmp_d"))
        out = capsys.readouterr().out
        parsed = json.loads(out.strip().split("\n", 1)[1])
        assert "rows" in parsed
        assert "metadata" in parsed
        assert len(parsed["rows"]) == 2

    def test_single_id_returns_nonzero(self, db_session: Session, monkeypatch, capsys):
        _patch_catalog_deps(monkeypatch, db_session)
        rc = strategy_cmd.handle_compare(_args(strategy_ids="strat_only_one"))
        assert rc != 0


# ---------------------------------------------------------------------------
# equity-curve (DB-backed)
# ---------------------------------------------------------------------------


class TestHandleEquityCurve:
    def test_found_strategy_no_runs_returns_zero(self, db_session: Session, monkeypatch, capsys):
        _patch_catalog_deps(monkeypatch, db_session)
        _seed_strategy(db_session, strategy_id="strat_ec_test")
        rc = strategy_cmd.handle_equity_curve(_args(strategy_id="strat_ec_test"))
        assert rc == 0

    def test_output_contains_strategy_id_and_points(self, db_session: Session, monkeypatch, capsys):
        _patch_catalog_deps(monkeypatch, db_session)
        _seed_strategy(db_session, strategy_id="strat_ec_fields")
        strategy_cmd.handle_equity_curve(_args(strategy_id="strat_ec_fields"))
        out = capsys.readouterr().out
        parsed = json.loads(out.strip().split("\n", 1)[1])
        assert parsed["strategy_id"] == "strat_ec_fields"
        assert "points" in parsed

    def test_missing_strategy_returns_nonzero(self, db_session: Session, monkeypatch, capsys):
        _patch_catalog_deps(monkeypatch, db_session)
        rc = strategy_cmd.handle_equity_curve(_args(strategy_id="no_such_strat"))
        assert rc != 0


# ---------------------------------------------------------------------------
# active (DB-backed)
# ---------------------------------------------------------------------------


class TestHandleActive:
    def test_returns_zero_empty_db(self, db_session: Session, monkeypatch, capsys):
        _patch_catalog_deps(monkeypatch, db_session)
        rc = strategy_cmd.handle_active(_args())
        assert rc == 0

    def test_output_contains_count_and_strategies(self, db_session: Session, monkeypatch, capsys):
        _patch_catalog_deps(monkeypatch, db_session)
        strategy_cmd.handle_active(_args())
        out = capsys.readouterr().out
        parsed = json.loads(out.strip().split("\n", 1)[1])
        assert "count" in parsed
        assert "strategies" in parsed
