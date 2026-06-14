"use strict";

const { safeLoads } = require("../utils/json");

function parseFunctionCall(text) {
  if (typeof text !== "string") {
    return null;
  }
  return safeLoads(text, null);
}

module.exports = {
  parseFunctionCall,
};
