"use strict";

module.exports = {
  ...require("./registry"),
  ...require("./local_runner"),
  ...require("./registry_proxy"),
  ...require("./remote_runner"),
};
