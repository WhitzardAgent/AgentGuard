"use strict";

const fs = require("fs");
const path = require("path");
const { builtinRules } = require("./builtin");
const { PolicyRule } = require("../schemas/policy");
const { safeLoads } = require("../utils/json");

function loadPolicy(filePath = null) {
  if (!filePath) {
    return builtinRules();
  }
  const absolutePath = path.resolve(filePath);
  const raw = fs.readFileSync(absolutePath, "utf8");
  const data = safeLoads(raw, {});
  const rules = Array.isArray(data) ? data : data.rules || [];
  return rules.map((rule) => PolicyRule.fromDict(rule));
}

module.exports = {
  loadPolicy,
};
