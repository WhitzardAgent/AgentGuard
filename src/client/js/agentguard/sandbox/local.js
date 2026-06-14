"use strict";

const { BaseSandbox } = require("./base");
const { PermissionProfile } = require("./profiles");
const { checkPermissions } = require("./permissions");
const { SandboxResult } = require("../schemas/sandbox");
const { invokeWithArguments } = require("../utils/invoke");

class LocalPermissionSandbox extends BaseSandbox {
  constructor(profile = null) {
    super();
    this.name = "local";
    this.profile = profile || PermissionProfile.restricted();
  }

  execute(fn, arguments_ = {}, options = {}) {
    const check = checkPermissions(this.profile, options.capabilities || [], arguments_);
    if (!check.allowed) {
      return SandboxResult.fail(`permission denied: ${check.reason}`, {
        backend: this.name,
        metadata: { capabilities: options.capabilities || [] },
      });
    }
    const started = Date.now();
    try {
      const value = invokeWithArguments(fn, arguments_);
      return SandboxResult.ok(value, {
        backend: this.name,
        duration_ms: Date.now() - started,
      });
    } catch (error) {
      return SandboxResult.fail(String(error.message || error), {
        backend: this.name,
        duration_ms: Date.now() - started,
      });
    }
  }
}

module.exports = {
  LocalPermissionSandbox,
};
