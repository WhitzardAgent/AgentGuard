"use strict";

const { Trace } = require("../audit/trace");

class Session {
  constructor({ context }) {
    this.context = context;
    this.trace = new Trace({ session_id: context.session_id });
    this.tool_call_count = 0;
  }

  inc_tool_call() {
    this.tool_call_count += 1;
  }
}

module.exports = {
  Session,
};
