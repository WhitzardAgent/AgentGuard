"use strict";

const { BaseSandbox } = require("./base");
const { SandboxResult } = require("../schemas/sandbox");
const { invokeWithArguments } = require("../utils/invoke");

class NoopSandbox extends BaseSandbox {
  execute(fn, arguments_ = {}) {
    try {
      return SandboxResult.ok(invokeWithArguments(fn, arguments_), { backend: "noop" });
    } catch (error) {
      return SandboxResult.fail(String(error), { backend: "noop" });
    }
  }
}

module.exports = {
  NoopSandbox,
};
