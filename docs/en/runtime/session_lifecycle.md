# Runtime Session Lifecycle

This page documents the current end-to-end runtime path between the Python client and the server, and the exact shape of the session record stored on the server.

## Complete Flow

### 1. Initialization

At initialization time, the current Python implementation behaves as follows:

1. The caller provides `session_id` when constructing `AgentGuard`.
2. The client generates `session_key` automatically if the caller does not provide one.
3. The client builds `RuntimeContext` with `session_id`, `agent_id`, `user_id`, and metadata such as:
   * `client_session_key`
   * `client_checker_config`
   * `remote_checker_config`
4. If remote mode is enabled, the client starts a local config API and writes these URLs into `context.metadata`:
   * `client_config_url`
   * `client_checker_list_url`
   * `client_health_url`
5. The client then registers the session to the server.
6. The server upserts a session record into the session pool.

Current code references:

* `src/client/python/agentguard/guard.py:60`
* `src/client/python/agentguard/guard.py:61`
* `src/client/python/agentguard/guard.py:155`
* `src/server/backend/api/client_router.py:66`
* `src/server/backend/runtime/storage/__init__.py:113`

### 2. Runtime Decision

At decision time, the current path is:

1. The client runs local checkers first.
2. If the local result is final, the client applies it locally and stores the decision in `ClientSyncBuffer`.
3. If the local result is not final, the client calls `/v1/server/guard/decide`.
4. The server refreshes or upserts the session context for this request.
5. The server looks up the session by the composite identity `session_id::agent_id::user_id` and reads the session's `remote_checker_config`.
6. The server checker manager parses the checker config by phase and only executes the `remote` checker list for each phase.
7. The server returns the decision to the client.

Current code references:

* `src/client/python/agentguard/u_guard/enforcer.py:68`
* `src/client/python/agentguard/u_guard/enforcer.py:75`
* `src/client/python/agentguard/u_guard/enforcer.py:96`
* `src/client/python/agentguard/u_guard/remote_client.py:102`
* `src/server/backend/runtime/manager.py:221`
* `src/server/backend/runtime/manager.py:256`
* `src/server/backend/runtime/checkers/manager.py:32`
* `src/server/backend/runtime/manager.py:267`

### 3. Local Result Sync

Local-only decisions are not discarded. The client syncs them back to the server through two paths:

1. At the end of a full round, the client asynchronously uploads trace entries.
2. If another remote decision happens before the async upload completes, the buffered local entries are piggybacked in `client_cached_entries`.
3. If the client hits an exception, it calls `sync_local_cache_now(reason="client_error")` to try an immediate upload.

Current code references:

* `src/client/python/agentguard/harness/runtime.py:130`
* `src/client/python/agentguard/harness/runtime.py:133`
* `src/client/python/agentguard/harness/runtime.py:164`
* `src/client/python/agentguard/harness/runtime.py:183`
* `src/client/python/agentguard/u_guard/enforcer.py:133`
* `src/client/python/agentguard/u_guard/remote_client.py:110`
* `src/server/backend/runtime/manager.py:245`
* `src/server/backend/runtime/manager.py:338`

### 4. Health Check

The server also maintains a background health check loop:

1. The server periodically calls the client's `/v1/client/health` endpoint.
2. If the client is reachable, the server refreshes `last_seen` and stores health metadata.
3. If the client is unreachable, the server marks the health check result as `unreachable`.
4. The current code does not automatically delete the session when the client is dead or unreachable.

Current code references:

* `src/client/python/agentguard/config_api.py:108`
* `src/server/backend/runtime/manager.py:164`
* `src/server/backend/runtime/manager.py:192`
* `src/server/backend/runtime/manager.py:210`

## Current HTTP Interfaces

### Client-local API

These endpoints are exposed by the client's local config API:

* `/v1/client/checkers/config`
* `/v1/client/checkers/list`
* `/v1/client/health`

Code references:

* `src/client/python/agentguard/config_api.py:16`
* `src/client/python/agentguard/config_api.py:17`
* `src/client/python/agentguard/config_api.py:19`

### Client-to-server API

These endpoints are used directly by the client runtime:

* `/v1/server/guard/decide`
* `/v1/server/policy/snapshot`
* `/v1/server/trace/upload`
* `/v1/server/tools/report`
* `/v1/server/session/register`
* `/v1/server/session/unregister`
* `/v1/server/skills/run`

Code reference:

* `src/server/backend/api/client_router.py:27`

### Backend / Frontend-to-server API

These endpoints are intended for backend or admin/frontend coordination instead of the runtime client path:

* `/v1/backend/checkers/config`

This API updates the server-side checker configuration and can also push checker configuration to registered clients.

