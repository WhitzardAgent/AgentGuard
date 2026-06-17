# 运行时会话链路与存储
本文档基于当前代码实现，梳理 Python client 与 server 之间的完整运行链路，以及 server 端实际存储的 session 结构。

## 完整链路

### 1. 初始化

当前 Python 实现中的初始化流程如下：

1. 调用方在构造 `AgentGuard` 时传入 `session_id`。
2. 如果没有显式传入 `session_key`，client 会自动生成一个。
3. client 会构造 `RuntimeContext`，其中包含 `session_id`、`agent_id`、`user_id`，以及这些 metadata：
   * `client_session_key`
   * `client_plugin_config`
   * `remote_plugin_config`
4. 如果启用了 remote 模式，client 会启动本地 config API，并把以下 URL 写入 `context.metadata`：
   * `client_config_url`
   * `client_plugin_list_url`
   * `client_health_url`
5. 随后 client 会向 server 注册该 session。
6. server 会在 session pool 中对该 session 做 upsert。

当前代码位置：

* `src/client/python/agentguard/guard.py:60`
* `src/client/python/agentguard/guard.py:61`
* `src/client/python/agentguard/guard.py:155`
* `src/server/backend/api/client_router.py:66`
* `src/server/backend/runtime/storage/__init__.py:113`

### 2. 运行时判定

当前判定链路如下：

1. client 先执行本地 plugin。
2. 如果本地 plugin 已经给出 final decision，则直接在本地生效，并写入 `ClientSyncBuffer`。
3. 如果本地 plugin 没有给出 final decision，则 client 调用 `/v1/server/guard/decide`。
4. server 会先刷新或 upsert 本次请求对应的 session 上下文。
5. server 会按组合身份 `session_id::agent_id::user_id` 查找 session，并读取该 session 上的 `remote_plugin_config`。
6. server plugin manager 会按 phase 解析 plugin config，但执行时只读取每个 phase 下的 `remote` plugin 列表。
7. server 返回 decision 给 client。

当前代码位置：

* `src/client/python/agentguard/u_guard/enforcer.py:68`
* `src/client/python/agentguard/u_guard/enforcer.py:75`
* `src/client/python/agentguard/u_guard/enforcer.py:96`
* `src/client/python/agentguard/u_guard/remote_client.py:102`
* `src/server/backend/runtime/manager.py:221`
* `src/server/backend/runtime/manager.py:256`
* `src/server/backend/plugins/manager.py:32`
* `src/server/backend/runtime/manager.py:267`

### 3. 本地结果同步

本地判定结果不会丢掉，client 会通过两条路径把它们同步回 server：

1. 一轮完整执行结束后，client 会异步上传 trace entries。
2. 如果下一次 remote decide 发生在异步上传完成之前，这些本地缓存会通过 `client_cached_entries` 顺带补传。
3. 如果 client 运行中出现异常，则会调用 `sync_local_cache_now(reason="client_error")` 尝试立即上传。

当前代码位置：

* `src/client/python/agentguard/harness/runtime.py:130`
* `src/client/python/agentguard/harness/runtime.py:133`
* `src/client/python/agentguard/harness/runtime.py:164`
* `src/client/python/agentguard/harness/runtime.py:183`
* `src/client/python/agentguard/u_guard/enforcer.py:133`
* `src/client/python/agentguard/u_guard/remote_client.py:110`
* `src/server/backend/runtime/manager.py:245`
* `src/server/backend/runtime/manager.py:338`

### 4. 健康检查

server 侧还有一个后台健康检查循环：

1. server 会周期性调用 client 的 `/v1/client/health`。
2. 如果 client 可达，server 会刷新 `last_seen`，并写入健康检查相关 metadata。
3. 如果 client 不可达，server 会把结果标记为 `unreachable`。
4. 当前代码不会因为 client dead 或 unreachable 而自动删除 session。

当前代码位置：

* `src/client/python/agentguard/config_api.py:108`
* `src/server/backend/runtime/manager.py:164`
* `src/server/backend/runtime/manager.py:192`
* `src/server/backend/runtime/manager.py:210`

## Plugin Config 的结构

session 上存放的 `remote_plugin_config` 不是扁平的 remote-only 结构，而是与 client 侧 plugin config 一致的 phase 结构。

典型结构如下：

```json
{
  "phases": {
    "tool_before": {
      "local": [],
      "remote": [
        {
          "name": "rule_based_check",
          "env": {}
        }
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

需要注意：

* 解析器要求存在 `phases` 对象。
* 每个被配置的 phase 都必须同时包含 `local` 和 `remote` 两个 key。
* server 执行时只读取 `remote` 列表。
* client 侧 plugin manager 读取的是同一套 phase 结构，但使用的是 `local` 侧配置。

代码位置：

* `src/client/python/agentguard/guard.py:68`
* `src/server/backend/plugins/manager.py:42`
* `src/server/backend/plugins/manager.py:48`
* `src/server/backend/plugins/manager.py:54`

## Server 默认判定

如果 server plugin 流程没有产出 final decision，server 会默认返回一个 `allow` decision。

这个默认行为来自 `_decision_from_plugin_result()`：

* 如果 `check.is_final` 且存在 `decision_candidate`，则直接返回该 final decision。
* 否则返回 `GuardDecision.allow("No server plugin returned a final decision; default allow.")`。

代码位置：

* `src/server/backend/runtime/manager.py:418`

## Server 端 Session 完整格式

server 会按组合身份存一条 session record：

* `session_key = session_id::agent_id::user_id`

这个 `session_key` 是 server 内部的存储 key，和 `client_key` 不是一回事。`client_key` 是 client 通过请求头传递的 session secret。

当前 session record 结构如下：

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

  "client_config_url": "http://127.0.0.1:38181/v1/client/plugins/config",
  "client_plugin_list_url": "http://127.0.0.1:38181/v1/client/plugins/list",
  "client_health_url": "http://127.0.0.1:38181/v1/client/health",

  "client_plugin_config": {
    "phases": {
      "tool_before": {
        "local": [
          {
            "name": "tool_invoke",
            "env": {}
          }
        ],
        "remote": []
      }
    }
  },
  "remote_plugin_config": {
    "phases": {
      "tool_before": {
        "local": [],
        "remote": [
          {
            "name": "rule_based_check",
            "env": {}
          }
        ]
      }
    }
  },

  "principal": {
    "agent_id": "agent-alpha",
    "user_id": "user-1"
  },

  "metadata": {
    "client_session_key": "sk_xxx",
    "client_config_url": "http://127.0.0.1:38181/v1/client/plugins/config",
    "client_plugin_list_url": "http://127.0.0.1:38181/v1/client/plugins/list",
    "client_health_url": "http://127.0.0.1:38181/v1/client/health",
    "client_plugin_config": {
      "phases": {
        "tool_before": {
          "local": [
            {
              "name": "tool_invoke",
              "env": {}
            }
          ],
          "remote": []
        }
      }
    },
    "remote_plugin_config": {
      "phases": {
        "tool_before": {
          "local": [],
          "remote": [
            {
              "name": "rule_based_check",
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

代码位置：

* `src/server/backend/runtime/storage/__init__.py:113`
* `src/server/backend/runtime/storage/__init__.py:149`
* `src/server/backend/runtime/manager.py:196`
* `src/server/backend/runtime/manager.py:339`
