# AgentGuard JS Client

`src/client/js/agentguard` 是 AgentGuard 的 JavaScript 客户端骨架版本，当前已经具备这些核心能力：

- 创建 `AgentGuard` 实例
- 包装普通工具函数
- 包装 LLM 调用
- 通过 `attach_langchain()` 给 LangChain/LangGraph agent 打补丁
- 对工具调用做本地规则检查、审计记录和基础 sandbox 控制

## 1. 基本导入

```js
const { AgentGuard } = require("./index");
```

如果你是在仓库根目录外部使用，通常会写成：

```js
const { AgentGuard } = require("agentguard");
```

当前仓库里更适合直接按相对路径引入。

## 2. 创建 Guard

```js
const guard = new AgentGuard("demo-session", {
  user_id: "alice",
  agent_id: "langchain-demo",
  policy: "builtin",
  sandbox: "local",
  max_tool_calls: 24,
  max_steps: 12,
});
```

常见参数：

- `session_id`: 当前会话 ID，必填
- `user_id`: 调用者 ID
- `agent_id`: agent 标识
- `policy`: 策略名或策略文件路径
- `server_url`: 远端控制面地址；不填时走本地模式
- `api_key`: 远端服务鉴权
- `sandbox`: `local` / `noop` / `subprocess`
- `audit_path`: 审计日志 JSONL 输出路径

## 3. 包装普通工具

最简单的接入方式是先包装工具，再把包装后的工具交给 agent。

```js
const guard = new AgentGuard("tool-demo");

function readNote({ path }) {
  return `reading: ${path}`;
}

const guardedReadNote = guard.wrap_tool(readNote, {
  name: "read_note",
  description: "Read a note file",
  capabilities: [],
});

async function main() {
  const result = await guardedReadNote.invoke({ path: "./notes/todo.txt" });
  console.log(result);
}

main();
```

返回结果可能有三类：

- 正常工具结果
- `{ agentguard: "blocked", ... }`
- `{ agentguard: "pending", ... }`

## 4. 包装 LLM

如果你手上是一个可调用函数，也可以直接先包 LLM：

```js
const guard = new AgentGuard("llm-demo");

const guardedLLM = guard.wrap_llm(async (request) => {
  return {
    text: `echo: ${JSON.stringify(request)}`,
  };
});

async function main() {
  const output = await guardedLLM.complete({ prompt: "hello" });
  console.log(output);
}

main();
```

## 5. LangChain 接入方式

LangChain 的接入建议优先走这条路径：

1. 先正常创建 LangChain agent
2. 调用 `guard.attach_langchain(agent)`
3. 再执行 `agent.invoke(...)`

示意代码：

```js
const { AgentGuard } = require("./index");

const guard = new AgentGuard("langchain-session", {
  user_id: "alice",
  agent_id: "langchain-agent",
});

const patched = guard.attach_langchain(agent, {
  wrap_tools: true,
  wrap_llm: true,
});

console.log("patched:", patched);
const result = await agent.invoke({
  messages: [{ role: "user", content: "help me inspect a file" }],
});
```

`attach_langchain()` 会尽量补丁这些位置：

- `tools`
- `tools_by_name`
- `_tools`
- `_tools_by_name`
- 常见 LLM 方法，例如 `invoke` / `predict` / `generate`

## 6. 审计记录

```js
const guard = new AgentGuard("audit-demo", {
  audit_path: "./tmp/agentguard-audit.jsonl",
});

// ... run tools / llm

const records = guard.flush_audit();
console.log(records);
```

## 7. 关闭 Guard

如果你启用了远端上报，结束时建议主动关闭：

```js
await guard.close();
```

## 8. LangChain Demo

完整的 JS LangChain demo 见：

- [examples/js/langchain-agentguard-demo.js](/f:/陈知乐/研究生/AgentGuard/examples/js/langchain-agentguard-demo.js)

如果你愿意，我下一步还可以继续补：

- OpenAI Agents SDK 的 JS demo
- 远端 `server_url` 模式 demo
- 一个真正可跑的 `package.json` 子包结构
