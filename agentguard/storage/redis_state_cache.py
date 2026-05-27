"""Redis-backed implementation of :class:`StateCache`.

Activated via ``--state-cache redis://host:port/db`` on the runtime CLI or
``Guard(state_cache=RedisStateCache.from_url(...))`` from Python.

The Redis backend is fully optional; install with ``pip install agentguard[redis]``.
Trace mutations that need read-modify-write semantics use a tiny Lua script
to stay atomic without holding a client-side lock.
"""

from __future__ import annotations

import json
from typing import Any

from agentguard.storage.session_store import (
    RECENT_TOOLS_CAP,
    TRACE_LOG_CAP,
    TRACE_RICH_CAP,
    StateCache,
)


# Lua: scan a list of JSON-encoded entries from the tail forward and update
# the most recent entry whose ``tool`` matches ARGV[1]. Result payload (ARGV[2])
# is JSON-encoded by the caller.
_LUA_UPDATE_LAST_RESULT = """
local key = KEYS[1]
local tool = ARGV[1]
local result_json = ARGV[2]
local len = redis.call('LLEN', key)
for i = len - 1, 0, -1 do
  local raw = redis.call('LINDEX', key, i)
  if raw then
    local entry = cjson.decode(raw)
    if entry.tool == tool then
      entry.result = cjson.decode(result_json)
      redis.call('LSET', key, i, cjson.encode(entry))
      return 1
    end
  end
end
return 0
"""


class RedisStateCache(StateCache):
    """``StateCache`` backed by a single Redis logical database."""

    def __init__(self, client: Any) -> None:
        self._client = client
        self._update_last_result = client.register_script(_LUA_UPDATE_LAST_RESULT)

    @classmethod
    def from_url(cls, url: str, **kwargs: Any) -> "RedisStateCache":
        try:
            import redis  # type: ignore[import-untyped]
        except ImportError as exc:
            raise RuntimeError(
                "RedisStateCache requires `pip install agentguard[redis]`"
            ) from exc
        kwargs.setdefault("decode_responses", True)
        client = redis.from_url(url, **kwargs)
        return cls(client)

    @staticmethod
    def _decode(value: Any) -> str | None:
        if value is None:
            return None
        if isinstance(value, bytes):
            return value.decode("utf-8")
        return str(value)

    # ── kv ────────────────────────────────────────────────────────────

    def get(self, key: str) -> str | None:
        return self._decode(self._client.get(key))

    def set(self, key: str, value: str, ttl_ms: int | None = None) -> None:
        if ttl_ms:
            self._client.set(key, value, px=ttl_ms)
        else:
            self._client.set(key, value)

    # ── set ───────────────────────────────────────────────────────────

    def sadd(self, key: str, *members: str) -> None:
        if members:
            self._client.sadd(key, *members)

    def smembers(self, key: str) -> set[str]:
        raw = self._client.smembers(key) or set()
        return {self._decode(m) or "" for m in raw}

    # ── capped list (LIFO, used by recent_tools) ─────────────────────

    def lpush_capped(self, key: str, value: str, cap: int = RECENT_TOOLS_CAP) -> None:
        pipe = self._client.pipeline()
        pipe.lpush(key, value)
        pipe.ltrim(key, 0, max(cap - 1, 0))
        pipe.execute()

    def lrange(self, key: str, start: int, end: int) -> list[str]:
        raw = self._client.lrange(key, start, end)
        return [self._decode(v) or "" for v in raw]

    # ── chronological trace log ──────────────────────────────────────

    def append_trace(
        self,
        key: str,
        tool_name: str,
        ts_ms: int,
        cap: int = TRACE_LOG_CAP,
    ) -> None:
        encoded = json.dumps([tool_name, ts_ms])
        pipe = self._client.pipeline()
        pipe.rpush(key, encoded)
        pipe.ltrim(key, -cap, -1)
        pipe.execute()

    def read_trace(self, key: str) -> list[tuple[str, int]]:
        out: list[tuple[str, int]] = []
        for raw in self._client.lrange(key, 0, -1):
            try:
                tool, ts = json.loads(raw if isinstance(raw, str) else raw.decode("utf-8"))
                out.append((tool, int(ts)))
            except Exception:
                continue
        return out

    # ── rich trace log ───────────────────────────────────────────────

    def append_trace_rich(
        self,
        key: str,
        entry: dict[str, Any],
        cap: int = TRACE_RICH_CAP,
    ) -> None:
        try:
            payload = json.dumps(entry, default=str)
        except (TypeError, ValueError):
            payload = json.dumps({"tool": entry.get("tool"), "args": {}, "result": None})
        pipe = self._client.pipeline()
        pipe.rpush(key, payload)
        pipe.ltrim(key, -cap, -1)
        pipe.execute()

    def update_trace_result_last(self, key: str, tool_name: str, result: Any) -> None:
        try:
            result_json = json.dumps(result)
        except (TypeError, ValueError):
            result_json = json.dumps(str(result))
        try:
            self._update_last_result(keys=[key], args=[tool_name, result_json])
        except Exception:
            return

    def read_trace_rich(self, key: str) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for raw in self._client.lrange(key, 0, -1):
            try:
                out.append(
                    json.loads(raw if isinstance(raw, str) else raw.decode("utf-8"))
                )
            except Exception:
                continue
        return out

    # ── housekeeping ─────────────────────────────────────────────────

    def clear(self) -> None:
        # Wipe only AgentGuard-owned keys; leave the rest of the Redis DB alone
        # so callers can safely share infrastructure.
        for prefix in ("ag:sess:*", "ag:feat:*", "ag:prov:*"):
            cursor = 0
            while True:
                cursor, keys = self._client.scan(cursor=cursor, match=prefix, count=500)
                if keys:
                    self._client.delete(*keys)
                if cursor == 0:
                    break
