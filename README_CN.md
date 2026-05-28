# 🛡️ AgentGuard

<p align="center">
  <a href="https://whitzardagent.github.io/AgentGuard/zh">
    <img src="https://img.shields.io/badge/%E6%96%87%E6%A1%A3-Docs-0ea5e9?style=for-the-badge&logo=gitbook&logoColor=white" alt="文档" />
  </a>
  <a href="https://github.com/WhitzardAgent/AgentGuard/releases">
    <img src="https://img.shields.io/badge/%E5%8F%91%E5%B8%83-v0.1-111827?style=for-the-badge&logo=github&logoColor=white" alt="发布 v1.0" />
  </a>
  <a href="./LICENSE">
    <img src="https://img.shields.io/badge/%E8%AE%B8%E5%8F%AF%E8%AF%81-MIT-16a34a?style=for-the-badge&logo=open-source-initiative&logoColor=white" alt="许可证" />
  </a>
</p>

<p align="center">
  <a href="./README.md">English</a> |
  <strong>简体中文</strong>
</p>

<p align="center">
  <strong>AgentGuard: 面向基于 LLM 的工具使用智能体的基于属性的访问控制框架</strong>
</p>

<p align="center">
  通过声明式策略、可追溯决策与人工审核，为高风险工具调用提供安全控制。
</p>

<table align="center" width="100%" cellspacing="0" cellpadding="0">
  <tr>
    <td align="center" width="30%" style="padding: 20px 18px; border: 1px solid #e5e7eb; border-radius: 18px; background: #ffffff;">
      <div style="font-size: 28px; line-height: 1; margin-bottom: 10px;">🧩</div>
      <small><strong>无&#8288;缝&#8288;集&#8288;成</strong></small>
    </td>
    <td align="center" width="30%" style="padding: 20px 18px; border: 1px solid #e5e7eb; border-radius: 18px; background: #ffffff;">
      <div style="font-size: 28px; line-height: 1; margin-bottom: 10px;">🛡️</div>
      <small><strong>多&#8288;风&#8288;险&#8288;覆&#8288;盖</strong></small>
    </td>
    <td align="center" width="40%" style="padding: 20px 18px; border: 1px solid #e5e7eb; border-radius: 18px; background: #ffffff;">
      <div style="font-size: 28px; line-height: 1; margin-bottom: 10px;">👁️</div>
      <small><strong>可&#8288;视&#8288;化&#8288;规&#8288;则&#8288;配&#8288;置&#8288;与&#8288;审&#8288;计</strong></small>
    </td>
  </tr>
</table>


> [!IMPORTANT]
> 本项目仍处于活跃开发阶段，可能包含尚未发现的缺陷。欢迎通过 Issue 和 PR 提交反馈与贡献。

AgentGuard 是一个面向智能体工具调用的基于属性的访问控制框架，它作用于大模型规划引擎与工具之间。在每一次工具调用真正执行之前，以及工具执行结束之后，AgentGuard 会依据声明式策略评估智能体行为风险，判断当前智能体的行为是否需要强制阻断、人工审核等。

![AgentGuard 设计定位](./docs/figs/positioning.png)

AgentGuard 可以集成到现有的智能体框架中，无需修改底层的执行逻辑。目前，它支持 LangChain、AutoGen 和 OpenAI Agents SDK 的集成，并且我们正在持续扩大对更多智能体生态系统和框架的支持。

## ✨ 功能特点

### 1. 丰富的策略表达能力

AgentGuard 的策略不是把风险判断写死在业务代码中，而是通过独立的 DSL 描述“什么条件下允许、拒绝或转入审核”。策略可以同时引用智能体身份、工具元数据、工具参数、目标地址、会话历史和调用链上下文，适合表达智能体工具调用中常见的安全边界。

#### 算术与逻辑表达式语法

策略条件支持数值比较、集合判断、正则匹配、字符串包含以及 `AND` / `OR` / `NOT` 组合。例如，可以用 `principal.trust_level < 2` 区分低信任智能体，用 `tool.recipient_domain NOT IN allowlist.email` 限制外发目标，也可以通过 `tool.cmd MATCHES ...` 识别危险命令，而上述表达式都可以通过 `AND` / `OR` / `NOT` 层层组合。

