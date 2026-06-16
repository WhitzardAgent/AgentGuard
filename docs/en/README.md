# Quick Deployment

## Prerequisites
* Python >= 3.11
* pip
* Docker (if using Docker deployment)

## Setup

### Step 1: Prepare an agent

To apply access control to a target agent, you need its source code. Here we use LangChain as an example and build a minimal zero-shot ReAct agent.

#### 1. Install LangChain

```bash
pip install langchain==1.2.18
pip install langchain-openai==1.2.1
```

> This guide uses LangChain 1.2.18. You can also build agents with other frameworks.

#### 2. Write the agent code

```python
from langchain.agents import create_agent
from langchain.tools import tool

LLM_API_KEY = "<YOUR KEY>"         # Fill this manually
LLM_MODEL_NAME = "gpt-5.4-mini"

@tool
def retrieve_doc(id: int) -> str:
    """Retrieve a document by integer id."""
    return f"DOC#{id}: This is a mocked document body."

@tool
def send_email_to(doc: str, addr: str) -> str:
    """Send a document to an email address."""
    return f"Email has sent to {addr}: {doc}"

def build_llm():
    from langchain_openai import ChatOpenAI

    return ChatOpenAI(
        api_key=LLM_API_KEY,
        model=LLM_MODEL_NAME,
        temperature=0,
    )

def build_agent():
    return create_agent(
        model=build_llm(),
        tools=[retrieve_doc, send_email_to],
        system_prompt=(
            "You are a zero-shot ReAct style agent. Decide which tool to use, "
            "observe tool results, and continue until the user's task is complete."
        ),
    )

def run(agent, prompt):
    print("===================================")
    print(f"Prompt: {prompt}")
    result = agent.invoke(
        {
            "messages": [
                {
                    "role": "user",
                    "content": prompt,
                }
            ]
        }
    )
    print(f"Output: {result["messages"][-1].content}")
    print("===================================\n")

if __name__ == "__main__":
    agent = build_agent()

    run(agent, "Please retrieve document id=0 and send it to admin@example.com.")
    run(agent, "Please retrieve document id=0 and send it to alice@example.com.")
```

### Step 2: AgentGuard Client Importing

On top of the agent code from Step 1, you next need to import the AgentGuard client SDK. The client communicates with the control server, forwards the agent's runtime state, and receives access-control decisions.

#### 1. Install the AgentGuard client SDK

```bash
git clone https://github.com/WhitzardAgent/AgentGuard.git
cd AgentGuard
pip install -e .
```

#### 2. Import the client

Below is the complete code after importing the AgentGuard client. Lines marked with 🚩 show where the client is inserted:

```python
from langchain.agents import create_agent
from langchain.tools import tool

# 🚩 Import the AgentGuard client SDK
from agentguard import Guard, Principal

LLM_API_KEY = "<YOUR KEY>"         # Fill this manually
LLM_MODEL_NAME = "gpt-5.4-mini"

@tool
def retrieve_doc(id: int) -> str:
    """Retrieve a document by integer id."""
    return f"DOC#{id}: This is a mocked document body."

@tool
def send_email_to(doc: str, addr: str) -> str:
    """Send a document to an email address."""
    return f"Email has sent to {addr}: {doc}"

def build_llm():
    from langchain_openai import ChatOpenAI

    return ChatOpenAI(
        api_key=LLM_API_KEY,
        model=LLM_MODEL_NAME,
        temperature=0,
    )

def build_agent():
    return create_agent(
        model=build_llm(),
        tools=[retrieve_doc, send_email_to],
        system_prompt=(
            "You are a zero-shot ReAct style agent. Decide which tool to use, "
            "observe tool results, and continue until the user's task is complete."
        ),
    )

def run(agent, prompt):
    print("===================================")
    print(f"Prompt: {prompt}")
    result = agent.invoke(
        {
            "messages": [
                {
                    "role": "user",
                    "content": prompt,
                }
            ]
        }
    )
    print(f"Output: {result["messages"][-1].content}")
    print("===================================\n")

if __name__ == "__main__":
    agent = build_agent()

    # 🚩 Load the guard client
    guard = Guard(
        remote_url="http://<Control Server IP>:38080",      # Replace with your control server IP and port
        mode="enforce",
        fail_open=False,
    )

    # 🚩 Create a principal for the agent
    principal = Principal(
        agent_id="langchain-remote-demo",
        session_id="langchain-remote-session",
        role="default",
        trust_level=1,
    )

    # 🚩 Start a session with the principal
    guard.start(principal=principal, goal="langchain remote runnable host demo")

    # 🚩 Attach the guard to the LangChain agent
    guard.attach_langchain(agent)

    try:
        run(agent, "Please retrieve document id=0 and send it to admin@example.com.")
        run(agent, "Please retrieve document id=0 and send it to alice@example.com.")
    finally:
        # 🚩 Close the guard
        guard.close()
```

