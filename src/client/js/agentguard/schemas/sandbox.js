"use strict";

class SandboxResult {
  constructor(data = {}) {
    this.success = Boolean(data.success);
    this.value = data.value;
    this.error = data.error ?? null;
    this.backend = data.backend || "unknown";
    this.stdout = data.stdout || "";
    this.stderr = data.stderr || "";
    this.duration_ms = data.duration_ms ?? 0;
    this.metadata = { ...(data.metadata || {}) };
  }

  static ok(value, extra = {}) {
    return new SandboxResult({ success: true, value, ...extra });
  }

  static fail(error, extra = {}) {
    return new SandboxResult({ success: false, error, ...extra });
  }
}

module.exports = {
  SandboxResult,
};
