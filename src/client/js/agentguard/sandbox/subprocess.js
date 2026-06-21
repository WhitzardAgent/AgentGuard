"use strict";

const { BaseSandbox } = require("./base");
const { LocalPermissionSandbox } = require("./local");

class SubprocessSandbox extends BaseSandbox {
  constructor(profile = null, options = {}) {
    super();
    this.name = "subprocess";
    this.profile = profile;
    this.options = options;
    this.delegate = new LocalPermissionSandbox(profile);
  }

  execute(fn, arguments_ = {}, options = {}) {
    return this.delegate.execute(fn, arguments_, options);
  }
}

module.exports = {
  SubprocessSandbox,
};
