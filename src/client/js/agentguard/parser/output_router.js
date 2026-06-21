"use strict";

const { parseToolCall } = require("./tool_call_parser");

function routeOutput(text) {
  const toolCall = parseToolCall(text);
  if (toolCall && toolCall.name) {
    return { type: "tool_call", value: toolCall };
  }
  return { type: "text", value: text };
}

module.exports = {
  routeOutput,
};
