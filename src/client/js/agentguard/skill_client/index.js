"use strict";

module.exports = {
  ...require("./local_runner"),
  ...require("./registry_proxy"),
  ...require("./remote_runner"),
};
