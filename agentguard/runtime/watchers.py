"""Rule-file hot-reload watcher.

Two back-ends (in priority order):
1. ``watchdog`` (if installed) — inotify/kqueue, zero-overhead, ~1 ms latency.
2. Polling thread — pure stdlib, checks mtime every ``interval_s`` seconds.

Usage::

    watcher = RuleWatcher(
        guard=guard,
        paths=["rules/", "rules/prod.rules"],
        interval_s=5.0,
        on_reload=lambda n: print(f"Reloaded {n} rules"),
    )
    watcher.start()          # call once after the server has started
    ...
    watcher.stop()           # call during shutdown

The watcher is automatically integrated into ``AgentGuardServer`` when
``--watch`` is passed to ``python -m agentguard serve``.
"""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Callable, Iterable

log = logging.getLogger(__name__)


def _glob_rules(paths: list[str]) -> list[Path]:
    """Return all .rules files reachable from the given paths."""
    out: list[Path] = []
    for p in paths:
        pp = Path(p)
        if pp.is_dir():
            out.extend(sorted(pp.rglob("*.rules")))
        elif pp.is_file():
            out.append(pp)
    return out


def _snapshot(paths: list[str]) -> dict[str, float]:
    """Map each .rules file to its mtime."""
    return {str(f): f.stat().st_mtime for f in _glob_rules(paths) if f.exists()}


class RuleWatcher:
    """Background watcher that hot-reloads rules when source files change.

    Parameters
    ----------
    guard:
        The :class:`agentguard.sdk.guard.Guard` instance to reload.
    paths:
        List of file or directory paths to watch.  Directories are watched
        recursively for ``*.rules`` files.
    interval_s:
        Polling interval in seconds (used when *watchdog* is unavailable).
    on_reload:
        Optional callback invoked after a successful reload, receives the
        number of rules loaded as its sole argument.
    async_runtime:
        If provided, propagates the new rule list to the async actor runtime
        so PolicyActor and SessionActor are updated atomically.
    """

    def __init__(
        self,
        *,
        guard: "Guard",  # type: ignore[name-defined]  # noqa: F821
        paths: Iterable[str],
        interval_s: float = 5.0,
        on_reload: Callable[[int], None] | None = None,
        async_runtime: "AgentGuardRuntime | None" = None,  # type: ignore[name-defined]  # noqa: F821
    ) -> None:
        self._guard = guard
        self._paths = list(paths)
        self._interval_s = interval_s
        self._on_reload = on_reload
        self._async_runtime = async_runtime
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_snapshot: dict[str, float] = {}

    # ── public lifecycle ────────────────────────────────────────────────

    def start(self) -> None:
        """Start the background watcher thread (idempotent)."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._last_snapshot = _snapshot(self._paths)
        self._stop_event.clear()

        # Try watchdog first.
        if self._try_start_watchdog():
            return

        # Fall back to polling.
        self._thread = threading.Thread(
            target=self._poll_loop,
            name="agentguard-rule-watcher",
            daemon=True,
        )
        self._thread.start()
        log.info(
            "RuleWatcher started (polling, interval=%.1fs) watching: %s",
            self._interval_s, self._paths,
        )

    def stop(self) -> None:
        """Stop the watcher (blocks up to 2× interval_s)."""
        self._stop_event.set()
        if hasattr(self, "_wd_observer"):
            try:
                self._wd_observer.stop()
                self._wd_observer.join(timeout=2.0)
            except Exception:
                pass
        if self._thread is not None:
            self._thread.join(timeout=self._interval_s * 2)
        log.info("RuleWatcher stopped")

    @property
    def is_running(self) -> bool:
        return (
            (self._thread is not None and self._thread.is_alive())
            or getattr(self, "_wd_observer", None) is not None
        )

    # ── internal ────────────────────────────────────────────────────────

    def _reload(self) -> None:
        """Reload rules and propagate to async runtime if present."""
        try:
            n = self._guard.reload_rules()
            if self._async_runtime is not None and self._async_runtime.started:
                self._async_runtime.load_rules(self._guard.active_rules())
            log.info(
                "RuleWatcher: reloaded %d rules from %s",
                n, self._paths,
            )
            if self._on_reload is not None:
                try:
                    self._on_reload(n)
                except Exception:
                    pass
        except Exception as exc:
            log.error("RuleWatcher: reload failed: %s", exc)

    def _check_and_reload(self) -> bool:
        """Return True if a reload was triggered."""
        new_snap = _snapshot(self._paths)
        if new_snap != self._last_snapshot:
            changed = {
                k for k in new_snap
                if self._last_snapshot.get(k) != new_snap[k]
            }
            added = set(new_snap) - set(self._last_snapshot)
            removed = set(self._last_snapshot) - set(new_snap)
            self._last_snapshot = new_snap
            desc = []
            if changed - added:
                desc.append(f"modified: {sorted(changed - added)}")
            if added:
                desc.append(f"added: {sorted(added)}")
            if removed:
                desc.append(f"removed: {sorted(removed)}")
            log.info("RuleWatcher: file change detected (%s)", "; ".join(desc))
            self._reload()
            return True
        return False

    def _poll_loop(self) -> None:
        while not self._stop_event.is_set():
            self._stop_event.wait(timeout=self._interval_s)
            if self._stop_event.is_set():
                break
            self._check_and_reload()

    def _try_start_watchdog(self) -> bool:
        """Try to use the *watchdog* package for event-driven watching.

        Returns True on success; caller falls back to polling otherwise.
        """
        try:
            from watchdog.observers import Observer
            from watchdog.events import FileSystemEventHandler, FileSystemEvent
        except ImportError:
            return False

        watcher = self

        class _Handler(FileSystemEventHandler):
            def on_any_event(self, event: "FileSystemEvent") -> None:
                if event.is_directory:
                    return
                src = getattr(event, "src_path", "")
                if src.endswith(".rules"):
                    watcher._check_and_reload()

        observer = Observer()
        for p in self._paths:
            pp = Path(p)
            watch_dir = str(pp if pp.is_dir() else pp.parent)
            recursive = pp.is_dir()
            observer.schedule(_Handler(), watch_dir, recursive=recursive)

        observer.start()
        self._wd_observer = observer
        log.info(
            "RuleWatcher started (watchdog/inotify) watching: %s",
            self._paths,
        )
        return True
