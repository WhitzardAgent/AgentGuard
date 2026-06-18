# Runtime Session Lifecycle
This page documents the current end-to-end runtime path between the Python client and the server, and the exact shape of the session record stored on the server.

## Complete Flow

### 1. Initialization

At initialization time, the current Python implementation behaves as follows:

1. The caller provides `session_id` when constructing `AgentGuard`.
2. The client generates `session_key` automatically if the caller does not provide one.
3. The client builds `RuntimeContext` with `session_id`, `agent_id`, `user_id`, and metadata such as:
   * `client_session_key`
   * `client_plugin_config`
   * `remote_plugin_config`
4. If remote mode is enabled (`server_url` configured), the client constructor attempts to start a local config API immediately and writes these URLs into `context.metadata`:
   * `client_config_url`
   * `client_plugin_list_url`
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

1. The client runs client-side plugins first.
2. If the client-side result is final, the client applies it locally and stores the decision in `ClientSyncBuffer`.
3. If the client-side result is not final, the client calls `/v1/server/guard/decide`.
4. The server refreshes or upserts the session context for this request.
5. The server looks up the session by the composite identity `session_id::agent_id::user_id`, then applies any agent-scoped plugin override on top of the stored session config.
6. The server plugin manager parses the effective plugin config by phase and only executes the `server` plugin list for each phase.
7. The server returns the decision to the client.

Current code references:

* `src/client/python/agentguard/u_guard/enforcer.py:68`
* `src/client/python/agentguard/u_guard/enforcer.py:75`
* `src/client/python/agentguard/u_guard/enforcer.py:96`
* `src/client/python/agentguard/u_guard/remote_client.py:102`
* `src/server/backend/runtime/manager.py:221`
* `src/server/backend/runtime/manager.py:256`
* `src/server/backend/runtime/plugins/manager.py:32`
* `src/server/backend/runtime/manager.py:267`

### 3. Client-Side Result Sync

Client-side-only decisions are not discarded. The client syncs them back to the server through two paths:

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
2. If the client is reachable, the server refreshes `last_seen` and stores health metadata on the session.
3. If the client is unreachable, the returned health-check result is marked as `unreachable`, but the session record itself is left unchanged.
4. The current code does not automatically delete the session when the client is dead or unreachable.

Current code references:

* `src/client/python/agentguard/config_api.py:108`
* `src/server/backend/runtime/manager.py:164`
* `src/server/backend/runtime/manager.py:192`
* `src/server/backend/runtime/manager.py:210`

## Plugin Config Shape

The session-scoped `remote_plugin_config` is not stored as a flattened server-only structure. It keeps the same phased shape as the client-side plugin config. During initial registration, clients populate it with the same payload as `client_plugin_config`; later client-side `update_plugin_config()` calls only update `client_plugin_config`, so the stored `remote_plugin_config` reflects the last server-synchronized server-side view unless the client re-registers or the server applies overrides.

A typical shape is:

```json
{
  "phases": {
    "tool_before": {
      "client": [],
      "server": [
        {
          "name": "rule_based_plugin",
          "env": {}
        }
      ]
    },
    "llm_before": {
      "client": [],
      "server": []
    },
    "llm_after": {
      "client": [],
      "server": []
    },
    "tool_after": {
      "client": [],
      "server": []
    },
    "global": {
      "client": [],
      "server": []
    }
  }
}
```

Important behavior:

* When a plugin manager loads config for execution, the parser requires a `phases` object.
* When a phase is present, the execution parser expects both `client` and `server` keys.
* The server only reads the `server` list for execution.
* The client-side plugin manager reads the same phased structure, but uses the `client` side.
* If the server already has a default `plugin_config` and the client mirrors that same structure into `remote_plugin_config`, the server clears the mirrored session-scoped server override so the server default remains authoritative. Explicit session-scoped server overrides are still preserved.

Code references:

* `src/client/python/agentguard/guard.py:68`
* `src/server/backend/runtime/plugins/manager.py:42`
* `src/server/backend/runtime/plugins/manager.py:48`
* `src/server/backend/runtime/plugins/manager.py:54`

## Default Server Decision

If the server plugin pipeline does not produce a final decision, the server returns a default `allow` decision.

That default comes from `_decision_from_plugin_result()`:

* If `check.is_final` and `decision_candidate` exist, return that final plugin decision.
* Otherwise return `GuardDecision.allow("No server plugin returned a final decision; default allow.")`.

Code reference:

* `src/server/backend/runtime/manager.py:418`

## Server Session Record Format

The server stores one session record per composite identity:

* `session_key = session_id::agent_id::user_id`

This `session_key` is an internal storage key. It is different from `client_key`, which is the client session secret used in headers.

A typical healthy session record may look like this:

```json
{
  "session_key": "session_id::agent_id::user_id",
  "session_id": "sess_123",
  "agent_id": "agent-alpha",
  "user_id": "user-1",

  "client_ip": "127.0.0.1",
  "client_key": "sk_xxx",

  "client_config_url": "http://127.0.0.1:38181/v1/client/plugins/config",
  "client_plugin_list_url": "http://127.0.0.1:38181/v1/client/plugins/list",
  "client_health_url": "http://127.0.0.1:38181/v1/client/health",

  "client_plugin_config": {
    "phases": {
      "tool_before": {
        "client": [
          {
            "name": "tool_invoke",
            "env": {}
          }
        ],
        "server": []
      }
    }
  },
  "remote_plugin_config": {
    "phases": {
      "tool_before": {
        "client": [],
        "server": [
          {
            "name": "rule_based_plugin",
            "env": {}
          }
        ]
      }
    }
  },

  "principal": null,

  "metadata": {
    "client_session_key": "sk_xxx",
    "client_config_url": "http://127.0.0.1:38181/v1/client/plugins/config",
    "client_plugin_list_url": "http://127.0.0.1:38181/v1/client/plugins/list",
    "client_health_url": "http://127.0.0.1:38181/v1/client/health",
    "client_plugin_config": {
      "phases": {
        "tool_before": {
          "client": [
            {
              "name": "tool_invoke",
              "env": {}
            }
          ],
          "server": []
        }
      }
    },
    "remote_plugin_config": {
      "phases": {
        "tool_before": {
          "client": [],
          "server": [
            {
              "name": "rule_based_plugin",
              "env": {}
            }
          ]
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

Notes:

* `principal` is optional and only appears when incoming event metadata provides it.
* `metadata.last_health_check_*` fields appear only after a successful health check.
* The effective server-side execution config can still be replaced by agent-scoped overrides at decision time.
