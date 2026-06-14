"use strict";

const fs = require("fs");
const path = require("path");
const { safeDumps } = require("../utils/json");

class AuditLogger {
  constructor(filePath = null) {
    this.path = filePath ? path.resolve(filePath) : null;
    this.buffer = [];
    if (this.path) {
      fs.mkdirSync(path.dirname(this.path), { recursive: true });
    }
  }

  write(record) {
    this.buffer.push(record);
    if (this.path) {
      fs.appendFileSync(this.path, `${safeDumps(record)}\n`, "utf8");
    }
  }

  records() {
    return [...this.buffer];
  }

  flush() {
    return this.records();
  }

  clear() {
    this.buffer = [];
  }
}

module.exports = {
  AuditLogger,
};
