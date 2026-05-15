from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import asdict, dataclass
from typing import cast


@dataclass(frozen=True)
class RuntimeContext:
    correlation_id: str | None = None
    run_id: str | None = None
    strategy_id: str | None = None
    dataset_version: str | None = None
    universe_version: str | None = None
    job_name: str | None = None
    job_run_id: str | None = None
    environment: str | None = None


_ctx: ContextVar[RuntimeContext | None] = ContextVar("ratp_runtime_ctx", default=None)


def get_runtime_context() -> RuntimeContext | None:
    return _ctx.get()


def _bind(token_holder: list[Token], **kwargs: object) -> None:
    current = _ctx.get()
    if current is None:
        new = RuntimeContext(**_context_kwargs(kwargs))
    else:
        fields = asdict(current)
        fields.update(_context_kwargs(kwargs, include_none=False))
        new = RuntimeContext(**cast(dict[str, str | None], fields))
    token_holder.append(_ctx.set(new))


@contextmanager
def runtime_context(**kwargs: object) -> Iterator[RuntimeContext]:
    token_holder: list[Token] = []
    _bind(token_holder, **kwargs)
    try:
        ctx = _ctx.get()
        assert ctx is not None
        yield ctx
    finally:
        _ctx.reset(token_holder[0])


def _context_kwargs(
    kwargs: dict[str, object],
    *,
    include_none: bool = True,
) -> dict[str, str | None]:
    allowed = RuntimeContext.__dataclass_fields__
    values: dict[str, str | None] = {}
    for key, value in kwargs.items():
        if key not in allowed:
            continue
        if value is None:
            if include_none:
                values[key] = None
            continue
        values[key] = str(value)
    return values