* `Guard()`: configures the control server address. This must match the server's configuration — see the control-server deployment section below.
* `Principal()`: defines the agent's identity, including agent ID, session ID, role, and trust level. These attributes are used to build constraints in access control policies.
* `guard.start()`: opens an access-control session, linking the agent's identity and task goal, and starts communicating with the control server. Call this before the agent begins its task.
* `guard.attach_langchain()`: attaches the client to a LangChain agent instance. Different frameworks use different adapters; see later sections for details.
* `guard.close()`: closes the session and releases resources. Call this after the agent has finished all tasks.

### Step 3: AgentGuard Checkers

AgentGuard supports pluggable checkers on both the client and the server. Both sides use the same normalized runtime schema, but they do not see the same input scope and they are not deployed to the same location. For implementation-level details, see `../../src/client/python/agentguard/checkers/README.md` and `../../src/server/backend/runtime/checkers/README.md`.

#### 1. Client vs. Server Checkers

- **Client checkers** run locally inside the agent process. They receive only the current `event: RuntimeEvent` and `context: RuntimeContext`, so they are best for lightweight low-latency filtering before a remote decision.
- **Server checkers** run on the control server. They receive the current `event`, the current `context`, and `trajectory_window: list[RuntimeEvent]`, so they are best for cross-step detection, centralized policy evaluation, and auditing.
- Client checker files must be placed under `../../src/client/python/agentguard/checkers/<phase>/`.
- Server checker files must be placed under `../../src/server/backend/runtime/checkers/<phase>/`.

#### 2. RuntimeEvent

`RuntimeEvent` is the normalized event object shared by client and server checkers:

```python
RuntimeEvent(
    event_id: str,
    event_type: EventType,
    timestamp: float,
    context: RuntimeContext,
    payload: dict[str, Any],
    risk_signals: list[str] = [],
    metadata: dict[str, Any] = {},
)
```

- `event_id`: unique identifier for the current runtime event.
- `event_type`: current runtime stage. Active values are `LLM_INPUT`, `LLM_OUTPUT`, `TOOL_INVOKE`, and `TOOL_RESULT`.
- `timestamp`: event creation time.
- `context`: the shared runtime context attached to this event.
- `payload`: the stage-specific content the checker actually inspects.
- `risk_signals`: risk labels already attached by earlier checkers or plugins.
- `metadata`: extra debug or adapter-specific information carried with the event.

Common payload shapes:

```python
# LLM_INPUT
{"messages": [...]} 
{"text": "..."}  # compatibility/simple adapters

# LLM_OUTPUT
{"output": ...}

# TOOL_INVOKE
{
    "tool_name": "send_email",
    "arguments": {"to": "...", "body": "..."},
    "capabilities": ["external_send"],
}

# TOOL_RESULT
{
    "tool_name": "read_file",
    "result": ...,
    "error": None,
}
```

#### 3. RuntimeContext

`RuntimeContext` is the session-level context propagated across events:

```python
RuntimeContext(
    session_id: str,
    user_id: str | None = None,
    agent_id: str | None = None,
    task_id: str | None = None,
    policy: str | None = None,
    policy_version: str | None = None,
    environment: str | None = None,
    metadata: dict[str, Any] = {},
)
```

- `session_id`: required session identifier used to associate all events in the same run.
- `user_id`: optional end-user identity behind the agent request.
- `agent_id`: optional agent instance or service identity.
- `task_id`: optional task or workflow identifier for the current unit of work.
- `policy`: optional logical policy name, source, or mode attached to the session.
- `policy_version`: optional policy version or snapshot identifier.
- `environment`: optional runtime environment such as `dev`, `staging`, or `prod`.
- `metadata`: free-form additional context such as tenant info, framework labels, or adapter-specific fields.

#### 4. `trajectory_window: list[RuntimeEvent]`

`trajectory_window` is only available to server-side checkers.

- It is a recent event window for the same session.
- Each element in the list is a full `RuntimeEvent`.
- Use it when detection depends on execution history instead of only the current event.
- Typical cases include "tool result exposed sensitive data, then a later tool call tries to send it externally" or "untrusted LLM output later flows into a shell command."

Client checkers do not receive `trajectory_window`. If your detection logic requires history, implement it as a server-side checker. In practice, the server window can include both the normal runtime trace and cached local decisions synchronized from the client.

#### 5. Custom Checker

##### Client-side checker

Client checkers must be placed in the phase folder that matches the event type:

```text
../../src/client/python/agentguard/checkers/llm_before/
../../src/client/python/agentguard/checkers/llm_after/
../../src/client/python/agentguard/checkers/tool_before/
../../src/client/python/agentguard/checkers/tool_after/
```

