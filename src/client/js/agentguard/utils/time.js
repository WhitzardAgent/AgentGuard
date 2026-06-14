"use strict";

function nowTs() {
  return Date.now() / 1000;
}

function isoNow() {
  return new Date().toISOString();
}

module.exports = {
  nowTs,
  isoNow,
};
