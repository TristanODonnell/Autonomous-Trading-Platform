# test_smoke.py


import autonomous_trading_platform as atp
import autonomous_trading_platform.backtesting
import autonomous_trading_platform.cli
import autonomous_trading_platform.common
import autonomous_trading_platform.contracts
import autonomous_trading_platform.execution
import autonomous_trading_platform.ingestion
import autonomous_trading_platform.scheduler
import autonomous_trading_platform.storage
import autonomous_trading_platform.strategy


def test_imports_smoke() -> None:
    assert atp is not None
