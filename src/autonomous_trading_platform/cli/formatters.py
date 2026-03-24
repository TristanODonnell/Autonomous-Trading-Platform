from __future__ import annotations

import json
import traceback
from collections.abc import Mapping


def print_header(title: str) -> None:
    print(f"\n=== {title} ===")


def print_kv_rows(data: Mapping[str, object]) -> None:
    for key, value in data.items():
        print(f"{key}: {value}")


def print_success(message: str) -> None:
    print(f"[OK] {message}")


def print_error(message: str) -> None:
    print(f"[ERROR] {message}")


def print_json(data: object) -> None:
    print(json.dumps(data, indent=2, default=str))


def format_exception(exc: Exception) -> str:
    return "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
