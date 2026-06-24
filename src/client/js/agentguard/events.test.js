"use strict";

const assert = require("node:assert/strict");
const test = require("node:test");

const { RuntimeContext } = require("./schemas/context");
const ev = require("./schemas/events");

test("LLMOutput keeps thought and final_output fields", () => {
  const ctx = new RuntimeContext({ session_id: "sess-1" });
  const event = ev.llm_output(ctx, {
    thought: "internal chain",
    final_output: "visible answer",
  });

  assert.equal(event.payload.output, "visible answer");
  assert.equal(event.payload.thought, "internal chain");
  assert.equal(event.payload.final_output, "visible answer");

  const restored = ev.RuntimeEvent.fromDict(event.toDict());
  assert.equal(restored.payload.output, "visible answer");
  assert.equal(restored.payload.thought, "internal chain");
  assert.equal(restored.payload.final_output, "visible answer");
});

test("LLMOutput aliases fill dedicated fields", () => {
  const ctx = new RuntimeContext({ session_id: "sess-2" });
  const thoughtEvent = ev.llm_thought(ctx, "internal chain");
  const finalEvent = ev.final_response(ctx, "visible answer");

  assert.equal(thoughtEvent.payload.output, "internal chain");
  assert.equal(thoughtEvent.payload.thought, "internal chain");
  assert.equal(thoughtEvent.payload.final_output, null);

  assert.equal(finalEvent.payload.output, "visible answer");
  assert.equal(finalEvent.payload.thought, null);
  assert.equal(finalEvent.payload.final_output, "visible answer");
});

test("LLMOutput preserves non-structured objects as output text", () => {
  const ctx = new RuntimeContext({ session_id: "sess-3" });
  const event = ev.llm_output(ctx, { tool_calls: [{ name: "search" }] });

  assert.match(event.payload.output, /tool_calls/);
  assert.equal(event.payload.thought, null);
  assert.equal(event.payload.final_output, null);
});
