"use strict";

function formatTrajectoryWindow(window = []) {
  return window.map((item) => (item.toDict ? item.toDict() : item));
}

module.exports = {
  formatTrajectoryWindow,
};
