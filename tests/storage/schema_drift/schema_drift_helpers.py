from __future__ import annotations

from dataclasses import dataclass, fields, is_dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from types import NoneType, UnionType
from typing import Annotated, Any, Literal, Union, cast, get_args, get_origin, get_type_hints
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Float,
    Integer,
    Numeric,
    String,
    inspect,
)
from sqlalchemy import (
    Enum as SAEnum,
)
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.sql.schema import Column
from sqlalchemy.types import TypeDecorator


@dataclass(frozen=True)
class ContractOrmSchemaPair:
    contract: type[Any]
    orm_model: type[DeclarativeBase]
    expected_fields: tuple[str, ...]
    note: str = ""


def assert_contract_matches_orm(pair: ContractOrmSchemaPair) -> None:
    contract_columns = _contract_columns(pair.contract, pair.expected_fields)
    orm_columns = _orm_columns(pair.orm_model, pair.expected_fields)

    assert set(contract_columns) == set(pair.expected_fields)
    assert set(orm_columns) == set(pair.expected_fields)

    mismatches: list[str] = []
    for field_name in pair.expected_fields:
        contract_column = contract_columns[field_name]
        orm_column = orm_columns[field_name]
        if contract_column != orm_column:
            mismatches.append(f"{field_name}: contract={contract_column!r}, orm={orm_column!r}")

    assert not mismatches, "Contract/ORM schema drift:\n" + "\n".join(mismatches)


def assert_orm_matches_database(pair: ContractOrmSchemaPair, bind: Any) -> None:
    table_name = pair.orm_model.__table__.name
    inspected_columns = {column["name"]: column for column in inspect(bind).get_columns(table_name)}
    orm_field_names = tuple(column.name for column in pair.orm_model.__table__.columns)
    orm_columns = _orm_columns(pair.orm_model, orm_field_names)

    missing_columns = sorted(set(orm_field_names) - set(inspected_columns))
    assert not missing_columns, (
        f"{table_name} is missing expected migrated columns: {missing_columns}"
    )
    extra_columns = sorted(set(inspected_columns) - set(orm_field_names))
    assert not extra_columns, (
        f"{table_name} has migrated columns missing from ORM model: {extra_columns}"
    )

    mismatches: list[str] = []
    for column_name, orm_column in orm_columns.items():
        db_column = inspected_columns[column_name]
        db_column_shape = {
            "name": column_name,
            "nullable": bool(db_column["nullable"]),
        }
        expected_database_column = {
            "name": orm_column["name"],
            "nullable": orm_column["nullable"],
        }
        if expected_database_column != db_column_shape:
            mismatches.append(
                f"{column_name}: orm={expected_database_column!r}, database={db_column_shape!r}"
            )

    assert not mismatches, "ORM/database schema drift:\n" + "\n".join(mismatches)


def _contract_columns(
    contract: type[Any],
    expected_fields: tuple[str, ...],
) -> dict[str, dict[str, object]]:
    contract_fields = _contract_field_annotations(contract)
    actual_fields = set(contract_fields)
    expected_field_set = set(expected_fields)
    assert actual_fields == expected_field_set, (
        f"{contract.__name__} fields differ from expected persistence fields: "
        f"missing={sorted(expected_field_set - actual_fields)}, "
        f"extra={sorted(actual_fields - expected_field_set)}"
    )

    return {
        name: {
            "name": name,
            "nullable": _annotation_allows_none(annotation),
            "shape": _annotation_shape(annotation),
        }
        for name, annotation in contract_fields.items()
    }


def _contract_field_annotations(contract: type[Any]) -> dict[str, object]:
    if issubclass(contract, BaseModel):
        return {name: field.annotation for name, field in contract.model_fields.items()}
    if is_dataclass(contract):
        type_hints = get_type_hints(contract, include_extras=True)
        return {field.name: type_hints.get(field.name, field.type) for field in fields(contract)}
    raise TypeError(f"{contract.__name__} is not a supported runtime contract")


