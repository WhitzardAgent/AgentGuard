"use strict";

class BaseInterceptor {
  before(event) {
    return event;
  }

  after(event) {
    return event;
  }
}

module.exports = {
  BaseInterceptor,
};
