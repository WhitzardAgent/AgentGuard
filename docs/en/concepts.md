# Core Concepts

This page covers the most common concepts you'll encounter when using AgentGuard. The focus is not on internals, but on helping you understand how to integrate the system, configure it, and what objects your policies ultimately target.

## Agent

An "agent" here refers to an agent application or runtime unit you're already using — built with frameworks like LangChain, AutoGen, Dify, OpenAI Agents SDK, or your own custom tool-calling pipeline.

AgentGuard does not replace the agent's task execution logic. The agent is still responsible for understanding the task, planning steps, and initiating tool calls. AgentGuard is responsible for runtime inspection of those calls.

## AgentGuard Client

The AgentGuard client lives on the agent side and connects tool calls to the control service. In practice, users interact directly with `Guard`.

Its responsibilities include:

* Communicating with the control server, forwarding the agent's current runtime state as `RuntimeEvent`
* Intercepting the agent's tool call requests
* Submitting the current operation to the control server for a decision via HTTP
* Determining the tool's execution policy based on the decision

You can think of it as AgentGuard's probe on the agent side.

## Principal

A principal describes "what attributes the agent performing this operation has." In policy evaluation, principal information is typically used to differentiate permission scopes and trust levels across agents.

Common principal attributes include:

* Agent ID
* Session ID
* Role
* Trust level

The value of these attributes is that they let policies express differentiated constraints — for example, blocking low-trust agents from certain operations, or restricting high-risk tools to specific roles only.

## Session

A session represents the context scope of the agent's current task round.

A complete task often involves multiple tool calls, and many security judgments can't be made from a single operation alone. For instance, if the agent read sensitive data earlier and is now about to send content externally, that typically requires evaluating the entire task round.

So sessions serve to:

* Correlate multiple tool calls within the same task round
* Preserve necessary context information
* Provide a basis for cross-step rule decisions

## Tool

A tool is the capability unit that an agent uses to perform real operations — sending email, making HTTP requests, running commands, reading/writing files, or querying databases.

In AgentGuard, tools are the primary governance target. The reason is straightforward: the actual security impact comes not from model-generated text, but from the real actions triggered by tools.

You should pay special attention to access control for these tool categories:

* Outbound tools
* System operation tools
* Data write tools
* Sensitive data read tools

## Policy

A policy is a control rule defined by the user. It specifies under what conditions a type of tool call should be allowed, denied, or sent to human review.

From a usage perspective, policies typically revolve around two types of intent:

### Deny

Handles operations that must never happen, for example:

* Dangerous command execution
* Sensitive data exfiltration
* Unauthorized modifications to critical resources

### Approve

Handles operations that are high-risk but shouldn't be flatly denied, for example:

* Sending content to external contacts
* Accessing destinations not pre-approved
* Running operations with wide impact

For most projects, we recommend starting with deny rules, then gradually introducing more granular approval policies.

## Control Server

The control server is AgentGuard's server-side component. It centralizes rule evaluation and management operations.

The control server typically handles:

* Receiving decision requests from agents
* Policy definition and evaluation
* Coordinating human approval workflows
* Providing audit and management interfaces

## Audit

Audit records the key operations an agent has performed and how they were handled.

Audit information is primarily used for:

* Tracing an agent's actual behavior
* Analyzing why an operation was denied or constrained
* Verifying that rules work as expected
* Providing evidence for incident investigation and compliance records

Audit is not just a post-hoc tracking tool — it's also an important reference during policy tuning.

## Provenance

In practice, users often need to determine whether an outbound operation involves sensitive data that was read earlier in the session.

This is where the "provenance" concept matters. For AgentGuard, only when the system can identify which data is sensitive can relevant policies take effect during subsequent outbound, sharing, or processing operations.

If you want the system to restrict sensitive data exfiltration, you need to explicitly mark which data is sensitive during the integration process, so that targeted access control policies can be written.
