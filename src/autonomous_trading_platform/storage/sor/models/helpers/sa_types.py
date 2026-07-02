# src/autonomous_trading_platform/storage/sor/models/helpers/sa_types.py

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.types import DateTime, Numeric, Text, TypeDecorator

UUID_PK = PG_UUID(as_uuid=True)  # SQLAlchemy column type
uuid_pk_default = uuid4  # default generator


class UTCDateTimeType(TypeDecorator):
    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("Datetime must be timezone-aware")
        return value.astimezone(UTC)

    def process_result_value(self, value: datetime | None, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            # SQLite stores UTC datetimes as naive ISO strings.  Use replace() so
            # the naive value is tagged as UTC rather than treated as local time.
            return value.replace(tzinfo=UTC)
        # Postgres returns tz-aware; normalize to UTC.
        return value.astimezone(UTC)


class MoneyType(TypeDecorator):
    impl = Numeric(18, 6)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if isinstance(value, Decimal):
            return value
        # keep strict:
        raise TypeError("Money must be Decimal")


class QuantityType(TypeDecorator):
    impl = Numeric(18, 9)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if isinstance(value, Decimal):
            return value
        raise TypeError("Quantity must be Decimal")


class JSONStringListType(TypeDecorator):
    """Cross-DB list[str] column.

    Postgres: delegates to the native ARRAY(String) column (stored natively).
    SQLite / other dialects: serializes to a JSON text string on write,
    deserializes on read.

    Use this instead of ``ARRAY(String)`` for columns that need to work in
    both Postgres (production) and SQLite (tests).
    """

    impl = Text
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            from sqlalchemy import String
            from sqlalchemy.dialects.postgresql import ARRAY

            return dialect.type_descriptor(ARRAY(String))
        return dialect.type_descriptor(Text())

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if dialect.name == "postgresql":
            return value  # pass the list directly; ARRAY handles it
        return json.dumps(value)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        if isinstance(value, list):
            return value  # Postgres ARRAY already deserializes
        return json.loads(value)
