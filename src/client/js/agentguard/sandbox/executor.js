"use strict";

const { BaseSandbox } = require("./base");
const { LocalPermissionSandbox } = require("./local");
const { NoopSandbox } = require("./noop");
const { PermissionProfile } = require("./profiles");
const { SubprocessSandbox } = require("./subprocess");

const BACKENDS = {
  noop: NoopSandbox,
  local: LocalPermissionSandbox,
  subprocess: SubprocessSandbox,
};

function buildSandbox(backend = "local", profile = null) {
  if (backend instanceof BaseSandbox) {
    return backend;
  }
  const SandboxClass = BACKENDS[backend];
  if (!SandboxClass) {
    throw new Error(`unknown sandbox backend: ${backend}`);
  }
  if (SandboxClass === NoopSandbox) {
    return new SandboxClass();
  }
  return new SandboxClass(profile || PermissionProfile.restricted());
}

class SandboxExecutor {
  constructor(backend = "local", profile = null) {
    this.backend = buildSandbox(backend, profile);
  }

  run(fn, arguments_ = {}, options = {}) {
    return this.backend.execute(fn, arguments_, options);
  }
}

module.exports = {
  buildSandbox,
  SandboxExecutor,
};
