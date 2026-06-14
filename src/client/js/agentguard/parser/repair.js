"use strict";

const { safeLoads } = require("../utils/json");

function repairJson(text) {
  return safeLoads(text, text);
}

module.exports = {
  repairJson,
};
