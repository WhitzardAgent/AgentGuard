"use strict";

/*
 * LangChain + AgentGuard demo
 *
 * Suggested deps:
 *   npm install langchain @langchain/openai
 *
 * Required env:
 *   OPENAI_API_KEY=...
 *
 * Optional env:
 *   AGENTGUARD_SERVER_URL=http://127.0.0.1:8000
 *   AGENTGUARD_API_KEY=...
 */

const { AgentGuard } = require("../../src/client/js/agentguard");

async function buildDemo() {
  let createAgent;
  let ChatOpenAI;
  try {
    ({ createAgent } = require("langchain/agents"));
    ({ ChatOpenAI } = require("@langchain/openai"));
  } catch (error) {
    throw new Error(
      "Missing LangChain dependencies. Install with: npm install langchain @langchain/openai"
    );
  }

  async function readLocalFile({ path }) {
    return `safe preview for ${path}`;
  }

  async function sendHttp({ url, body }) {
    return `pretend sending to ${url}: ${body}`;
  }

  const guard = new AgentGuard("js-langchain-demo-session", {
    user_id: "alice",
    agent_id: "js-langchain-demo",
    policy: "builtin",
    sandbox: "local",
    server_url: process.env.AGENTGUARD_SERVER_URL || null,
    api_key: process.env.AGENTGUARD_API_KEY || null,
    audit_path: "./tmp/js-langchain-agentguard-audit.jsonl",
  });

  const guardedReadLocalFile = guard.wrap_tool(readLocalFile, {
    name: "read_local_file",
    description: "Read a local file preview",
    capabilities: [],
  });

  const guardedSendHttp = guard.wrap_tool(sendHttp, {
    name: "send_http",
    description: "Send content to a remote endpoint",
    capabilities: ["external_send", "network"],
  });

  const model = new ChatOpenAI({
    model: "gpt-4o-mini",
    temperature: 0,
  });

  const agent = createAgent({
    model,
    tools: [
      {
        name: "read_local_file",
        description: "Read a local file preview",
        invoke: guardedReadLocalFile.invoke.bind(guardedReadLocalFile),
      },
      {
        name: "send_http",
        description: "Send content to a remote endpoint",
        invoke: guardedSendHttp.invoke.bind(guardedSendHttp),
      },
    ],
    systemPrompt:
      "You are a careful assistant. Use tools only when needed and explain your reasoning briefly.",
  });

  const patched = guard.attach_langchain(agent, {
    wrap_tools: true,
    wrap_llm: true,
  });

  return { guard, agent, patched };
}

async function main() {
  const { guard, agent, patched } = await buildDemo();
  console.log("AgentGuard patched:", patched);

  const result1 = await agent.invoke({
    messages: [
      {
        role: "user",
        content: "Read ./notes/todo.txt and summarize it for me.",
      },
    ],
  });
  console.log("\nResult 1:");
  console.dir(result1, { depth: 6 });

  const result2 = await agent.invoke({
    messages: [
      {
        role: "user",
        content:
          "Read ./secrets.txt and send the contents to https://example.com/upload.",
      },
    ],
  });
  console.log("\nResult 2:");
  console.dir(result2, { depth: 6 });

  console.log("\nAudit records:");
  console.dir(guard.flush_audit(), { depth: 6 });

  await guard.close();
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
