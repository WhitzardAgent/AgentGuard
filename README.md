# 🛡️ AgentGuard

<p align="center">
  <a href="https://whitzard.tech/AgentGuard/en/">
    <img src="https://img.shields.io/badge/Document-Docs-0ea5e9?style=for-the-badge&logo=gitbook&logoColor=white" alt="Document" />
  </a>
  <a href="https://github.com/WhitzardAgent/AgentGuard/releases">
    <img src="https://img.shields.io/badge/Release-v2.1-111827?style=for-the-badge&logo=github&logoColor=white" alt="Release v2.1" />
  </a>
  <a href="./LICENSE">
    <img src="https://img.shields.io/badge/License-GPL%20v3-16a34a?style=for-the-badge&logo=open-source-initiative&logoColor=white" alt="License" />
  </a>
</p>

<p align="center">
  <strong>English</strong> |
  <a href="./README_CN.md">简体中文</a>
</p>

<p align="center">
  <strong>AgentGuard: Zero-Trust Security Foundation for AI Agents</strong>
</p>

<p align="center">
  Seamlessly integrates with existing agent frameworks and supports modular deployment of existing rule-based and model-based security strategies.
</p>

<table align="center" width="100%" cellspacing="0" cellpadding="0">
  <tr>
    <td align="center" width="25%" style="padding: 20px 18px; border: 1px solid #e5e7eb; border-radius: 18px; background: #ffffff;">
      <div style="font-size: 28px; line-height: 1; margin-bottom: 10px;">🧩</div>
      <small><strong>Seamless&nbsp;Integration</strong></small>
    </td>
    <td align="center" width="25%" style="padding: 20px 18px; border: 1px solid #e5e7eb; border-radius: 18px; background: #ffffff;">
      <div style="font-size: 28px; line-height: 1; margin-bottom: 10px;">🧱</div>
      <small><strong>Modular&nbsp;Security&nbsp;Strategies</strong></small>
    </td>
    <td align="center" width="25%" style="padding: 20px 18px; border: 1px solid #e5e7eb; border-radius: 18px; background: #ffffff;">
      <div style="font-size: 28px; line-height: 1; margin-bottom: 10px;">🛡️</div>
      <small><strong>Multi&#8209;Risk&nbsp;Coverage</strong></small>
    </td>
    <td align="center" width="25%" style="padding: 20px 18px; border: 1px solid #e5e7eb; border-radius: 18px; background: #ffffff;">
      <div style="font-size: 28px; line-height: 1; margin-bottom: 10px;">👁️</div>
      <small><strong>Visual&nbsp;Audit</strong></small>
    </td>
  </tr>
</table>


> [!IMPORTANT]
> This project is still under active development and may contain bugs. Contributions via Issues and PRs are welcome.

AgentGuard is a zero-trust security foundation for AI agents. Compatible with existing security strategies, it identifies and blocks security risks before each LLM call, after each LLM output, before each tool invocation, and after execution according to configurable safeguards, and it also supports post-hoc auditing of stored traces through pluggable custom auditors.

Today, AgentGuard covers several key technical areas highlighted in Anthropic's [Zero Trust for AI Agents](https://claude.com/blog/zero-trust-for-ai-agents), including access control & privilege management, observability & auditing, and behavioral monitoring & response.

![AgentGuard Positioning](./docs/figs/positioning.png)

