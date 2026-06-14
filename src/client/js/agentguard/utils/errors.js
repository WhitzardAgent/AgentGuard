"use strict";

class AgentGuardError extends Error {}
class AdapterError extends AgentGuardError {}
class RemoteGuardError extends AgentGuardError {}

module.exports = {
  AgentGuardError,
  AdapterError,
  RemoteGuardError,
};
