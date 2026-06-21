"use strict";

class DegradePlan {
  constructor(data = {}) {
    this.degraded = Boolean(data.degraded);
    this.target_tool = data.target_tool || null;
    this.arguments = { ...(data.arguments || {}) };
    this.explanation = data.explanation || "";
    this.safe_error = data.safe_error || null;
  }
}

class ToolDegradeManager {
  plan(toolName, arguments_, reason = "") {
    return new DegradePlan({
      degraded: false,
      target_tool: null,
      arguments: arguments_,
      explanation: reason,
      safe_error: reason || `tool ${toolName} cannot be safely degraded`,
    });
  }
}

module.exports = {
  DegradePlan,
  ToolDegradeManager,
};