#### 跨工具调用的策略表达

AgentGuard 既可以判断单次工具调用，也可以判断跨步骤风险。通过 `TRACE` 和会话历史函数，策略能够表达“读取数据库后发送邮件”、“读取敏感文件后上传到外部 HTTP 端点”、“外部输入最终流入 Shell 执行”等链式行为，而不只依赖当前工具参数。

#### 工具执行的多阶段介入

策略可以作用在工具执行前的 `requested` 阶段，也可以作用在工具完成后的 `completed` 阶段或失败时的 `failed` 阶段。执行前适合做阻断和审批；执行后则可用于记录结果、基于 `tool.result` 触发后续审计或规则判断。

#### 多样化的策略决策

规则命中后可以返回 `ALLOW`、`DENY`、`HUMAN_CHECK` 或 `LLM_CHECK`。这使策略不必只有“放行/拒绝”两种结果：明确危险的操作可以直接拒绝，风险不确定的操作可以交给人工或 LLM 审查。

#### 主体与客体标签

策略可以基于智能体（主体）和工具（客体）属性做差异化控制。智能体侧可声明 `agent_id`、`session_id`、`role`、`trust_level`、`scope` 等身份信息；工具侧可声明 `boundary`、`sensitivity`、`integrity`、`tags` 等静态标签。这样可以直接写出“低信任智能体不能调用特权边界工具”、“高敏感工具结果不能流向外部边界”这类策略。同时用户也可以根据自己需要定义新的标签。

### 2. 无缝集成现有智能体框架

AgentGuard 位于大模型规划引擎与工具之间，不介入智能体的规划、推理及任务编排逻辑。AgentGuard 为多种主流智能体框架提供了 Adapter，用户无需改动框架内部代码，也不用对现有智能体进行大规模重构，仅需极少量代码即可通过 Adapter 快速完成集成。对于暂未支持的智能体框架，AgentGuard 也提供了方便的开发接口让用户自定义 Adapter。

