"use strict";

class EventBus {
  constructor() {
    this.listeners = new Set();
  }

  subscribe(listener) {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  publish(event) {
    for (const listener of this.listeners) {
      listener(event);
    }
  }
}

module.exports = {
  EventBus,
};
