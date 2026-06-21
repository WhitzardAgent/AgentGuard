"use strict";

class Lifecycle {
  constructor() {
    this.hooks = new Map();
  }

  register(name, fn) {
    const list = this.hooks.get(name) || [];
    list.push(fn);
    this.hooks.set(name, list);
  }

  dispatch(name, ...args) {
    let current;
    for (const fn of this.hooks.get(name) || []) {
      const result = fn(...args);
      if (result !== undefined) {
        current = result;
      }
    }
    return current;
  }

  notify(name, ...args) {
    this.dispatch(name, ...args);
  }
}

module.exports = {
  Lifecycle,
};
