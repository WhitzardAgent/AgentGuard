"use strict";

const assert = require("node:assert/strict");
const test = require("node:test");

const { _private } = require("./index");

test("applyModifyToResponsesRequest rewrites request input and preserves other fields", () => {
  const result = _private.applyModifyToResponsesRequest(
    {
      model: "gpt-4.1",
      stream: false,
      input: [{ role: "user", content: "original prompt" }],
    },
    JSON.stringify({
      input: [
        { role: "system", content: "You are safe." },
        { role: "user", content: "rewritten prompt" },
      ],
      temperature: 0.2,
    })
  );

  assert.equal(result.model, "gpt-4.1");
  assert.equal(result.temperature, 0.2);
  assert.deepEqual(result.input, [
    { role: "system", content: "You are safe." },
    { role: "user", content: "rewritten prompt" },
  ]);
});

test("applyModifyToResponsesResult rewrites output text and output items", () => {
  const result = _private.applyModifyToResponsesResult(
    {
      output_text: "raw answer",
      output: [
        {
          type: "message",
          content: [{ type: "output_text", text: "raw answer", annotations: [] }],
        },
      ],
    },
    JSON.stringify({ output: "safe answer" })
  );

  assert.equal(result.output_text, "safe answer");
  assert.equal(result.output[0].content[0].text, "safe answer");
});

test("applyModifyToRunNodeArgs rewrites node parameters from processed content", () => {
  const result = _private.applyModifyToRunNodeArgs(
    {
      node: {
        parameters: {
          responses: {
            values: [{ role: "user", content: "original" }],
          },
          temperature: 0.8,
        },
      },
    },
    JSON.stringify({
      input: [{ role: "user", content: "rewritten" }],
      temperature: 0.1,
    })
  );

  assert.equal(result.node.parameters.temperature, 0.1);
  assert.equal(result.node.parameters.responses.values[0].content, "rewritten");
});

test("applyModifyToRunNodeResult rewrites the first run node payload", () => {
  const result = _private.applyModifyToRunNodeResult(
    {
      data: [[{ json: { output: "raw result" } }]],
      hints: [],
    },
    JSON.stringify({ output: "safe result" })
  );

  assert.equal(result.data[0][0].json.output, "safe result");
});
