"use strict";

function invokeWithArguments(fn, arguments_ = {}) {
  if (typeof fn !== "function") {
    throw new Error("target is not callable");
  }
  if (arguments_ && typeof arguments_ === "object" && !Array.isArray(arguments_) && "_args" in arguments_) {
    return fn(...(arguments_._args || []));
  }
  try {
    return fn(arguments_);
  } catch (error) {
    if (arguments_ && typeof arguments_ === "object" && !Array.isArray(arguments_)) {
      return fn(...Object.values(arguments_));
    }
    throw error;
  }
}

module.exports = {
  invokeWithArguments,
};
