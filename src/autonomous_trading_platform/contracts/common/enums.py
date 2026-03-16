import enum


class Side(enum.StrEnum):
    BUY = "buy"
    SELL = "sell"


class OrderType(enum.StrEnum):
    MARKET = "market"
    LIMIT = "limit"
    STOP = "stop"
    STOP_LIMIT = "stop_limit"


class TimeInForce(enum.StrEnum):
    DAY = "day"
    GTC = "gtc"
    IOC = "ioc"
    FOK = "fok"


class OrderSource(enum.StrEnum):
    LEDGER = "ledger"
    BROKER_RECONCILED = "broker_reconciled"


class OrderStatus(enum.StrEnum):
    NEW = "new"
    SUBMITTED = "submitted"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    CANCELED = "canceled"
    REJECTED = "rejected"


class CorporateActionType(enum.StrEnum):
    CASH_DIVIDEND = "cash_dividend"
    STOCK_DIVIDEND = "stock_dividend"
    SPLIT_FORWARD = "split_forward"
    SPLIT_REVERSE = "split_reverse"
    SPINOFF = "spinoff"
    MERGER_CASH = "merger_cash"
    MERGER_STOCK = "merger_stock"
    NAME_CHANGE = "name_change"


class LiquiditySide(enum.StrEnum):
    MAKER = "maker"
    TAKER = "taker"


class BarInterval(enum.StrEnum):
    ONE_MIN = "1m"
    FIVE_MIN = "5m"
    FIFTEEN_MIN = "15m"
    ONE_HOUR = "1h"
    ONE_DAY = "1d"


class PriceBasis(enum.StrEnum):
    RAW = "raw"
    ADJUSTED = "adjusted"


class RunType(enum.StrEnum):
    BACKTEST = "backtest"
    PAPER = "paper"
    LIVE = "live"
    SHADOW = "shadow"
    INGESTION = "ingestion"


class SignalDirection(enum.StrEnum):
    BUY = "buy"
    SELL = "sell"
    FLAT = "flat"


class MarketSession(enum.StrEnum):
    PREMARKET = "premarket"
    REGULAR = "regular"
    POSTMARKET = "postmarket"
    OVERNIGHT = "overnight"


class BarQualityFlag(enum.StrEnum):
    LATE = "late"
    SUSPECTED_OUTLIER = "suspected_outlier"
    MISSING_CYCLE_PEER = "missing_cycle_peer"


class IntentExecutionStatus(enum.StrEnum):
    SUBMITTED = "submitted"
    SHADOW_SUPPRESSED = "shadow_suppressed"