def _orm_columns(
    orm_model: type[DeclarativeBase],
    expected_fields: tuple[str, ...],
) -> dict[str, dict[str, object]]:
    table_columns = {column.name: column for column in orm_model.__table__.columns}
    actual_columns = set(table_columns)
    expected_column_set = set(expected_fields)
    assert actual_columns == expected_column_set, (
        f"{orm_model.__name__} columns differ from expected persistence fields: "
        f"missing={sorted(expected_column_set - actual_columns)}, "
        f"extra={sorted(actual_columns - expected_column_set)}"
    )

    return {
        name: {
            "name": name,
            "nullable": bool(table_columns[name].nullable),
            "shape": _column_type_shape(table_columns[name]),
        }
        for name in expected_fields
    }


def _annotation_allows_none(annotation: object) -> bool:
    unwrapped = _unwrap_annotated(annotation)
    origin = get_origin(unwrapped)
    if origin in (Union, UnionType):
        return NoneType in get_args(unwrapped)
    return unwrapped is NoneType


def _annotation_shape(annotation: object) -> str:
    unwrapped = _normalize_annotation(annotation)
    origin = get_origin(unwrapped)
    if unwrapped is UUID:
        return "uuid"
    if unwrapped is datetime:
        return "datetime"
    if unwrapped is date:
        return "date"
    if unwrapped is Decimal:
        return "numeric"
    if unwrapped is int:
        return "integer"
    if unwrapped is bool:
        return "boolean"
    if unwrapped is float:
        return "numeric"
    if unwrapped is str:
        return "string"
    if isinstance(unwrapped, type) and issubclass(unwrapped, Enum):
        return "string"
    if origin is Literal:
        args = get_args(unwrapped)
        if args and all(isinstance(arg, str) for arg in args):
            return "string"
    if origin is dict:
        return "json"
    if origin is list:
        return "json"
    return repr(unwrapped)


def _column_type_shape(column_or_type: Column[Any] | Any) -> str:
    column_type = getattr(column_or_type, "type", column_or_type)
    if isinstance(column_type, SAEnum):
        return "string"
    if _is_uuid_type(column_type):
        return "uuid"

    if isinstance(column_type, TypeDecorator):
        from autonomous_trading_platform.storage.sor.models.helpers.sa_types import (
            JSONStringListType,
        )

        if isinstance(column_type, JSONStringListType):
            return "json"
        column_type = column_type.impl

    if _is_array_type(column_type):
        return "json"
    if _is_json_type(column_type):
        return "json"
    if isinstance(column_type, Boolean):
        return "boolean"
    if isinstance(column_type, Integer):
        return "integer"
    if isinstance(column_type, Date):
        return "date"
    if isinstance(column_type, DateTime):
        return "datetime"
    if isinstance(column_type, Numeric):
        return "numeric"
    if isinstance(column_type, Float):
        return "float"
    if isinstance(column_type, String):
        return "string"
    return cast(str, column_type.__class__.__name__).lower()


def _unwrap_annotated(annotation: object) -> object:
    if get_origin(annotation) is Annotated:
        return get_args(annotation)[0]
    return annotation


def _unwrap_optional(annotation: object) -> object:
    origin = get_origin(annotation)
    if origin not in (Union, UnionType):
        return annotation
    args = tuple(arg for arg in get_args(annotation) if arg is not NoneType)
    if len(args) == 1:
        return args[0]
    return annotation


def _normalize_annotation(annotation: object) -> object:
    return _unwrap_annotated(_unwrap_optional(_unwrap_annotated(annotation)))


def _is_uuid_type(column_type: object) -> bool:
    type_name = column_type.__class__.__name__.lower()
    return type_name in {"uuid", "pguuid"} or "uuid" in type_name


def _is_json_type(column_type: object) -> bool:
    return "json" in column_type.__class__.__name__.lower()


def _is_array_type(column_type: object) -> bool:
    return "array" in column_type.__class__.__name__.lower()
