"""Lifecycle hook registry for the Harness.

Plugins and user code can register callbacks fired at well-defined stages
(session start/end, before/after each event, on every decision). Useful for
metrics, plugins, and custom enforcement side-effects.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from enum import Enum
from typing import Any, Callable

log = logging.getLogger("agentguard.harness")


class LifecycleStage(str, Enum):
    SESSION_START = "session_start"
    SESSION_END = "session_end"
    BEFORE_EVENT = "before_event"
    AFTER_EVENT = "after_event"
    ON_DECISION = "on_decision"


Hook = Callable[..., None]


class Lifecycle:
    def __init__(self) -> None:
        self._hooks: dict[LifecycleStage, list[Hook]] = defaultdict(list)

    def on(self, stage: LifecycleStage, hook: Hook) -> Callable[[], None]:
        self._hooks[stage].append(hook)

        def remove() -> None:
            try:
                self._hooks[stage].remove(hook)
            except ValueError:
                pass

        return remove

    def fire(self, stage: LifecycleStage, *args: Any, **kwargs: Any) -> None:
        for hook in list(self._hooks.get(stage, [])):
            try:
                hook(*args, **kwargs)
            except Exception as exc:  # noqa: BLE001
                log.warning("lifecycle hook %s failed: %s", stage.value, exc)