Example:

```python
from agentguard.plugins.base import BaseChecker, CheckResult
from agentguard.plugins.registry import register
from agentguard.schemas.context import RuntimeContext
from agentguard.schemas.events import EventType, RuntimeEvent


@register(
    name="my_client_checker",
    description="Detect risky tool arguments on the client side.",
)
class MyClientChecker(BaseChecker):
    event_types = [EventType.TOOL_INVOKE]

    def check(self, event: RuntimeEvent, context: RuntimeContext) -> CheckResult:
        tool_name = event.payload.get("tool_name")
        arguments = event.payload.get("arguments") or {}
        if tool_name == "send_email" and arguments.get("to", "").endswith("@external.com"):
            return CheckResult(risk_signals=["external_send"])
        return CheckResult.empty()
```

##### Server-side checker

Server checkers must be placed in the matching server folder:

```text
../../src/server/backend/runtime/checkers/llm_before/
../../src/server/backend/runtime/checkers/llm_after/
../../src/server/backend/runtime/checkers/tool_before/
../../src/server/backend/runtime/checkers/tool_after/
```

Example:

```python
from backend.runtime.checkers.base import BaseChecker, CheckResult
from backend.runtime.checkers.registry import register
from shared.schemas.context import RuntimeContext
from shared.schemas.events import EventType, RuntimeEvent


@register(
    name="my_server_checker",
    description="Detect multi-step exfiltration on the server side.",
)
class MyServerChecker(BaseChecker):
    event_types = [EventType.TOOL_INVOKE]

    def check(
        self,
        event: RuntimeEvent,
        context: RuntimeContext,
        trajectory_window: list[RuntimeEvent] | None = None,
    ) -> CheckResult:
        trajectory_window = trajectory_window or []
        if trajectory_window and event.payload.get("tool_name") == "send_email":
            return CheckResult(risk_signals=["cross_step_review"])
        return CheckResult.empty()
```

The server also includes a built-in rule-based checker at `../../src/server/backend/runtime/checkers/tool_before/rule_based_check/checker.py`. Its registered name is `rule_based_check`.

##### Checker configuration

After adding the checker classes, reference their registered names in checker config:

```json
{
  "phases": {
    "tool_before": {
      "local": ["my_client_checker"],
      "remote": ["rule_based_check", "my_server_checker"]
    }
  }
}
```

- `local` is loaded by the client checker manager.
- `remote` is loaded by the server checker manager.
- Even if both names appear in the same config file, the implementation files must still be deployed to the correct client or server folder.


#### 6. Custom Auditor

AgentGuard also supports post-hoc auditing on the backend. Unlike checkers, which run inline during the live runtime, custom auditors run on the full stored trace for a `session_id` / `agent_id` / `user_id` tuple after events have already been recorded. This is useful for compliance review, incident triage, retrospective analysis, and generating summarized severity labels for the frontend.

The shared auditor abstractions live under:

```text
../../src/server/backend/audit/base.py
../../src/server/backend/audit/manager.py
../../src/server/backend/audit/registry.py
```

Concrete auditor implementations must be placed under:

```text
../../src/server/backend/audit/auditors/
```

The backend-discovered auditor interface is:

```python
from backend.audit.base import AuditResult, AuditTraceEntry, BaseAuditor
from backend.audit.registry import register


@register(
    name="my_trace_auditor",
    description="Summarize a stored trace into a severity label.",
)
class MyTraceAuditor(BaseAuditor):
    def audit(
        self,
        trace: list[AuditTraceEntry],
    ) -> AuditResult:
        if any((record.get("decision") or {}).get("decision_type") == "deny" for record in trace):
            return AuditResult(level="high", reason="The trace contains denied actions.")
        return AuditResult.ok()
```

Each `AuditTraceEntry` contains the canonical trace fields `session_id`, `agent_id`, `user_id`, `reason`, `event`, `decision`, `checker_result`, and `plugin_results`. Auditors should treat `event` as the primary runtime payload and the other fields as optional enrichments from the backend trace pipeline.

`AuditResult` currently uses four normalized severity levels: `critical`, `high`, `warning`, and `ok`. Each result also includes a human-readable `reason` and optional `metadata`.

After you add the auditor implementation, the backend discovers it by registered name. The frontend can then:

- call `GET /v1/backend/auditors` to list available auditors and descriptions
- call `POST /v1/backend/audit/custom/run` with `session_id`, `agent_id`, `user_id`, and `auditor_name` to run one auditor on the corresponding stored trace

For a concrete built-in example, see `../../src/server/backend/audit/auditors/trace_risk_summary.py`.

### Step 4: Write a policy and deploy the control server

