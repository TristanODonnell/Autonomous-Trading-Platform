from __future__ import annotations

import argparse
import json
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from autonomous_trading_platform.cli.commands import admin
from autonomous_trading_platform.cli.main import build_parser
from autonomous_trading_platform.contracts.common.enums import BarInterval, PriceBasis, RunType
from autonomous_trading_platform.governance.models.governance_state import GovernanceState
from autonomous_trading_platform.storage.sor.models.run_manifests import RunManifestRow

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _args(**kwargs) -> argparse.Namespace:
    defaults = dict(
        json_only=False,
        include_broker=False,
        limit=25,
        run_id=None,
        action_type=None,
        strategy_id=None,
        user=None,
        from_date=None,
        to_date=None,
        page=1,
        page_size=25,
    )
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


def _seed_failed_row(
    db_session: Session,
    *,
    run_id=None,
    error: str = "boom",
    status: str = "failed",
) -> RunManifestRow:
    row = RunManifestRow(
        run_id=run_id or uuid4(),
        run_type=RunType.PAPER,
        created_at=datetime(2026, 1, 1, 12, 0, tzinfo=UTC),
        environment="paper",
        broker="alpaca",
        broker_account_id="acct-1",
        strategy_id="strat-1",
        strategy_version="v1",
        strategy_config={},
        capital_bucket=Decimal("10000"),
        interval=BarInterval.FIVE_MIN,
        start_date=date(2026, 1, 1),
        end_date=None,
        dataset_version="v1",
        price_basis=PriceBasis.RAW,
        universe_version="v1",
        cost_model=None,
        fill_model=None,
        random_seed=None,
        git_commit="abc1234",
        docker_image=None,
        python_version=None,
        dependency_lock_hash=None,
        notes=None,
        bar_timestamp=datetime(2026, 1, 1, 12, 0, tzinfo=UTC),
        status=status,
        current_step="evaluation",
        last_successful_step="ingestion",
        error_message=error,
        artifact_manifest=None,
        schema_definition=None,
        governance_state=GovernanceState.PROPOSED,
    )
    db_session.add(row)
    db_session.flush()
    return row


# ---------------------------------------------------------------------------
# inspect-config
# ---------------------------------------------------------------------------


class TestHandleShowConfig:
    def test_returns_zero(self, monkeypatch):
        monkeypatch.setenv("VALIDATE_BROKER_CONFIG", "false")
        assert admin.handle_show_config(_args()) == 0

    def test_output_contains_json(self, monkeypatch, capsys):
        monkeypatch.setenv("VALIDATE_BROKER_CONFIG", "false")
        admin.handle_show_config(_args())
        captured = capsys.readouterr().out
        parsed = json.loads(captured[captured.index("{") :])
        assert "app_env" in parsed

    def test_secrets_are_redacted(self, monkeypatch, capsys):
        monkeypatch.setenv("VALIDATE_BROKER_CONFIG", "false")
        monkeypatch.setenv("PAPER_BROKER_API_KEY", "my-secret-key")
        admin.handle_show_config(_args())
        captured = capsys.readouterr().out
        assert "my-secret-key" not in captured
        parsed = json.loads(captured[captured.index("{") :])
        assert parsed["paper_broker_api_key"] == "<redacted>"

    def test_database_url_is_redacted(self, monkeypatch, capsys):
        monkeypatch.setenv("VALIDATE_BROKER_CONFIG", "false")
        admin.handle_show_config(_args())
        captured = capsys.readouterr().out
        parsed = json.loads(captured[captured.index("{") :])
        assert parsed["database_url"] == "<redacted>"

    def test_json_only_flag_omits_header(self, monkeypatch, capsys):
        monkeypatch.setenv("VALIDATE_BROKER_CONFIG", "false")
        admin.handle_show_config(_args(json_only=True))
        captured = capsys.readouterr().out
        assert "===" not in captured
        json.loads(captured)  # must be pure JSON with no header noise

    def test_without_json_flag_includes_header(self, monkeypatch, capsys):
        monkeypatch.setenv("VALIDATE_BROKER_CONFIG", "false")
        admin.handle_show_config(_args(json_only=False))
        captured = capsys.readouterr().out
        assert "Config" in captured


