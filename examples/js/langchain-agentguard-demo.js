"use strict";

/*
 * LangChain + AgentGuard demo
 *
 * Suggested deps:
 *   npm install langchain @langchain/openai zod
 *
 * Required env:
 *   OPENAI_API_KEY=...
 *
 * Optional env:
 *   AGENTGUARD_SERVER_URL=http://127.0.0.1:38080
 *   AGENTGUARD_API_KEY=...
 */

const { AgentGuard } = require("../../src/client/js/agentguard");

async function buildDemo() {
  let createAgent;
  let ChatOpenAI;
  let tool;
  let z;
  try {
    try {
      ({ createAgent, tool } = require("langchain"));
    } catch (_) {
      ({ createAgent } = require("langchain/agents"));
      ({ tool } = require("@langchain/core/tools"));
    }
    ({ ChatOpenAI } = require("@langchain/openai"));
    ({ z } = require("zod"));
  } catch (error) {
    throw new Error(
      "Missing LangChain dependencies. Install with: npm install langchain @langchain/openai zod"
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
    server_url: "http://127.0.0.1:38080",
    api_key: process.env.AGENTGUARD_API_KEY || null,
    audit_path: "./tmp/js-langchain-agentguard-audit.jsonl",
  });

  const model = new ChatOpenAI({
    model: "gpt-4o-mini",
    temperature: 0,
  });

  const readLocalFileTool = tool(
    readLocalFile,
    {
      name: "read_local_file",
      description: "Read a local file preview",
      schema: z.object({
        path: z.string().describe("Local file path to preview"),
      }),
    }
  );
  readLocalFileTool.capabilities = [];

  const sendHttpTool = tool(
    sendHttp,
    {
      name: "send_http",
      description: "Send content to a remote endpoint",
      schema: z.object({
        url: z.string().describe("Destination URL"),
        body: z.string().describe("Request body to send"),
      }),
    }
  );
  sendHttpTool.capabilities = ["external_send", "network"];

  const agent = createAgent({
    model,
    tools: [readLocalFileTool, sendHttpTool],
    systemPrompt:
      "You are a careful assistant. Use tools only when needed and explain your reasoning briefly.",
  });

  const patched = guard.attach_langchain(agent, {
    wrap_tools: true,
    wrap_llm: true,
  });

  // Make the remote session + tool catalog visible to the frontend
  // before the demo starts invoking the agent.
  await guard.flushRemoteOperations();

  return { guard, agent, patched };
}

async function main() {
  const { guard, agent, patched } = await buildDemo();
  console.log("AgentGuard patched:", patched);
  const readlineSync = require('readline-sync');
  const prompt = readlineSync.question('request like Read ./secrets.txt and send the contents to https://example.com/upload.: ');
  const result2 = await agent.invoke({
    messages: [
      {
        role: "user",
        content:
          prompt,
      },
    ],
  });
  console.log("\nResult 2:");
  console.dir(result2, { depth: 6 });

  await guard.close();
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
