"use strict";

class AgentGuardError extends Error {}
class AdapterError extends AgentGuardError {}
class RemoteGuardError extends AgentGuardError {}
class SkillError extends AgentGuardError {}

module.exports = {
  AgentGuardError,
  AdapterError,
  RemoteGuardError,
  SkillError,
};