# ---------------------------------------------------------------------------
# inspect-env
# ---------------------------------------------------------------------------


class TestHandleShowEnv:
    def test_returns_zero(self, monkeypatch):
        monkeypatch.setenv("VALIDATE_BROKER_CONFIG", "false")
        assert admin.handle_show_env(_args()) == 0

    def test_kv_output_contains_app_env(self, monkeypatch, capsys):
        monkeypatch.setenv("VALIDATE_BROKER_CONFIG", "false")
        admin.handle_show_env(_args())
        assert "APP_ENV" in capsys.readouterr().out

    def test_json_mode_is_parseable(self, monkeypatch, capsys):
        monkeypatch.setenv("VALIDATE_BROKER_CONFIG", "false")
        admin.handle_show_env(_args(json_only=True))
        captured = capsys.readouterr().out
        assert "===" not in captured
        parsed = json.loads(captured)
        assert "APP_ENV" in parsed

    def test_set_credential_shown_as_set(self, monkeypatch, capsys):
        monkeypatch.setenv("VALIDATE_BROKER_CONFIG", "false")
        monkeypatch.setenv("PAPER_BROKER_API_KEY", "k")
        admin.handle_show_env(_args(json_only=True))
        parsed = json.loads(capsys.readouterr().out)
        assert parsed["PAPER_BROKER_API_KEY"] == "<set>"

    def test_credential_field_uses_set_or_unset_sentinel(self, monkeypatch, capsys):
        monkeypatch.setenv("VALIDATE_BROKER_CONFIG", "false")
        admin.handle_show_env(_args(json_only=True))
        parsed = json.loads(capsys.readouterr().out)
        # Actual value depends on environment; we only care that raw secrets are never emitted
        assert parsed["LIVE_BROKER_API_KEY"] in ("<set>", "<unset>")
        assert parsed["LIVE_BROKER_API_SECRET"] in ("<set>", "<unset>")

    def test_json_mode_omits_header(self, monkeypatch, capsys):
        monkeypatch.setenv("VALIDATE_BROKER_CONFIG", "false")
        admin.handle_show_env(_args(json_only=True))
        assert "===" not in capsys.readouterr().out


# ---------------------------------------------------------------------------
# validate-config
# ---------------------------------------------------------------------------


class TestHandleValidateConfig:
    def test_passes_with_valid_env(self, monkeypatch, capsys):
        monkeypatch.setenv("VALIDATE_BROKER_CONFIG", "false")
        result = admin.handle_validate_config(_args())
        assert result == 0
        assert "[PASS]" in capsys.readouterr().out

    def test_fails_when_settings_raises(self, monkeypatch, capsys):
        monkeypatch.setenv("VALIDATE_BROKER_CONFIG", "false")
        monkeypatch.setattr(
            "autonomous_trading_platform.cli.commands.admin.Settings",
            lambda: (_ for _ in ()).throw(
                RuntimeError("Missing required environment variable: APP_ENV")
            ),
        )
        result = admin.handle_validate_config(_args())
        assert result == 1
        assert "[FAIL]" in capsys.readouterr().out

    def test_include_broker_flag_appears_in_output(self, monkeypatch, capsys):
        monkeypatch.setenv("VALIDATE_BROKER_CONFIG", "false")
        admin.handle_validate_config(_args(include_broker=True))
        assert "Broker credentials" in capsys.readouterr().out

    def test_include_broker_fails_when_validator_raises(self, monkeypatch, capsys):
        monkeypatch.setenv("VALIDATE_BROKER_CONFIG", "false")

        def _reject(**kwargs):
            raise RuntimeError("Missing PAPER_BROKER_API_KEY")

        monkeypatch.setattr(
            "autonomous_trading_platform.config.broker_config_validator.validate_broker_environment",
            _reject,
        )
        result = admin.handle_validate_config(_args(include_broker=True))
        captured = capsys.readouterr().out
        assert result == 1
        assert "[FAIL]" in captured
        assert "Broker credentials" in captured


# ---------------------------------------------------------------------------
# inspect-failed-runs
# ---------------------------------------------------------------------------


