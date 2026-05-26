import pytest
from sqlalchemy.orm import Session

from autonomous_trading_platform.config.settings import Settings
from autonomous_trading_platform.contracts.common.enums import RunType
from autonomous_trading_platform.safety.environment_policy import EnvironmentSafetyPolicy
from autonomous_trading_platform.safety.errors import (
    ConfigGateDisabledError,
    LiveTradingBlockedError,
    RuntimeGateNotArmedError,
)
from autonomous_trading_platform.safety.services.kill_switch_service import KillSwitchService
from autonomous_trading_platform.safety.services.live_trading_gate_service import (
    LiveTradingGateService,
)
from autonomous_trading_platform.safety.services.runtime_gate_service import (
    RuntimeGateService,
)
from autonomous_trading_platform.safety.services.runtime_trading_guard_service import (
    RuntimeTradingGuardService,
)
from autonomous_trading_platform.storage.sor.repositories.core.kill_switch_state_repository import (
    KillSwitchStateRepository,
)


def _base_env(monkeypatch) -> None:
    monkeypatch.setenv("APP_ENV", "local")
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+psycopg://ratp:ratp_password@localhost:5432/ratp",
    )


def _paper_env(monkeypatch) -> None:
    _base_env(monkeypatch)
    monkeypatch.setenv("TRADING_ENVIRONMENT", "paper")
    monkeypatch.setenv("NO_LIVE_TRADING", "true")
    monkeypatch.setenv("ENABLE_LIVE_TRADING", "false")
    monkeypatch.setenv("PAPER_BROKER_API_KEY", "paper-key")
    monkeypatch.setenv("PAPER_BROKER_API_SECRET", "paper-secret")
    monkeypatch.setenv("PAPER_ALLOWED_ACCOUNT_IDS", "paper-1")


def _live_env(monkeypatch) -> None:
    _base_env(monkeypatch)
    monkeypatch.setenv("TRADING_ENVIRONMENT", "live")
    monkeypatch.setenv("NO_LIVE_TRADING", "false")
    monkeypatch.setenv("ENABLE_LIVE_TRADING", "true")
    monkeypatch.setenv("INCLUDE_LIVE_MODULES", "true")
    monkeypatch.setenv("LIVE_BROKER_API_KEY", "live-key")
    monkeypatch.setenv("LIVE_BROKER_API_SECRET", "live-secret")
    monkeypatch.setenv("LIVE_ALLOWED_ACCOUNT_IDS", "live-1")


def _build_guard(
    db_session: Session,
) -> tuple[RuntimeTradingGuardService, RuntimeGateService]:
    settings = Settings()
    environment_policy = EnvironmentSafetyPolicy(settings)
    runtime_gate_service = RuntimeGateService()
    live_trading_gate_service = LiveTradingGateService(
        environment_policy=environment_policy,
        runtime_gate_service=runtime_gate_service,
        kill_switch_service=KillSwitchService(
            repository=KillSwitchStateRepository(session=db_session),
        ),
    )

    return (
        RuntimeTradingGuardService(
            settings=settings,
            environment_policy=environment_policy,
            runtime_gate_service=runtime_gate_service,
            live_trading_gate_service=live_trading_gate_service,
        ),
        runtime_gate_service,
    )


def test_paper_mode_allowed_without_runtime_gate_arming(monkeypatch, db_session: Session) -> None:
    _paper_env(monkeypatch)
    guard, runtime_gate_service = _build_guard(db_session)

    assert runtime_gate_service.is_armed() is False
    guard.assert_trading_mode_allowed(account_id="paper-1", run_type=RunType.PAPER)


def test_paper_mode_blocks_live_run_type(monkeypatch, db_session: Session) -> None:
    _paper_env(monkeypatch)
    guard, _ = _build_guard(db_session)

    with pytest.raises(LiveTradingBlockedError, match="run type does not match"):
        guard.assert_trading_mode_allowed(account_id="paper-1", run_type=RunType.LIVE)


def test_paper_mode_respects_account_allowlist(monkeypatch, db_session: Session) -> None:
    _paper_env(monkeypatch)
    guard, _ = _build_guard(db_session)

    with pytest.raises(LiveTradingBlockedError, match="Paper account paper-2"):
        guard.assert_trading_mode_allowed(account_id="paper-2", run_type=RunType.PAPER)


def test_live_mode_blocked_by_default(monkeypatch, db_session: Session) -> None:
    _base_env(monkeypatch)
    monkeypatch.setenv("TRADING_ENVIRONMENT", "live")
    monkeypatch.setenv("LIVE_BROKER_API_KEY", "live-key")
    monkeypatch.setenv("LIVE_BROKER_API_SECRET", "live-secret")

    guard, _ = _build_guard(db_session)

    with pytest.raises(ConfigGateDisabledError, match="NO_LIVE_TRADING"):
        guard.assert_trading_mode_allowed(account_id="live-1", run_type=RunType.LIVE)


def test_live_mode_blocked_when_no_live_trading_true(monkeypatch, db_session: Session) -> None:
    _live_env(monkeypatch)
    monkeypatch.setenv("NO_LIVE_TRADING", "true")
    guard, _ = _build_guard(db_session)

    with pytest.raises(ConfigGateDisabledError, match="NO_LIVE_TRADING"):
        guard.assert_trading_mode_allowed(account_id="live-1", run_type=RunType.LIVE)


def test_live_mode_blocked_when_enable_live_trading_false(monkeypatch, db_session: Session) -> None:
    _live_env(monkeypatch)
    monkeypatch.setenv("ENABLE_LIVE_TRADING", "false")
    guard, _ = _build_guard(db_session)

    with pytest.raises(ConfigGateDisabledError, match="ENABLE_LIVE_TRADING"):
        guard.assert_trading_mode_allowed(account_id="live-1", run_type=RunType.LIVE)


def test_live_mode_blocked_when_runtime_gate_not_armed(monkeypatch, db_session: Session) -> None:
    _live_env(monkeypatch)
    guard, _ = _build_guard(db_session)

    with pytest.raises(RuntimeGateNotArmedError):
        guard.assert_trading_mode_allowed(account_id="live-1", run_type=RunType.LIVE)


def test_live_mode_allowed_only_when_all_safety_conditions_pass(
    monkeypatch, db_session: Session
) -> None:
    _live_env(monkeypatch)
    guard, runtime_gate_service = _build_guard(db_session)
    runtime_gate_service.arm(reason="supervised test", armed_by="tester")

    guard.assert_trading_mode_allowed(account_id="live-1", run_type=RunType.LIVE)


def test_live_mode_respects_account_allowlist(monkeypatch, db_session: Session) -> None:
    _live_env(monkeypatch)
    guard, runtime_gate_service = _build_guard(db_session)
    runtime_gate_service.arm(reason="supervised test", armed_by="tester")

    with pytest.raises(LiveTradingBlockedError, match="Live account live-2"):
        guard.assert_trading_mode_allowed(account_id="live-2", run_type=RunType.LIVE)
