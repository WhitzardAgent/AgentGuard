"use strict";

const CHECKERS = new Map();
const DESCRIPTIONS = new Map();

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
  return CHECKERS.get(name) || null;
}

function checkerDescriptions() {
  return Object.fromEntries(DESCRIPTIONS.entries());
}

module.exports = {
  register,
  getCheckerClass,
  checkerDescriptions,
};
