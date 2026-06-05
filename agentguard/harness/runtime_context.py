"""Ambient :class:`RuntimeContext` propagation via ``contextvars``."""

from __future__ import annotations

import contextlib
import contextvars
from typing import Iterator

from agentguard.schemas.context import RuntimeContext

_current: contextvars.ContextVar[RuntimeContext | None] = contextvars.ContextVar(
    "agentguard_harness_context", default=None
)


def current_context() -> RuntimeContext | None:
    return _current.get()


def push_context(context: RuntimeContext) -> contextvars.Token[RuntimeContext | None]:
    return _current.set(context)


def pop_context(token: contextvars.Token[RuntimeContext | None]) -> None:
    _current.reset(token)


@contextlib.contextmanager
def use_context(context: RuntimeContext) -> Iterator[RuntimeContext]:
    token = push_context(context)
    try:
        yield context
    finally:
        pop_context(token)
