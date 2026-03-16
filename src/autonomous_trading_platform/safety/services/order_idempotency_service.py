from __future__ import annotations

import hashlib
from datetime import datetime, timedelta

from autonomous_trading_platform.config.settings import Settings
from autonomous_trading_platform.contracts.trading.order_intent import OrderIntent
from autonomous_trading_platform.safety.errors import DuplicateIdempotencyKeyError


class OrderIdempotencyService:
    def __init__(self, settings: Settings, order_activity_reader) -> None:
        self.settings = settings
        self.order_activity_reader = order_activity_reader

    def build_idempotency_key(self, order_intent: OrderIntent) -> str:
        """
        Build a deterministic idempotency key from the logical identity of an
        order intent.

        The key is based on:
        - run_id
        - strategy_id
        - bar_timestamp
        - symbol
        - side
        - qty

        Returns:
            Stable SHA-256 hex digest string.
        """
        if order_intent.qty is None:
            raise ValueError("Order intent qty must be set for idempotency key generation.")

        raw_key = "|".join(
            [
                str(order_intent.run_id),
                order_intent.strategy_id,
                order_intent.bar_timestamp.isoformat(),
                order_intent.symbol.upper(),
                order_intent.side.value
                if hasattr(order_intent.side, "value")
                else str(order_intent.side),
                str(order_intent.qty),
            ]
        )

        return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()

    def assert_not_duplicate_within_window(
        self,
        order_intent: OrderIntent,
        now: datetime,
    ) -> str:
        """
        Build the deterministic idempotency key and raise if an order with the
        same key already exists within the configured dedupe window.

        Returns:
            The generated idempotency key if no duplicate is found.
        """
        idempotency_key = self.build_idempotency_key(order_intent)

        window_minutes = self.settings.idempotency_deduplication_window_minutes
        window_start = now - timedelta(minutes=window_minutes)

        duplicate_exists = self.order_activity_reader.idempotency_key_exists_between(
            idempotency_key=idempotency_key,
            start_time=window_start,
            end_time=now,
        )

        if duplicate_exists:
            raise DuplicateIdempotencyKeyError(
                f"Duplicate order intent detected within deduplication window: {idempotency_key}"
            )

        return idempotency_key
