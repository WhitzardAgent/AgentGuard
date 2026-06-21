# Core Concepts

This page explains the concepts you will see across AgentGuard docs and configuration. AgentGuard is a zero-trust security foundation for AI agents: it integrates into an existing agent runtime, observes LLM and tool events, evaluates configured safeguards, and returns decisions or audit records without replacing the agent's own planning logic.

## Agent

An agent is the application or runtime unit that receives a task, plans steps, calls an LLM, and may invoke tools. It can be built with LangChain, AutoGen, OpenAI Agents SDK, Openclaw, or a custom framework.

AgentGuard does not replace the agent. The agent still owns task understanding, reasoning, orchestration, and tool selection. AgentGuard adds a security layer around the runtime events produced by that agent.

## Runtime Phases

AgentGuard can inspect multiple phases of an agent run:

- `llm_before`: before a request is sent to the LLM
- `llm_after`: after the LLM returns output
- `tool_before`: before a tool invocation is executed
- `tool_after`: after a tool returns a result

This means AgentGuard is not limited to tool-call access control. Even if an agent does not call tools, AgentGuard can still inspect and intercept risks in LLM inputs and outputs.

## AgentGuard Client

The AgentGuard client runs inside or alongside the agent process. In most integrations, users interact with it through `Guard`.

The client is responsible for:

- attaching to an agent framework or custom runtime
- normalizing LLM and tool activity into `RuntimeEvent` objects
- running client-side plugins when configured
- sending remote decision requests to the control server when needed
- enforcing the returned decision in the agent process

You can think of it as AgentGuard's runtime probe and enforcement point on the agent side.

## Control Server

The control server is AgentGuard's centralized management and decision component.

It typically handles:

- receiving runtime events from AgentGuard clients
- evaluating configured server-side plugins and access-control policies
- returning allow, deny, or review decisions
- storing traces for runtime monitoring and audit
- supporting web-console workflows such as policy configuration and approval review

This centralized control-plane architecture lets organizations manage many distributed agents through one policy and audit surface.

## Principal

A principal describes the identity and trust attributes of the agent or caller behind a runtime event.

Common principal attributes include:

- agent ID
- session ID
- user ID
- role
- trust level

Policies use principal attributes to express differentiated constraints. For example, a low-trust agent may be blocked from sending documents externally, while a privileged role may be allowed or routed to review.

## Session

A session is the context scope for one agent task or run. It links related LLM events, tool calls, tool results, decisions, and trace entries.

Sessions matter because many risks are cross-step rather than single-step. For example, "read a sensitive file, then upload it to an external endpoint" requires the server to connect multiple events in the same run.

## RuntimeEvent

`RuntimeEvent` is the normalized event object used by client and server plugins. It represents one LLM or tool event in a consistent shape.

Common event types are:

- `LLM_INPUT`
- `LLM_OUTPUT`
- `TOOL_INVOKE`
- `TOOL_RESULT`

The event payload is typed by event phase:

- `LLMInput(messages=[{"role": "...", "content": "..."}])`
- `LLMOutput(output="...")`
- `ToolInvoke(tool_name="...", arguments={...}, capabilities=[...])`
- `ToolResult(tool_name="...", result="...")`

Plugins and policies inspect these fields to identify risk and produce decisions.

## RuntimeContext

`RuntimeContext` is the session-level context propagated across events. It includes identifiers such as `session_id`, `agent_id`, `user_id`, task metadata, policy metadata, and arbitrary integration-specific metadata.

Plugins and policies use runtime context to understand who is acting, which task the event belongs to, which environment is involved, and which client or server configuration applies.

## Tool

A tool is an operational capability the agent can invoke, such as sending email, making HTTP requests, running shell commands, reading files, writing files, or querying databases.

Tools are high-impact governance targets because they affect real systems and data. AgentGuard is especially useful for:

- outbound tools such as email, HTTP, or messaging
- shell and system-command tools
- filesystem read or write tools
- database read or write tools
- workflows where untrusted input may influence later actions

## Plugin

Plugins are AgentGuard's modular runtime inspection units. They can run on the client side or on the server side.

Client plugins:

- run inside the agent process
- receive the current `RuntimeEvent` and `RuntimeContext`
- are useful for low-latency local checks and lightweight filtering

Server plugins:

- run on the control server
- receive the current event and context
- can also use `trajectory_window` to inspect recent events from the same session
- are useful for cross-step detection, centralized policy evaluation, and audit-oriented analysis

Plugin configuration is phase-based. Each phase can define `client` plugins for the client runtime and `server` plugins for the control server. Each plugin entry is a spec object such as `{"name": "rule_based_plugin", "env": {}}`. In the current implementation, `client` plugin specs can pass `env` and constructor settings into client plugins, while `server` plugin specs are resolved by `name` or `class`. Implementation-level details live in [AgentGuard Plugins](plugins.md).

## Policy

A policy is a user-defined control rule. In the built-in flow, these DSL policies are consumed by the `rule_based_plugin` server plugin to specify when a runtime action should be allowed, denied, or sent to review.

AgentGuard includes a built-in access-control strategy set and supports policy definitions through DSL rules. Policies commonly express constraints such as:

- low-trust principals cannot send sensitive documents externally
- shell commands matching dangerous patterns must be denied
- access to unknown destinations requires human review
- a cross-step sequence such as database read followed by external email should be blocked or reviewed

Policies work together with plugins: `rule_based_plugin` evaluates explicit access-control rules, while other plugins can attach risk signals or produce additional decision candidates.

## Decision

A decision is the result of AgentGuard's runtime evaluation. Typical outcomes include:

- allow the event to proceed
- deny and block execution
- route the operation to human or model-based review
- record risk signals and metadata for audit

For tool invocations, the decision determines whether the tool actually runs. For LLM input and output events, the decision can be used to block or constrain unsafe content before it continues through the agent workflow.

## Audit and Custom Auditor

Audit records capture runtime events, decisions, plugin results, and related metadata so users can understand what happened and why.

Custom auditors are post-hoc analysis units that run over stored traces after events have already been recorded. They are useful for:

- compliance review
- incident triage
- retrospective risk analysis
- generating summarized severity labels for the frontend

See [Custom Auditors](auditors.md) for implementation-level details.

## Provenance and Cross-step Risk

Many agent risks depend on where information came from and how it later flows through the session. AgentGuard uses stored runtime context and trace windows to support cross-step reasoning, such as:

- sensitive data was read earlier and later sent externally
- untrusted LLM output later influenced a shell command
- an agent repeatedly tried different destinations after being denied

When integrating AgentGuard, it is useful to label tool boundaries, data sensitivity, and trust attributes clearly. Those labels make policy rules and plugin checks more precise.
