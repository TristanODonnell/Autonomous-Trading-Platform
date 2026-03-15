import pytest

from autonomous_trading_platform.config.settings import Settings
from autonomous_trading_platform.safety.environment_policy import EnvironmentSafetyPolicy
from autonomous_trading_platform.safety.errors import (
    BuildGateDisabledError,
    ConfigGateDisabledError,
    KillSwitchEnabledError,
    LiveTradingBlockedError,
    RuntimeGateNotArmedError,
)
from autonomous_trading_platform.safety.services.kill_switch_service import (
    KillSwitchService,
)
from autonomous_trading_platform.safety.services.live_trading_gate_service import (
    LiveTradingGateService,
)
from autonomous_trading_platform.safety.services.runtime_gate_service import (
    RuntimeGateService,
)


def _base_env(monkeypatch) -> None:
    monkeypatch.setenv("APP_ENV", "local")
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+psycopg://ratp:ratp_password@localhost:5432/ratp",
    )


def _live_ready_env(monkeypatch) -> None:
    _base_env(monkeypatch)
    monkeypatch.setenv("TRADING_ENVIRONMENT", "live")
    monkeypatch.setenv("NO_LIVE_TRADING", "false")
    monkeypatch.setenv("ENABLE_LIVE_TRADING", "true")
    monkeypatch.setenv("INCLUDE_LIVE_MODULES", "true")
    monkeypatch.setenv("LIVE_ALLOWED_ACCOUNT_IDS", "live-1")


def _build_service(
    monkeypatch,
) -> tuple[
    LiveTradingGateService,
    RuntimeGateService,
    KillSwitchService,
]:
    settings = Settings()
    environment_policy = EnvironmentSafetyPolicy(settings)
    runtime_gate_service = RuntimeGateService()
    kill_switch_service = KillSwitchService()

    live_gate_service = LiveTradingGateService(
        environment_policy=environment_policy,
        runtime_gate_service=runtime_gate_service,
        kill_switch_service=kill_switch_service,
    )
    return live_gate_service, runtime_gate_service, kill_switch_service


def test_live_gate_blocks_when_environment_is_not_live(monkeypatch) -> None:
    _base_env(monkeypatch)
    monkeypatch.setenv("TRADING_ENVIRONMENT", "paper")

    live_gate_service, _, _ = _build_service(monkeypatch)

    with pytest.raises(LiveTradingBlockedError, match="not LIVE"):
        live_gate_service.assert_live_trading_allowed(account_id="paper-1")


def test_live_gate_blocks_when_runtime_gate_not_armed(monkeypatch) -> None:
    _live_ready_env(monkeypatch)
    live_gate_service, _, _ = _build_service(monkeypatch)

    with pytest.raises(RuntimeGateNotArmedError):
        live_gate_service.assert_live_trading_allowed(account_id="live-1")


def test_live_gate_blocks_when_kill_switch_enabled(monkeypatch) -> None:
    _live_ready_env(monkeypatch)
    live_gate_service, runtime_gate_service, kill_switch_service = _build_service(monkeypatch)

    runtime_gate_service.arm(
        reason="supervised session",
        armed_by="tester",
    )
    kill_switch_service.enable(
        reason="emergency stop",
        updated_by="tester",
    )

    with pytest.raises(KillSwitchEnabledError, match="emergency stop"):
        live_gate_service.assert_live_trading_allowed(account_id="live-1")


def test_live_gate_allows_trading_when_all_gates_pass(monkeypatch) -> None:
    _live_ready_env(monkeypatch)
    live_gate_service, runtime_gate_service, _ = _build_service(monkeypatch)

    runtime_gate_service.arm(
        reason="supervised session",
        armed_by="tester",
    )

    live_gate_service.assert_live_trading_allowed(account_id="live-1")
    assert live_gate_service.can_trade_live(account_id="live-1") is True


def test_live_gate_blocks_when_account_not_allowlisted(monkeypatch) -> None:
    _live_ready_env(monkeypatch)
    live_gate_service, runtime_gate_service, _ = _build_service(monkeypatch)

    runtime_gate_service.arm(
        reason="supervised session",
        armed_by="tester",
    )

    with pytest.raises(LiveTradingBlockedError, match="not allowlisted"):
        live_gate_service.assert_live_trading_allowed(account_id="live-2")


def test_live_gate_maps_missing_live_modules_to_build_gate_error(monkeypatch) -> None:
    _base_env(monkeypatch)
    monkeypatch.setenv("TRADING_ENVIRONMENT", "live")
    monkeypatch.setenv("NO_LIVE_TRADING", "false")
    monkeypatch.setenv("ENABLE_LIVE_TRADING", "true")
    monkeypatch.setenv("INCLUDE_LIVE_MODULES", "false")
    monkeypatch.setenv("LIVE_ALLOWED_ACCOUNT_IDS", "live-1")

    live_gate_service, runtime_gate_service, _ = _build_service(monkeypatch)
    runtime_gate_service.arm(reason="supervised session", armed_by="tester")

    with pytest.raises(BuildGateDisabledError):
        live_gate_service.assert_live_trading_allowed(account_id="live-1")


def test_live_gate_maps_config_disable_to_config_gate_error(monkeypatch) -> None:
    _base_env(monkeypatch)
    monkeypatch.setenv("TRADING_ENVIRONMENT", "live")
    monkeypatch.setenv("NO_LIVE_TRADING", "false")
    monkeypatch.setenv("ENABLE_LIVE_TRADING", "false")
    monkeypatch.setenv("INCLUDE_LIVE_MODULES", "true")
    monkeypatch.setenv("LIVE_ALLOWED_ACCOUNT_IDS", "live-1")

    live_gate_service, runtime_gate_service, _ = _build_service(monkeypatch)
    runtime_gate_service.arm(reason="supervised session", armed_by="tester")

    with pytest.raises(ConfigGateDisabledError):
        live_gate_service.assert_live_trading_allowed(account_id="live-1")


def test_live_gate_status_reports_failures(monkeypatch) -> None:
    _live_ready_env(monkeypatch)
    live_gate_service, _, _ = _build_service(monkeypatch)

    status = live_gate_service.get_gate_status(account_id="live-1")

    assert status["build_and_config_ok"] is True
    assert status["account_ok"] is True
    assert status["runtime_ok"] is False
    assert status["kill_switch_ok"] is True
    assert status["overall_ok"] is False