class TestHandleListFailedRuns:
    def test_returns_zero_on_empty_db(self, monkeypatch, db_session, capsys):
        monkeypatch.setenv("VALIDATE_BROKER_CONFIG", "false")
        monkeypatch.setattr(admin, "get_session", lambda: db_session)
        assert admin.handle_list_failed_runs(_args(limit=10)) == 0

    def test_lists_seeded_failed_row(self, monkeypatch, db_session, capsys):
        monkeypatch.setenv("VALIDATE_BROKER_CONFIG", "false")
        monkeypatch.setattr(admin, "get_session", lambda: db_session)
        row = _seed_failed_row(db_session)
        admin.handle_list_failed_runs(_args(limit=25))
        out = capsys.readouterr().out
        parsed = json.loads(out[out.index("{") :])
        assert parsed["count"] == 1
        assert parsed["runs"][0]["run_id"] == str(row.run_id)
        assert parsed["runs"][0]["error_message"] == "boom"

    def test_completed_run_not_listed(self, monkeypatch, db_session, capsys):
        monkeypatch.setenv("VALIDATE_BROKER_CONFIG", "false")
        monkeypatch.setattr(admin, "get_session", lambda: db_session)
        _seed_failed_row(db_session, status="completed", error="")
        admin.handle_list_failed_runs(_args(limit=25))
        out = capsys.readouterr().out
        parsed = json.loads(out[out.index("{") :])
        assert parsed["count"] == 0

    def test_limit_zero_returns_error(self, monkeypatch, db_session, capsys):
        monkeypatch.setenv("VALIDATE_BROKER_CONFIG", "false")
        monkeypatch.setattr(admin, "get_session", lambda: db_session)
        assert admin.handle_list_failed_runs(_args(limit=0)) == 1
        assert "[ERROR]" in capsys.readouterr().out

    def test_limit_negative_returns_error(self, monkeypatch, db_session, capsys):
        monkeypatch.setenv("VALIDATE_BROKER_CONFIG", "false")
        monkeypatch.setattr(admin, "get_session", lambda: db_session)
        assert admin.handle_list_failed_runs(_args(limit=-5)) == 1

    def test_limit_over_max_returns_error(self, monkeypatch, db_session, capsys):
        monkeypatch.setenv("VALIDATE_BROKER_CONFIG", "false")
        monkeypatch.setattr(admin, "get_session", lambda: db_session)
        assert admin.handle_list_failed_runs(_args(limit=9999)) == 1

    def test_respects_limit(self, monkeypatch, db_session, capsys):
        monkeypatch.setenv("VALIDATE_BROKER_CONFIG", "false")
        monkeypatch.setattr(admin, "get_session", lambda: db_session)
        for _ in range(5):
            _seed_failed_row(db_session)
        admin.handle_list_failed_runs(_args(limit=3))
        out = capsys.readouterr().out
        parsed = json.loads(out[out.index("{") :])
        assert parsed["count"] <= 3


# ---------------------------------------------------------------------------
# inspect-failed-run (single run detail)
# ---------------------------------------------------------------------------


class TestHandleInspectFailedRun:
    def test_found_returns_zero_and_correct_data(self, monkeypatch, db_session, capsys):
        monkeypatch.setenv("VALIDATE_BROKER_CONFIG", "false")
        monkeypatch.setattr(admin, "get_session", lambda: db_session)
        row = _seed_failed_row(db_session)
        result = admin.handle_inspect_failed_run(_args(run_id=str(row.run_id)))
        assert result == 0
        out = capsys.readouterr().out
        parsed = json.loads(out[out.index("{") :])
        assert parsed["run_id"] == str(row.run_id)
        assert parsed["status"] == "failed"
        assert parsed["error_message"] == "boom"

    def test_shows_governance_state(self, monkeypatch, db_session, capsys):
        monkeypatch.setenv("VALIDATE_BROKER_CONFIG", "false")
        monkeypatch.setattr(admin, "get_session", lambda: db_session)
        row = _seed_failed_row(db_session)
        admin.handle_inspect_failed_run(_args(run_id=str(row.run_id)))
        out = capsys.readouterr().out
        parsed = json.loads(out[out.index("{") :])
        assert parsed["governance_state"] == "proposed"

    def test_not_found_returns_one(self, monkeypatch, db_session, capsys):
        monkeypatch.setenv("VALIDATE_BROKER_CONFIG", "false")
        monkeypatch.setattr(admin, "get_session", lambda: db_session)
        result = admin.handle_inspect_failed_run(_args(run_id=str(uuid4())))
        assert result == 1
        assert "[ERROR]" in capsys.readouterr().out

    def test_invalid_uuid_returns_one(self, monkeypatch, db_session, capsys):
        monkeypatch.setenv("VALIDATE_BROKER_CONFIG", "false")
        monkeypatch.setattr(admin, "get_session", lambda: db_session)
        result = admin.handle_inspect_failed_run(_args(run_id="not-a-uuid"))
        assert result == 1
        assert "[ERROR]" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# inspect-audit-log
