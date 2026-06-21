"use strict";

const { parseFunctionCall } = require("./function_call_parser");

function parseToolCall(text) {
  const parsed = parseFunctionCall(text);
  if (!parsed || typeof parsed !== "object") {
    return null;
  }
  return {
    name: parsed.name || parsed.tool || parsed.tool_name || null,
    arguments: parsed.arguments || parsed.args || {},
  };
}

module.exports = {
  parseToolCall,
};