AgentGuard uses a client-server architecture. All management operations — agent monitoring, policy configuration, policy enforcement, and decision dispatch — happen on the control server. This is especially useful when an organization has multiple agent deployments that need centralized governance.

Although the control server and agents can run on the same host, we recommend deploying the server on a dedicated machine for better scalability. The instructions below assume you've chosen a separate host for the server.

First, clone the project on the control server:

```bash
git clone https://github.com/WhitzardAgent/AgentGuard.git
cd AgentGuard
```

#### 1. Write a checker config file

Before writing any access-control policy, first define which server-side checker is active in this quick start:

```bash
mkdir -p config

cat <<EOF > config/checkers.json
{
  "phases": {
    "llm_before": {
      "local": [],
      "remote": []
    },
    "llm_after": {
      "local": [],
      "remote": []
    },
    "tool_before": {
      "local": [],
      "remote": ["rule_based_check"]
    },
    "tool_after": {
      "local": [],
      "remote": []
    }
  }
}
EOF
```

This config means: only the `tool_before` phase runs a remote checker, and that checker is the built-in `rule_based_check`. All other phases are empty. In other words, the server will evaluate your policy rules only right before a tool call runs. That keeps the quick start focused on access-control decisions around tool execution, without introducing additional LLM-phase or tool-result checkers yet.

#### 2. Create an access control policy

Our agent has two tools: `retrieve_doc` and `send_email_to` — one retrieves a document by ID, the other sends it to an email address. Suppose we want agents with trust level below 2 to only send the confidential document (id 0) to `admin@example.com`, and block all other recipients. We can create a policy file:

```bash
mkdir -p rules

cat <<EOF > rules/block_email_send.rules
RULE: block_untrusted_email_send
TRACE: Retriever -> ...? -> Mailer
CONDITION: Retriever.name == "retrieve_doc"
           AND Mailer.name == "send_email_to"
           AND Retriever.id == 0
           AND Mailer.addr != "admin@example.com"
           AND principal.trust_level < 2
POLICY: DENY
Severity: high
Category: data_exfiltration
Reason: "Low-trust principal cannot send document 0 to non-admin recipients"
EOF
```

AgentGuard provides a dedicated DSL for writing policies, which we'll cover in detail in [DSL Basic Structure](./policies/dsl_basic_structure.md).

#### 3. Deploy the AgentGuard control server

We offer two deployment methods: Docker and source code.

##### Docker deployment (recommended)

> You need Docker installed first.

Docker deployment is straightforward. First set the checker config path in `.env`:

```bash
cp .env.example .env
# then set:
# AGENTGUARD_SERVER_CHECKER_CONFIG=./config/checkers.json
```

Then run this command from the project root:

```bash
./scripts/start.sh -d
```

The control server listens on port `38080` by default.

We also provide a web UI that lets you monitor agent runtime status, audit policy enforcement records, and configure policies interactively. For new users, we recommend using the UI to manage access control. Visit `http://localhost:38008` in your browser to access it.

Below is a screenshot of the interactive policy configuration UI:

![UI policy configuration](../figs/ui_configure_policy.png)

We'll cover interactive policy configuration in detail in [Quick Configuration](./policies/quick_config.md).

##### Source-code deployment

If you prefer source-code deployment, install the dependencies manually:

```bash
pip install -e ".[server]"
```

Then start the control server:

```bash
AGENTGUARD_SERVER_CHECKER_CONFIG=./config/checkers.json \
python -m agentguard serve \
    --host 0.0.0.0 \
    --port 38080 \
    --policy rules/block_email_send.rules
```

* `--port`: the port the control server listens on.
* `--policy`: path to a policy file. You can pass multiple files with `--policy fileA --policy fileB ...`.

You can also start the UI:

```bash
./scripts/run-frontend.sh
```

Visit `http://localhost:8008` to access the UI.

### Step 5: Run the agent

On the agent host, run the agent code:

```bash
python <LANGCHAIN_AGENT_FILE>
```

Expected behavior: the first task succeeds — the confidential document is sent to `admin@example.com`. The second task fails — when the agent tries to send the same document to `alice@example.com`, an exception is raised and the call is denied.

Expected output:

```
===================================
Prompt: Please retrieve document id=0 and send it to admin@example.com.
Output: Done — document 0 was retrieved and sent to admin@example.com.
===================================

===================================
Prompt: Please retrieve document id=0 and send it to alice@example.com.
Traceback (most recent call last):
  File "...", line 83, in <module>
    run(agent, "Please retrieve document id=0 and send it to alice@example.com.")
  ...
    raise DecisionDenied(
agentguard.models.errors.DecisionDenied: block_untrusted_email_send
During task with name 'tools' and id 'ab34afab-e0f3-14f6-7517-bba2e47f0ea6'
```