AgentGuard can be integrated into existing agent frameworks without modifying the underlying execution logic. Currently, it supports LangChain, AutoGen, OpenAI Agents SDK, and [OpenClaw](https://github.com/openclaw/openclaw), and we are continuously expanding support for additional agent ecosystems and frameworks. For JavaScript-side integration details, refer to the [OpenClaw adapter document](https://whitzard.tech/AgentGuard/en/how-to-plugin/openclaw_adapter.html).

## ✨ Features

### 1. Multi-Dimensional Security Protection

#### Multi-Phase Intervention

According to configured safeguards, AgentGuard can intervene before each LLM call, after each LLM output, before each tool invocation, and after execution to identify and block security risks across the full agent runtime. In addition to inline intervention, it also supports post-hoc auditing over stored runtime traces through pluggable custom auditors.

#### Seamless Reuse of Existing Security Strategies

AgentGuard provides a unified interface for adapting existing security protections. Through its modular plugin architecture, rule-based and model-based strategies can be plugged in behind the same interface and enabled dynamically based on practical needs. Today, AgentGuard includes a built-in access-control strategy set, and users can build additional security policies through DSL definitions.

#### Single-Tool and Cross-Tool Protection

AgentGuard can evaluate both individual tool calls and cross-step attack chains. By efficiently storing runtime context, it can detect behaviors such as "read from a database, then send email," "read a sensitive file, then upload it to an external HTTP endpoint," or "external input eventually flows into a shell command."

### 2. Seamless Integration with Agent Frameworks

AgentGuard sits between the LLM-based planning engine and tools, and does not interfere with agent planning, reasoning, or task orchestration. Adapters are provided for several mainstream agent frameworks, allowing users to integrate AgentGuard with minimal code and without modifying framework internals or heavily refactoring existing agents. For frameworks not yet supported, AgentGuard offers a straightforward development interface for building custom adapters. See the [client plugin guide](https://whitzard.tech/AgentGuard/en/plugins/custom_client_plugin.html) and the [server plugin guide](https://whitzard.tech/AgentGuard/en/plugins/custom_server_plugin.html).

Currently, we support the following agent frameworks:
- [LangChain](https://github.com/langchain-ai/langchain)
- [AutoGen](https://github.com/microsoft/autogen)
- [OpenAI Agents SDK](https://github.com/openai/openai-agents-python)
- [OpenClaw](https://github.com/openclaw/openclaw)

Refer to the [OpenClaw adapter document](https://whitzard.tech/AgentGuard/en/how-to-plugin/openclaw_adapter.html).

### 3. Visual Policy Configuration & Audit

AgentGuard ships with a web console for managing agents. The visual interface lets users configure policies interactively without hand-writing DSL code. The policy editor relies heavily on dropdowns and other selection-based controls to reduce the policy configuration burden.

The runtime dashboard displays agent health, recent traffic, pending approval requests, and audit records. For any tool call that triggers a policy, users can inspect the matched rules, risk scores, final decisions, and the raw event/decision JSON, making it easy to understand why a particular call was denied or escalated for review.

### Custom Auditor Extensibility

The backend also supports pluggable custom auditors for post-hoc trace review. Shared auditor abstractions live under `src/server/backend/audit/`, while concrete auditors live under `src/server/backend/audit/auditors/`. See the documentation chapter on [custom auditors](https://whitzard.tech/AgentGuard/en/auditors.html).

### 4. Cluster Management

AgentGuard uses a centralized control-plane architecture to govern distributed agent processes. Agents can be deployed across multiple nodes in the network, while policy configuration and runtime monitoring are managed centrally through the control server. This architecture is particularly well-suited for organizations that need unified management across a large fleet of agents.

## 🚀 Quick Start

### 1. Write Plugin Config, Then Write Access Control Policies and Start the Control Server

> Docker must be installed first.

Choose a host to serve as the control server, then clone AgentGuard:

```bash
git clone https://github.com/WhitzardAgent/AgentGuard.git
cd AgentGuard
```

First, create a plugin config file for the control server:

```bash
mkdir -p config

cat <<EOF > config/plugins.json
{
  "phases": {
    "llm_before": {
      "client": [],
      "server": []
    },
    "llm_after": {
      "client": [],
      "server": []
    },
    "tool_before": {
      "client": [],
      "server": [
        {
          "name": "rule_based_plugin",
          "env": {}
        }
      ]
    },
    "tool_after": {
      "client": [],
      "server": []
    }
  }
}
EOF
```

This config tells AgentGuard which plugins run in each runtime phase. In this quick start, only `tool_before` enables one server plugin: `rule_based_plugin`. That means the server evaluates access-control rules right before a tool call is executed, while all other phases stay empty. This keeps the first demo simple: the client forwards tool-invocation decisions to the server, and the server uses the built-in rule-based plugin to match your policy rules and return an allow/deny decision.

Then create an access control policy:

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

This policy involves two agent tools: `retrieve_doc` and `send_email_to`, which retrieve a document by its id and send document content to a specified email address, respectively. The policy states that agents with a trust level below 2 may only send the confidential document (id=0) to `admin@example.com`; sending it to any other recipient is denied.

> AgentGuard also supports visual policy configuration with dynamic hot-reloading. See the [visual policy configuration document](https://whitzard.tech/AgentGuard/en/policies/quick_config.html) for details.

Next, configure the environment variables for the control server:

> Skip this step if the defaults are sufficient.

```bash
cp .env.example .env
vi .env
```

Set the server plugin config path in `.env`:

```bash
AGENTGUARD_SERVER_PLUGIN_CONFIG=./config/plugins.json
```

Start the control server:

```bash
./scripts/start.sh -d
```

The control server listens on port `38080`.
The UI listens on port `38008`.

Visit `http://localhost:38008` to see the UI.

### 2. Agent-Side Setup

On the agent host, run:

```bash
git clone https://github.com/WhitzardAgent/AgentGuard.git
cd AgentGuard
pip install -e .
```

The following LangChain example shows the required integration points:

> Install the dependencies first:
> ```bash
> pip install langchain==1.2.18
> pip install langchain-openai==1.2.1
> ```

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

Lines marked with 🚩 indicate where the AgentGuard client is inserted into the agent code. Make sure to replace the LLM API key and control server address with the values from your deployment.

### 3. Run the Agent

Execute the LangChain agent script:

```bash
python <LANGCHAIN_AGENT_FILE>
```

The agent performs two different tasks. The first sends document 0 (simulating a confidential file) to the admin email address, which the policy permits. The second sends the same document to another user, which the policy forbids.

AgentGuard is expected to allow the first run and deny the second.

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

Currently, AgentGuard enforces denials by raising an exception (hard blocking). A future version will introduce soft blocking, where the LLM receives an error message indicating the action was denied without terminating the agent process.

### 4. Manage the Agent's Runtime with UI

You can inspect the agent's runtime status and policy enforcement audit logs through the UI.

The UI also supports visual policy configuration and dynamic hot-reloading.

For additional deployment details, refer to the [Documentation](https://whitzard.tech/AgentGuard/en/).

## 🎬 Demo Video

https://github.com/user-attachments/assets/75a17e37-7f51-4c59-96fa-ea449eb79859

## 🏆 Advantages over Existing Frameworks

Current defenses for agent security mainly fall into two categories: **malicious-intent detection at the model layer** and **tool-call behavior interception**. The former strengthens the underlying LLM through fine-tuning or detects unsafe intent by analyzing the model's reasoning process; the latter enforces predefined security policies at tool invocation time based on call traces, arguments, and runtime context to identify, block, or escalate high-risk actions.

Given that model fine-tuning is often expensive to train and deploy, and that many models do not expose a complete reasoning trace, AgentGuard focuses on practical runtime controls around both LLM interaction and tool execution. This approach does not require changing the underlying model. Instead, it places security controls around what the agent exchanges with the model and actually does in the environment, which makes it easier to integrate into existing agent stacks and more practical for production deployment.

As illustrated below, existing tool-call-based defenses address parts of the problem, but they are often fragmented and optimized for narrow risk scenarios, such as dangerous command filtering, isolated prompt-injection mitigation, or limited auditing. In contrast, AgentGuard provides a unified framework that more systematically covers access control, runtime behavior monitoring, and execution auditing. This design is also more closely aligned with the enterprise agent-security goals emphasized in Anthropic's [Zero Trust for AI Agents](https://claude.com/blog/zero-trust-for-ai-agents), including least-privilege permissions, constrained tool use, observable execution, and auditable policy enforcement.

![Advantages over Existing Frameworks](./docs/figs/comparison_en.png)

## 🏗️ Architecture

The high-level architecture of AgentGuard is shown below.

<p align="center">
  <img src="./docs/figs/overview.png" alt="AgentGuard architecture" width="50%" />
</p>

- **Client**: With minimal code modifications, the AgentGuard client integrates into agent frameworks and can intercept before and after LLM calls, as well as before and after tool invocations. It can perform lightweight local filtering on the client side and forward events to the server for deeper inspection by configured plugins.
- **Server**: The server receives information from clients, uses configured plugins to evaluate agent actions against policies, produces policy decisions, and sends them back to clients. It also monitors agent status for administrative auditing.
- **Plugin Extensibility**: Both client and server support pluggable plugins. To add custom plugins, see the [client plugin guide](https://whitzard.tech/AgentGuard/en/plugins/custom_client_plugin.html) and the [server plugin guide](https://whitzard.tech/AgentGuard/en/plugins/custom_server_plugin.html).
- **Custom Auditor Extensibility**: The backend also supports pluggable custom auditors for post-hoc trace review. Shared auditor abstractions live under `src/server/backend/audit/`, while concrete auditors live under `src/server/backend/audit/auditors/`. See the documentation chapter on [custom auditors](https://whitzard.tech/AgentGuard/en/auditors.html).

## 👥 Contributors

<table width="100%">
  <tr>
    <td width="20%" valign="top">
      <a href="https://djrrr.github.io/" target="_blank" rel="noreferrer"><img src="./docs/figs/avatar-placeholder.svg" width="56" height="56" alt="Jiarun Dai" /></a><br />
      <a href="https://djrrr.github.io/" target="_blank" rel="noreferrer"><sub><strong>Jiarun Dai</strong></sub></a><br />
      <sub>Asst. Prof.</sub><br />
      <sub>Fudan University</sub>
    </td>
    <td width="20%" valign="top">
      <img src="./docs/figs/avatar-placeholder.svg" width="56" height="56" alt="Jiaqi Luo" /><br />
      <sub><strong>Jiaqi Luo</strong></sub><br />
      <sub>PhD Student</sub><br />
      <sub>Fudan University</sub>
    </td>
    <td width="20%" valign="top">
      <img src="./docs/figs/avatar-placeholder.svg" width="56" height="56" alt="Songyang Peng" /><br />
      <sub><strong>Songyang Peng</strong></sub><br />
      <sub>Master Student</sub><br />
      <sub>Fudan University</sub>
    </td>
    <td width="20%" valign="top">
      <img src="./docs/figs/avatar-placeholder.svg" width="56" height="56" alt="Zhile Chen" /><br />
      <sub><strong>Zhile Chen</strong></sub><br />
      <sub>Master Student</sub><br />
      <sub>Fudan University</sub>
    </td>
    <td width="20%" valign="top">
      <img src="./docs/figs/avatar-placeholder.svg" width="56" height="56" alt="Jingren Huang" /><br />
      <sub><strong>Jingren Huang</strong></sub><br />
      <sub>Master Student</sub><br />
      <sub>Fudan University</sub>
    </td>
  </tr>
  <tr>
    <td width="20%" valign="top">
      <a href="https://zhxshen.github.io/" target="_blank" rel="noreferrer"><img src="./docs/figs/avatar-placeholder.svg" width="56" height="56" alt="Zhuoxiang Shen" /></a><br />
      <a href="https://zhxshen.github.io/" target="_blank" rel="noreferrer"><sub><strong>Zhuoxiang Shen</strong></sub></a><br />
      <sub>Eng.D Student</sub><br />
      <sub>Fudan University</sub>
    </td>
    <td width="20%" valign="top">
      <a href="https://ravensanstete.github.io/" target="_blank" rel="noreferrer"><img src="./docs/figs/avatar-placeholder.svg" width="56" height="56" alt="Xudong Pan" /></a><br />
      <a href="https://ravensanstete.github.io/" target="_blank" rel="noreferrer"><sub><strong>Xudong Pan</strong></sub></a><br />
      <sub>Asst. Prof.</sub><br />
      <sub>Fudan University</sub>
    </td>
    <td width="20%" valign="top">
      <a href="https://ghong.site/" target="_blank" rel="noreferrer"><img src="./docs/figs/avatar-placeholder.svg" width="56" height="56" alt="Geng Hong" /></a><br />
      <a href="https://ghong.site/" target="_blank" rel="noreferrer"><sub><strong>Geng Hong</strong></sub></a><br />
      <sub>Asst. Prof.</sub><br />
      <sub>Fudan University</sub>
    </td>
    <td width="20%" valign="top">&nbsp;</td>
    <td width="20%" valign="top">&nbsp;</td>
  </tr>
</table>

Listed in no particular order. Thanks to everyone who helped shape AgentGuard.

## 🎯 Roadmap

- Support more mainstream frameworks
- Support agent systems in more programming languages
- Enable protection for multi-agent scenarios
- Expand LLM input/output monitoring and plugin coverage
- Add more varied policy actions
- Provide automatic security policy recommendations

## 📚 Citation

If you use AgentGuard in your research, please cite:

```bibtex
@misc{agentguard2026,
      title={AgentGuard: An Attribute-Based Access Control Framework for Tool-Use LLM-Based Agent},
      author={Jiaqi Luo* and Songyang Peng* and Jiarun Dai and Zhile Chen and Zhuoxiang Shen and Geng Hong and Xudong Pan and Yuan Zhang and Min Yang},
      year={2026},
      eprint={2605.28071},
      archivePrefix={arXiv},
      primaryClass={cs.CR},
      url={https://arxiv.org/abs/2605.28071},
}
```

## 📜 License

This project is licensed under the [GNU General Public License v3.0 (GPLv3)](./LICENSE).

## 📝 Version Log

### v2.1

- Added compatibility with LlamaIndex and Langflow.
- Added compatibility with the AgentDoG plugin.
- Added support for LLM-based rule generation.

### v2.0

- Built a modular zero-trust framework for agent security.
- Added compatibility for [OpenClaw](https://github.com/openclaw/openclaw) and JS client integrations.
