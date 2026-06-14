"use strict";

const path = require("path");
const { URL } = require("url");
const {
  CAP_EXTERNAL_SEND,
  CAP_NETWORK,
  CAP_SHELL,
  CAP_WRITE_FILE,
} = require("../tools/capability");

class PermissionCheck {
  constructor(allowed, reason = "") {
    this.allowed = allowed;
    this.reason = reason;
  }
}

function pathUnder(targetPath, roots) {
  const absolute = path.resolve(targetPath);
  return roots.some((root) => {
    const absoluteRoot = path.resolve(root);
    return absolute === absoluteRoot || absolute.startsWith(`${absoluteRoot}${path.sep}`);
  });
}

function parseHost(value) {
  try {
    return new URL(value).hostname;
  } catch (_) {
    return value;
  }
}

function checkPermissions(profile, capabilities = [], arguments_ = {}) {
  const caps = new Set(capabilities);
  if (caps.has(CAP_SHELL) && !profile.allow_subprocess) {
    return new PermissionCheck(false, "subprocess/shell not permitted");
  }
  if ((caps.has(CAP_NETWORK) || caps.has(CAP_EXTERNAL_SEND)) && !profile.allow_network) {
    return new PermissionCheck(false, "network access not permitted");
  }
  if (caps.has(CAP_WRITE_FILE) && !profile.allow_write) {
    return new PermissionCheck(false, "file write not permitted");
  }
  for (const key of ["path", "file", "filename", "target"]) {
    const value = arguments_[key];
    if (typeof value === "string" && value) {
      if (profile.denied_file_roots.length && pathUnder(value, profile.denied_file_roots)) {
        return new PermissionCheck(false, `path under denied root: ${key}`);
      }
      if (profile.allowed_file_roots.length && !pathUnder(value, profile.allowed_file_roots)) {
        return new PermissionCheck(false, `path outside allowed roots: ${key}`);
      }
    }
  }
  for (const key of ["url", "endpoint", "host", "to"]) {
    const value = arguments_[key];
    if (typeof value === "string" && value && (value.includes("://") || value.includes("."))) {
      const host = parseHost(value);
      if (profile.denied_domains.length && profile.denied_domains.some((domain) => host.includes(domain))) {
        return new PermissionCheck(false, `denied domain: ${host}`);
      }
      if (profile.allowed_domains.length && !profile.allowed_domains.some((domain) => host.includes(domain))) {
        return new PermissionCheck(false, `domain not in allowlist: ${host}`);
      }
    }
  }
  return new PermissionCheck(true, "permitted");
}

module.exports = {
  PermissionCheck,
  checkPermissions,
};
