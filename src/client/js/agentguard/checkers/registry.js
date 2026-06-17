"use strict";

const CHECKERS = new Map();
const DESCRIPTIONS = new Map();

let DISCOVERED = false;

function register(name, description) {
  if (!name) {
    throw new Error("checker registration name must not be empty");
  }
  return (CheckerClass) => {
    CheckerClass.prototype.name = name;
    CheckerClass.prototype.description = description;
    CHECKERS.set(name, CheckerClass);
    DESCRIPTIONS.set(name, description);
    return CheckerClass;
  };
}

function getCheckerClass(name) {
  discoverCheckers();
  return CHECKERS.get(name) || null;
}

function checkerDescriptions() {
  discoverCheckers();
  return Object.fromEntries(DESCRIPTIONS.entries());
}

function registeredCheckers() {
  discoverCheckers();
  return Object.fromEntries(CHECKERS.entries());
}

function discoverCheckers() {
  if (DISCOVERED) {
    return;
  }
  DISCOVERED = true;
  require("./llm_before/llm_input");
  require("./llm_after/llm_output");
  require("./llm_after/llm_thought");
  require("./llm_after/final_response");
  require("./tool_before/tool_invoke");
  require("./tool_after/tool_result");
}

module.exports = {
  register,
  getCheckerClass,
  checkerDescriptions,
  registeredCheckers,
  discoverCheckers,
};
