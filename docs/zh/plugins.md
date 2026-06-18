# AgentGuard插件

AgentGuard 同时支持部署在 client 和 server 两侧的 plugin。两侧使用同一套标准化运行时 schema，但可见信息范围不同，部署位置也不同。若需要查看实现级细节，可参考 `src/client/python/agentguard/plugins/README_CN.md` 和 `src/server/backend/runtime/plugins/`。

## Client 与 Server 插件的区别

- **Client plugin** 运行在智能体进程本地，只接收当前 `event: RuntimeEvent` 和 `context: RuntimeContext`，适合在 server 判定前做低延迟、轻量级过滤。
- **Server plugin** 运行在中控服务端，接收当前 `event`、当前 `context` 和 `trajectory_window: list[RuntimeEvent]`，适合跨步骤检测、集中策略评估和审计。
- Client plugin 文件需要放在 `src/client/python/agentguard/plugins/<phase>/`。
- Server plugin 文件需要放在 `src/server/backend/runtime/plugins/<phase>/`。

## 插件配置

加入 plugin 类之后，需要在 plugin 配置中用 plugin spec 对象引用它们。`name` 字段是注册名。client plugin spec 支持 `env`、`kwargs` 和顶层构造参数，这些参数会传入 plugin 实例；server plugin spec 当前主要按 `name` 或 `class` 解析，server runtime 不会把 `env` 或 `kwargs` 注入 server plugin 构造函数。

```json
{
  "phases": {
    "tool_before": {
      "client": [
        {
          "name": "my_client_plugin",
          "env": {}
        }
      ],
      "server": [
        {
          "name": "rule_based_plugin",
          "env": {}
        },
        {
          "name": "my_server_plugin",
          "env": {}
        }
      ]
    }
  }
}
```

- `client` 由 client 侧 plugin manager 加载。
- `server` 由 server 侧 plugin manager 加载。
- `client` plugin spec 可以使用 `name`、可选的 `env`，也可以通过 `kwargs` 或顶层字段传入构造参数。
- `server` plugin spec 当前主要使用 `name`（或 `class`）做解析；额外字段会保留在配置里，但不会被注入 server plugin 构造函数。
- 即使两个 plugin spec 出现在同一份配置文件里，对应实现文件仍然必须分别部署到正确的 client 或 server 目录下。

## 插件分类

Plugin 实现细节拆到了本章节下的独立页面：

- [内置插件](plugins/builtin_plugins.md)
- [自定义客户端插件](plugins/custom_client_plugin.md)
- [自定义服务端插件](plugins/custom_server_plugin.md)
