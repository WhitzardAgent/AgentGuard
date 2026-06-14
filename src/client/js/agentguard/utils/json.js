"use strict";

function safeDumps(value, space = 0) {
  return JSON.stringify(
    value,
    (_, current) => {
      if (typeof current === "bigint") {
        return current.toString();
      }
      if (current instanceof Error) {
        return {
          name: current.name,
          message: current.message,
          stack: current.stack,
        };
      }
      return current;
    },
    space
  );
}

function safeLoads(text, fallback = null) {
  try {
    return JSON.parse(text);
  } catch (_) {
    return fallback;
  }
}

module.exports = {
  safeDumps,
  safeLoads,
};
