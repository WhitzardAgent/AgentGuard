"use strict";

const TRANSFORM_HOOKS = [
  "on_before_remote_decision",
];

const NOTIFY_HOOKS = [
  "on_event",
  "on_llm_input",
  "on_llm_output",
  "on_tool_invoke",
  "on_tool_result",
  "on_after_remote_decision",
  "on_session_start",
  "on_session_end",
];

module.exports = {
  TRANSFORM_HOOKS,
  NOTIFY_HOOKS,
};
