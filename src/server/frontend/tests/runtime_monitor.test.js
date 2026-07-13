const test = require("node:test");
const assert = require("node:assert/strict");

function createElement() {
  return {
    innerHTML: "",
    textContent: "",
    className: "",
    disabled: false,
    dataset: {},
    children: [],
    classList: {
      add() {},
      remove() {},
    },
    appendChild(child) {
      this.children.push(child);
      return child;
    },
    addEventListener() {},
  };
}

function installRuntimeGlobals() {
  const elements = new Map();
  global.document = {
    getElementById(id) {
      if (!elements.has(id)) {
        elements.set(id, createElement());
      }
      return elements.get(id);
    },
    createElement() {
      return createElement();
    },
  };
  global.window = {
    AgentGuardApi: {
      buildQuery(params = {}) {
        const search = new URLSearchParams(params);
        const query = search.toString();
        return query ? `?${query}` : "";
      },
      async fetchJson(url) {
        if (url === "/api/health") {
          return { ok: true };
        }
        if (url.includes("/runtime/stats")) {
          return { total_requests: 0, deny_count: 0, deny_rate: 0, uptime_s: 0 };
        }
        return [];
      },
    },
    AgentGuardShell: {
      getState() {
        return { selectedAgentId: "agent-a", selectedAgentLabel: "agent-a" };
      },
      setPageContext() {},
    },
    AgentGuardI18n: {
      getLocale() {
        return "en-US";
      },
    },
    AgentGuardUI: {
      showToast() {},
    },
    AgentGuardUIHelpers: {
      actionTone() {
        return "";
      },
    },
    addEventListener() {},
    setInterval() {
      return 1;
    },
  };
  global.setInterval = global.window.setInterval;
}

test("runtime audit expansion formats each event type", () => {
  installRuntimeGlobals();
  delete require.cache[require.resolve("../static/pages/runtime/runtime.js")];
  require("../static/pages/runtime/runtime.js");

  const { auditExpansionContent } = global.window.AgentGuardRuntimeMonitor;

  assert.deepEqual(
    auditExpansionContent({
      runtimeState: {
        event_type: "llm_input",
        payload: { messages: [{ role: "user", content: "Summarize this file." }] },
      },
    }),
    { label: "LLM Input", body: "user: Summarize this file." },
  );

  assert.deepEqual(
    auditExpansionContent({
      runtimeState: {
        event_type: "llm_input",
        payload: {
          messages: [
            { role: "system", content: "You are helpful." },
            { role: "human", content: "Please retrieve document id=0." },
            {
              type: "ai",
              content: "",
              tool_calls: [{ name: "retrieve_doc", args: { id: 0 }, type: "tool_call" }],
            },
          ],
        },
      },
    }),
    { label: "LLM Input", body: 'ai: [toolCall retrieve_doc] {\n  "id": 0\n}' },
  );

  assert.deepEqual(
    auditExpansionContent({
      runtimeState: {
        event_type: "llm_input",
        payload: {
          messages: [
            { role: "human", content: "Please retrieve document id=0." },
            { role: "tool", name: "retrieve_doc", content: "DOC#0: This is a document." },
          ],
        },
      },
    }),
    { label: "LLM Input", body: "tool: [toolResult retrieve_doc] DOC#0: This is a document." },
  );

  assert.deepEqual(
    auditExpansionContent({
      runtimeState: {
        event_type: "llm_output",
        payload: { output: JSON.stringify({ data: { tool_calls: [{ name: "search" }] } }) },
      },
    }),
    { label: "LLM Output", body: "[Construct A Tool Invoke]" },
  );

  assert.deepEqual(
    auditExpansionContent({
      runtimeState: {
        event_type: "tool_invoke",
        arguments: { path: "/tmp/report.txt" },
        payload: {},
      },
    }),
    { label: "Tool Arguments", body: '{\n  "path": "/tmp/report.txt"\n}' },
  );

  assert.deepEqual(
    auditExpansionContent({
      runtimeState: {
        event_type: "tool_result",
        result: "done",
        payload: {},
      },
    }),
    { label: "Tool Result", body: "done" },
  );
});