目前我们支持的智能体框架有：
- [LangChain](https://github.com/langchain-ai/langchain)
- [AutoGen](https://github.com/microsoft/autogen)
- [OpenAI Agents SDK](https://github.com/openai/openai-agents-python)

### 3. 可视化策略配置与行为审计

AgentGuard 提供前端控制台来管理智能体。通过可视化页面，用户可以采用交互式的方式配置策略，无需手写 DSL 代码；同时策略配置界面广泛采用下拉框等选择性交互元素，极大降低了用户的策略配置负担。

运行时页面会展示智能体的健康状态、近期流量、待审批请求和审计记录。对于触发策略的工具调用，可以查看命中的规则、风险分数、最终决策以及原始事件/决策 JSON，便于定位为什么某次调用被拒绝或转入审核。

### 4. 集群管理

AgentGuard 采用集中式中控架构，实现对分布式智能体进程的统一治理。智能体可部署于网络中的多个节点，通过中控服务即可完成策略的集中配置与运行时状态的实时监控。这一架构特别适合需要统一管理众多智能体资产的组织场景。

## 🚀 快速开始

### 1. 编写访问控制策略并安装中控服务

> 你需要先安装 Docker

选择一台主机作为中控服务器，然后执行以下命令下载 AgentGuard：

```bash
git clone https://github.com/WhitzardAgent/AgentGuard.git
cd AgentGuard
```

编写一套访问控制策略：
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

该策略针对两个智能体工具：`retrieve_doc` 和 `send_email_to`，它们分别用于检索特定 id 的文档，以及将文档内容发送到指定的邮箱地址。这项策略描述了这么一个规则：信任级别小于 2 的智能体在执行任务时，只能将 id 为 0 的机密文件发送给 `admin@example.com` 邮箱，发送到其他地址一律不允许。

> AgentGuard 也提供了可视化策略配置方式，并支持策略的动态热更新，详情请参考 [这里](https://whitzardagent.github.io/AgentGuard/zh/policies/quick_config.html)。

接下来配置中控服务的环境变量：

> 若使用默认配置，此步骤可省略。

```bash
cp .env.example .env
vi .env
```

启动中控服务：
```bash
./scripts/start.sh -d
```

中控服务监听在：`38080` 端口
UI 界面监听在：`8080` 端口

你可以通过访问 `http://localhost:8080` 来查看 UI 界面。

### 2. 智能体端的设置

切换到智能体端的主机，执行以下命令：

```bash
git clone https://github.com/WhitzardAgent/AgentGuard.git
cd AgentGuard
pip install -e .
```

以 LangChain 为例，准备一份智能体代码：

> 你需要先安装包依赖：
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

标 🚩 符号的地方是 AgentGuard 客户端插入智能体代码的位置，另外请注意在代码中将 LLM API 密钥和中控服务器的地址改为你实际部署对应的值。

### 3. 运行智能体

执行刚刚准备的 LangChain 智能体代码：

```bash
python <LANGCHAIN_AGENT_FILE>
```

智能体执行了两项不同的任务，第一次是将 id 为 0 的文档（模拟机密文件）发送给管理员邮箱，这是访问控制策略允许的操作；第二次是将 id 为 0 的文档发送给其他用户邮箱，这是访问控制策略不允许的操作。

预期行为是，AgentGuard 会允许第一次智能体执行，拒绝第二次智能体执行。

预期输出如下：
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

目前 AgentGuard 通过直接抛出异常来硬阻断智能体执行，后续版本会采用软阻断机制，在不阻断智能体进程的前提下，给大模型返回一个错误信息，提示智能体当前任务被拒绝。

### 4. 可视化管理智能体运行状态

您可以通过 UI 界面查看智能体的运行状态，审计策略执行日志。

UI 界面还支持策略可视化配置和动态热更新。

关于 AgentGuard 部署的其他细节，请参考[项目文档](https://whitzardagent.github.io/AgentGuard/zh)。

## 🎬 演示视频

https://github.com/user-attachments/assets/5e226203-910a-42cb-aeca-7a43ebcf51b1

## 🏆 相比于现有框架的能力优势

![相比于现有框架的能力优势](./docs/figs/comparison_cn.png)

## 🏗️ 架构

下图展示了 AgentGuard 的高层架构。

<p align="center">
  <img src="./docs/figs/overview.png" alt="AgentGuard 设计架构图" width="50%" />
</p>

- **客户端**：通过极少量代码修改，客户端可集成进智能体框架中。客户端会监控每一次工具调用，将相关上下文信息转发至服务器，并执行服务器的策略决策。
- **服务器**：服务器接收来自客户端的信息，对智能体动作进行策略评估，产生策略决策，下发给客户端；同时服务器能对智能体做状态监控，方便管理员审计。

## 👥 贡献者

<table>
  <tr>
    <th align="left">贡献者</th>
    <th align="left">身份</th>
  </tr>
  <tr>
    <td><a href="https://djrrr.github.io/" target="_blank" rel="noreferrer">戴嘉润</a></td>
    <td>复旦大学副研究员</td>
  </tr>
  <tr>
    <td>罗嘉骐</td>
    <td>复旦大学博士生</td>
  </tr>
  <tr>
    <td>彭松洋</td>
    <td>复旦大学硕士生</td>
  </tr>
  <tr>
    <td>陈知乐</td>
    <td>复旦大学硕士生</td>
  </tr>
  <tr>
    <td><a href="https://zhxshen.github.io/" target="_blank" rel="noreferrer">申卓祥</a></td>
    <td>复旦大学博士生</td>
  </tr>
  <tr>
    <td><a href="https://ravensanstete.github.io/" target="_blank" rel="noreferrer">潘旭东</a></td>
    <td>复旦大学副研究员</td>
  </tr>
  <tr>
    <td><a href="https://ghong.site/" target="_blank" rel="noreferrer">洪赓</a></td>
    <td>复旦大学助理研究员</td>
  </tr>
</table>

排名不分先后，感谢所有为 AgentGuard 贡献过想法、代码和反馈的朋友。

## 🎯 未来计划

- 支持更多主流的智能体框架
- 支持更多编程语言的智能体系统
- 启用多智能体场景的保护
- 添加对 LLM 输入输出的监控
- 添加更丰富的策略执行动作
- 提供策略自动推荐的能力

## 📚 引用

如果你在研究工作中使用了 AgentGuard，请引用：

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

## 📜 许可证

本项目基于 [MIT License](./LICENSE) 开源。
