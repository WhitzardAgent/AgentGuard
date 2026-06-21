"use strict";

class ToolCall {
  constructor(data = {}) {
    this.name = data.name || "";
    this.arguments = { ...(data.arguments || {}) };
  }

  toDict() {
    return {
      name: this.name,
      arguments: { ...this.arguments },
    };
  }
}

module.exports = {
  ToolCall,
};
