# LangChain

## 导入方法
使用 `Guard.attach_langchain()` 这个 Adapter 方法可以自动将 LangChain 智能体实例与 AgentGuard 关联起来，你不再需要对 LangChain SDK 原本的代码做任何修改。

我们的 LangChain Adapter 关联的对象是 `langchain.agents.create_agent()` 的返回值。在 LangChain v1 中，这个对象通常底层也是 `langgraph.graph.state.CompiledGraph`；但如果你接入的是原生 LangGraph graph，请改用 `Guard.attach_langgraph()`。

```python
agent = create_agent(...)

guard = Guard(
    remote_url="http://<Control Server IP>:38080",
    ticket="<AgentGuard User Ticket>",
)
guard.start(...)
guard.attach_langchain(agent)   # Attach the guard to the LangChain agent
```

远程接入 LangChain 时，`ticket` 是必填项。用户需要先登录 AgentGuard 前端，在 User Centre 的 Current User 区域点击 `Generate Ticket`，复制弹窗中的一次性 ticket，然后传给 LangChain 程序。ticket 是短时有效且一次性消费的，不建议写入环境变量或配置文件。

## 完整代码示例
下面代码展示了一个导入 AgentGuard 访问控制客户端后的完整代码示例，标 🚩 符号的地方是客户端的插入位置：
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
    print(f"Output: {result['messages'][-1].content}")
    print("===================================\n")

if __name__ == "__main__":
    agent = build_agent()

    # 🚩 Load the guard client
    guard = Guard(
        remote_url="http://<Control Server IP>:38080",         # Replace with your control server IP and port
        ticket="<AgentGuard User Ticket>",                     # Generate this in User Centre before each run
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

### 运行环境说明
以上代码的运行有如下包依赖：
```bash
pip install langchain==1.2.18
pip install langchain-openai==1.2.1
```

如果运行仓库里的示例脚本，需要把前端生成的 ticket 作为命令行参数传入：

```bash
python examples/test_langchain_emailcase.py --ticket agt_xxx
```
