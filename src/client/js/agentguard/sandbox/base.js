"use strict";

class BaseSandbox {
  execute() {
    throw new Error("execute() must be implemented");
  }
}

module.exports = {
  BaseSandbox,
};