# ---------------------------------------------------------------------------


class TestHandleInspectAuditLog:
    def test_returns_zero_on_empty_log(self, monkeypatch, db_session, capsys):
        monkeypatch.setenv("VALIDATE_BROKER_CONFIG", "false")
        monkeypatch.setattr(admin, "get_session", lambda: db_session)
        result = admin.handle_inspect_audit_log(_args())
        assert result == 0
        out = capsys.readouterr().out
        parsed = json.loads(out[out.index("{") :])
        assert "events" in parsed
        assert "total" in parsed

    def test_output_includes_pagination_fields(self, monkeypatch, db_session, capsys):
        monkeypatch.setenv("VALIDATE_BROKER_CONFIG", "false")
        monkeypatch.setattr(admin, "get_session", lambda: db_session)
        admin.handle_inspect_audit_log(_args(page=2, page_size=10))
        out = capsys.readouterr().out
        parsed = json.loads(out[out.index("{") :])
        assert parsed["page"] == 2
        assert parsed["page_size"] == 10

    def test_invalid_from_date_returns_one(self, monkeypatch, db_session, capsys):
        monkeypatch.setenv("VALIDATE_BROKER_CONFIG", "false")
        monkeypatch.setattr(admin, "get_session", lambda: db_session)
        result = admin.handle_inspect_audit_log(_args(from_date="not-a-date"))
        assert result == 1
        assert "[ERROR]" in capsys.readouterr().out

    def test_invalid_to_date_returns_one(self, monkeypatch, db_session, capsys):
        monkeypatch.setenv("VALIDATE_BROKER_CONFIG", "false")
        monkeypatch.setattr(admin, "get_session", lambda: db_session)
        result = admin.handle_inspect_audit_log(_args(to_date="bad"))
        assert result == 1

    def test_valid_iso_dates_accepted(self, monkeypatch, db_session, capsys):
        monkeypatch.setenv("VALIDATE_BROKER_CONFIG", "false")
        monkeypatch.setattr(admin, "get_session", lambda: db_session)
        result = admin.handle_inspect_audit_log(
            _args(from_date="2026-01-01T00:00:00+00:00", to_date="2026-12-31T23:59:59+00:00")
        )
        assert result == 0


# ---------------------------------------------------------------------------
# inspect-db
# ---------------------------------------------------------------------------


class TestHandleInspectDb:
    def test_passes_with_sqlite_test_db(self, monkeypatch, db_session, capsys):
        monkeypatch.setenv("VALIDATE_BROKER_CONFIG", "false")
        monkeypatch.setattr(admin, "get_session", lambda: db_session)
        result = admin.handle_inspect_db(_args())
        assert result == 0
        captured = capsys.readouterr().out
        assert "[PASS]" in captured
        assert "DB connectivity" in captured

    def test_fails_when_database_url_not_set(self, monkeypatch, capsys):
        monkeypatch.setenv("VALIDATE_BROKER_CONFIG", "false")
        monkeypatch.setattr(
            "autonomous_trading_platform.cli.commands.admin.os.getenv",
            lambda key, *args: None if key == "DATABASE_URL" else (args[0] if args else None),
        )
        result = admin.handle_inspect_db(_args())
        assert result == 1
        captured = capsys.readouterr().out
        assert "[FAIL]" in captured


# ---------------------------------------------------------------------------
# doctor
# ---------------------------------------------------------------------------


