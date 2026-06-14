"use strict";

class PermissionProfile {
  constructor(data = {}) {
    this.allowed_file_roots = [...(data.allowed_file_roots || [])];
    this.denied_file_roots = [...(data.denied_file_roots || [])];
    this.allowed_domains = [...(data.allowed_domains || [])];
    this.denied_domains = [...(data.denied_domains || [])];
    this.allowed_env_vars = [...(data.allowed_env_vars || [])];
    this.allow_subprocess = Boolean(data.allow_subprocess);
    this.allow_network = Boolean(data.allow_network);
    this.allow_write = Boolean(data.allow_write);
    this.timeout_s = data.timeout_s ?? 10.0;
    this.memory_limit_mb = data.memory_limit_mb ?? null;
  }

  static permissive() {
    return new PermissionProfile({
      allow_subprocess: true,
      allow_network: true,
      allow_write: true,
      timeout_s: 30.0,
    });
  }

  static restricted() {
    return new PermissionProfile({
      allow_subprocess: false,
      allow_network: false,
      allow_write: false,
      timeout_s: 5.0,
    });
  }
}

module.exports = {
  PermissionProfile,
};
