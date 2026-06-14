"use strict";

class LLMRequest {
  constructor(data = {}) {
    this.messages = [...(data.messages || [])];
    this.metadata = { ...(data.metadata || {}) };
  }
}

class LLMResponse {
  constructor(data = {}) {
    this.output = data.output;
    this.metadata = { ...(data.metadata || {}) };
  }
}

module.exports = {
  LLMRequest,
  LLMResponse,
};