class TestHandleDoctor:
    def test_passes_with_valid_env_and_db(self, monkeypatch, db_session, capsys):
        monkeypatch.setenv("VALIDATE_BROKER_CONFIG", "false")
        monkeypatch.setattr(admin, "get_session", lambda: db_session)
        result = admin.handle_doctor(_args())
        assert result == 0
        captured = capsys.readouterr().out
        assert "[PASS]" in captured
        assert "DB connectivity" in captured

    def test_include_broker_runs_credential_check(self, monkeypatch, db_session, capsys):
        monkeypatch.setenv("VALIDATE_BROKER_CONFIG", "false")
        monkeypatch.setattr(admin, "get_session", lambda: db_session)
        admin.handle_doctor(_args(include_broker=True))
        assert "Broker credentials" in capsys.readouterr().out

    def test_include_broker_fails_when_validator_raises(self, monkeypatch, db_session, capsys):
        monkeypatch.setenv("VALIDATE_BROKER_CONFIG", "false")
        monkeypatch.setattr(admin, "get_session", lambda: db_session)

        def _reject(**kwargs):
            raise RuntimeError("Missing PAPER_BROKER_API_KEY")

        monkeypatch.setattr(
            "autonomous_trading_platform.config.broker_config_validator.validate_broker_environment",
            _reject,
        )
        result = admin.handle_doctor(_args(include_broker=True))
        assert result == 1
        assert "[FAIL]" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# Parser wiring smoke tests
# ---------------------------------------------------------------------------


class TestParserRegistration:
    @pytest.mark.parametrize(
        "argv",
        [
            ["admin", "--help"],
            ["admin", "inspect-config", "--help"],
            ["admin", "inspect-env", "--help"],
            ["admin", "validate-config", "--help"],
            ["admin", "inspect-failed-runs", "--help"],
            ["admin", "inspect-failed-run", "--help"],
            ["admin", "inspect-audit-log", "--help"],
            ["admin", "inspect-db", "--help"],
            ["admin", "doctor", "--help"],
        ],
    )
    def test_help_exits_cleanly(self, argv):
        parser = build_parser()
        with pytest.raises(SystemExit) as exc:
            parser.parse_args(argv)
        assert exc.value.code == 0

    def test_inspect_config_json_flag(self):
        parser = build_parser()
        ns = parser.parse_args(["admin", "inspect-config", "--json"])
        assert ns.json_only is True

    def test_inspect_env_json_flag(self):
        parser = build_parser()
        ns = parser.parse_args(["admin", "inspect-env", "--json"])
        assert ns.json_only is True

    def test_validate_config_include_broker_flag(self):
        parser = build_parser()
        ns = parser.parse_args(["admin", "validate-config", "--include-broker"])
        assert ns.include_broker is True

    def test_doctor_include_broker_flag(self):
        parser = build_parser()
        ns = parser.parse_args(["admin", "doctor", "--include-broker"])
        assert ns.include_broker is True

    def test_inspect_failed_run_requires_run_id(self):
        parser = build_parser()
        with pytest.raises(SystemExit) as exc:
            parser.parse_args(["admin", "inspect-failed-run"])
        assert exc.value.code != 0

    def test_inspect_failed_runs_default_limit(self):
        parser = build_parser()
        ns = parser.parse_args(["admin", "inspect-failed-runs"])
        assert ns.limit == 25

    def test_runtime_list_failed_runs_registered(self):
        parser = build_parser()
        ns = parser.parse_args(["runtime", "list-failed-runs"])
        assert ns.limit == 25

    def test_admin_parser_help_text_not_misleading(self):
        import argparse as _ap

        from autonomous_trading_platform.cli.commands import admin as admin_mod

        p = _ap.ArgumentParser()
        subs = p.add_subparsers()
        admin_mod.register(subs)
        # The help string on the admin subparser action should no longer be
        # "Admin cycle operations" (the old misleading text from the audit).
        for action in p._subparsers._actions:
            if hasattr(action, "_choices_actions"):
                for choice_action in action._choices_actions:
                    if choice_action.dest == "admin":
                        assert "cycle" not in choice_action.help.lower()
                        return
