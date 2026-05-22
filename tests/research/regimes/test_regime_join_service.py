from __future__ import annotations

import pandas as pd

from autonomous_trading_platform.research.analysis.regimes.regime_join_service import (
    RegimeJoinService,
)


def _make_equity_curve(timestamps: list[str]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "timestamp": pd.to_datetime(timestamps, utc=True),
            "equity": [100_000.0 + i * 100 for i in range(len(timestamps))],
            "cash": [90_000.0] * len(timestamps),
            "positions_value": [10_000.0] * len(timestamps),
            "drawdown": [0.0] * len(timestamps),
        }
    )


def _make_regime_data(
    symbols: list[str],
    timestamps: list[str],
    trend_labels: list[str | None],
) -> pd.DataFrame:
    rows = []
    for sym in symbols:
        for ts, trend in zip(timestamps, trend_labels, strict=True):
            rows.append(
                {
                    "symbol": sym,
                    "timestamp": pd.Timestamp(ts, tz="UTC"),
                    "regime_trend": trend,
                    "regime_volatility": "normal_volatility",
                    "regime_liquidity": "high_liquidity",
                    "regime_mean_reversion": "trending",
                    "regime_risk": "risk_on",
                }
            )
    return pd.DataFrame(rows)


class TestJoinEquityCurve:
    def test_exact_join_single_symbol(self) -> None:
        svc = RegimeJoinService()
        timestamps = ["2023-01-03 10:00", "2023-01-04 10:00", "2023-01-05 10:00"]
        equity = _make_equity_curve(timestamps)
        regime = _make_regime_data(["AAPL"], timestamps, ["bull", "bear", "bull"])

        result = svc.join_equity_curve(equity_curve=equity, regime_data=regime)

        assert "regime_trend" in result.columns
        assert list(result.sort_values("timestamp")["regime_trend"]) == ["bull", "bear", "bull"]

    def test_mode_aggregation_two_symbols(self) -> None:
        svc = RegimeJoinService()
        timestamps = ["2023-01-03 10:00"]
        equity = _make_equity_curve(timestamps)

        rows = [
            {
                "symbol": "AAPL",
                "timestamp": pd.Timestamp("2023-01-03 10:00", tz="UTC"),
                "regime_trend": "bull",
                "regime_volatility": "normal_volatility",
                "regime_liquidity": "high_liquidity",
                "regime_mean_reversion": "trending",
                "regime_risk": "risk_on",
            },
            {
                "symbol": "MSFT",
                "timestamp": pd.Timestamp("2023-01-03 10:00", tz="UTC"),
                "regime_trend": "bull",
                "regime_volatility": "high_volatility",
                "regime_liquidity": "high_liquidity",
                "regime_mean_reversion": "trending",
                "regime_risk": "risk_on",
            },
        ]
        regime = pd.DataFrame(rows)

        result = svc.join_equity_curve(equity_curve=equity, regime_data=regime)

        assert result["regime_trend"].iloc[0] == "bull"

    def test_no_matching_timestamp_produces_nan(self) -> None:
        svc = RegimeJoinService()
        equity = _make_equity_curve(["2023-01-03 10:00"])
        regime = _make_regime_data(["AAPL"], ["2023-01-04 10:00"], ["bull"])

        result = svc.join_equity_curve(equity_curve=equity, regime_data=regime)

        assert result["regime_trend"].isna().all()

    def test_empty_regime_returns_equity_unchanged(self) -> None:
        svc = RegimeJoinService()
        equity = _make_equity_curve(["2023-01-03 10:00"])
        regime = pd.DataFrame()

        result = svc.join_equity_curve(equity_curve=equity, regime_data=regime)

        assert "regime_trend" not in result.columns
        assert len(result) == len(equity)

    def test_warmup_nans_are_preserved(self) -> None:
        svc = RegimeJoinService()
        timestamps = ["2023-01-03 10:00", "2023-01-04 10:00"]
        equity = _make_equity_curve(timestamps)
        regime = _make_regime_data(["AAPL"], timestamps, [None, "bull"])

        result = svc.join_equity_curve(equity_curve=equity, regime_data=regime)

        vals = list(result.sort_values("timestamp")["regime_trend"])
        assert vals[0] is None or pd.isna(vals[0])
        assert vals[1] == "bull"


class TestJoinSymbolFrame:
    def test_join_on_symbol_and_timestamp(self) -> None:
        svc = RegimeJoinService()
        timestamps = ["2023-01-03 10:00", "2023-01-04 10:00"]
        trade_logs = pd.DataFrame(
            {
                "symbol": ["AAPL", "AAPL"],
                "timestamp": pd.to_datetime(timestamps, utc=True),
                "side": ["buy", "sell"],
                "quantity": [10.0, 10.0],
                "price": [150.0, 155.0],
                "notional": [1500.0, 1550.0],
                "fees": [1.5, 1.5],
            }
        )
        regime = _make_regime_data(["AAPL"], timestamps, ["bull", "bear"])

        result = svc.join_symbol_frame(frame=trade_logs, regime_data=regime)

        assert "regime_trend" in result.columns
        assert list(result.sort_values("timestamp")["regime_trend"]) == ["bull", "bear"]

    def test_symbol_not_in_regime_data_produces_nan(self) -> None:
        svc = RegimeJoinService()
        trade_logs = pd.DataFrame(
            {
                "symbol": ["GOOG"],
                "timestamp": pd.to_datetime(["2023-01-03 10:00"], utc=True),
                "side": ["buy"],
                "quantity": [5.0],
                "price": [100.0],
                "notional": [500.0],
                "fees": [0.5],
            }
        )
        regime = _make_regime_data(["AAPL"], ["2023-01-03 10:00"], ["bull"])

        result = svc.join_symbol_frame(frame=trade_logs, regime_data=regime)

        assert result["regime_trend"].isna().all()

    def test_empty_frame_returns_empty(self) -> None:
        svc = RegimeJoinService()
        regime = _make_regime_data(["AAPL"], ["2023-01-03 10:00"], ["bull"])

        result = svc.join_symbol_frame(frame=pd.DataFrame(), regime_data=regime)

        assert result.empty

    def test_no_lookahead_bias_exact_join_only(self) -> None:
        """Verify that only exact timestamp matches are joined — no forward-fill."""
        svc = RegimeJoinService()
        trade_logs = pd.DataFrame(
            {
                "symbol": ["AAPL"],
                "timestamp": pd.to_datetime(["2023-01-05 10:00"], utc=True),
                "side": ["buy"],
                "quantity": [10.0],
                "price": [150.0],
                "notional": [1500.0],
                "fees": [1.5],
            }
        )
        # Regime data only has an earlier timestamp
        regime = _make_regime_data(["AAPL"], ["2023-01-03 10:00"], ["bull"])

        result = svc.join_symbol_frame(frame=trade_logs, regime_data=regime)

        assert result["regime_trend"].isna().all()