Code reference:

* `src/server/backend/api/frontend_router.py:43`

## Checker Config Shape

The session-scoped `remote_checker_config` is not stored as a flattened remote-only structure. It keeps the same phased shape as the client-side checker config.

A typical shape is:

```json
{
  "phases": {
    "tool_before": {
      "local": [],
      "remote": [
        "rule_based_check"
      ]
    },
    "llm_before": {
      "local": [],
      "remote": []
    },
    "llm_after": {
      "local": [],
      "remote": []
    },
    "tool_after": {
      "local": [],
      "remote": []
    },
    "global": {
      "local": [],
      "remote": []
    }
  }
}
```

Important behavior:

* The parser requires a `phases` object.
* Each configured phase must include both `local` and `remote` keys.
* The server only reads the `remote` list for execution.
* The client-side checker manager reads the same phased structure, but uses the `local` side.

Code references:

* `src/client/python/agentguard/guard.py:68`
* `src/server/backend/runtime/checkers/manager.py:42`
* `src/server/backend/runtime/checkers/manager.py:48`
* `src/server/backend/runtime/checkers/manager.py:54`

## Default Server Decision

If the server checker pipeline does not produce a final decision, the server returns a default `allow` decision.

That default comes from `_decision_from_checker_result()`:

* If `check.is_final` and `decision_candidate` exist, return that final checker decision.
* Otherwise return `GuardDecision.allow("No server checker returned a final decision; default allow.")`.

Code reference:

* `src/server/backend/runtime/manager.py:418`

## Server Session Record Format

The server stores one session record per composite identity:

* `session_key = session_id::agent_id::user_id`

This `session_key` is an internal storage key. It is different from `client_key`, which is the client session secret used in headers.

The current session record shape is:

```json
{
  "session_key": "session_id::agent_id::user_id",
  "session_id": "sess_123",
  "agent_id": "agent-alpha",
  "user_id": "user-1",
  "task_id": null,
  "policy": "builtin",
  "policy_version": "builtin",
  "environment": "prod",

  "client_ip": "127.0.0.1",
  "client_key": "sk_xxx",

  "client_config_url": "http://127.0.0.1:38181/v1/client/checkers/config",
  "client_checker_list_url": "http://127.0.0.1:38181/v1/client/checkers/list",
  "client_health_url": "http://127.0.0.1:38181/v1/client/health",

  "client_checker_config": {
    "phases": {
      "tool_before": {
        "local": ["tool_invoke"],
        "remote": []
      }
    }
  },
  "remote_checker_config": {
    "phases": {
      "tool_before": {
        "local": [],
        "remote": ["rule_based_check"]
      }
    }
  },

  "principal": {
    "agent_id": "agent-alpha",
    "user_id": "user-1"
  },

  "metadata": {
    "client_session_key": "sk_xxx",
    "client_config_url": "http://127.0.0.1:38181/v1/client/checkers/config",
    "client_checker_list_url": "http://127.0.0.1:38181/v1/client/checkers/list",
    "client_health_url": "http://127.0.0.1:38181/v1/client/health",
    "client_checker_config": {
      "phases": {
        "tool_before": {
          "local": ["tool_invoke"],
          "remote": []
        }
      }
    },
    "remote_checker_config": {
      "phases": {
        "tool_before": {
          "local": [],
          "remote": ["rule_based_check"]
        }
      }
    },
    "event_metadata": {
      "example": true
    },
    "last_health_check_status": "ok",
    "last_health_check_url": "http://127.0.0.1:38181/v1/client/health",
    "last_health_check_response": {
      "status": "ok",
      "service": "agentguard-client-config",
      "session_id": "sess_123",
      "agent_id": "agent-alpha",
      "user_id": "user-1"
    },
    "last_trace_upload_reason": "round_complete"
  },

  "last_seen": 1781423456.123
}
```

Code references:

* `src/server/backend/runtime/storage/__init__.py:113`
* `src/server/backend/runtime/storage/__init__.py:149`
* `src/server/backend/runtime/manager.py:196`
* `src/server/backend/runtime/manager.py:339`

## Notes and Common Misunderstandings

### `session_id` vs `session_key`

The current Python client does not auto-generate `session_id`. The caller passes `session_id` into `AgentGuard`, while `session_key` is auto-generated if omitted.

### Registration happens once during init

When remote mode is enabled, the Python client now starts the local config API first and then performs a single `register_session`, so the server receives the local client URLs in that one registration payload.

If `start_config_api()` is called later and the published local URLs change, the client may upsert the same session again to refresh those URLs on the server.

### Unreachable clients are not auto-removed

The health monitor reports `unreachable`, but the current code does not delete the session from the pool automatically.
