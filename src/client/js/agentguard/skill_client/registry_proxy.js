"use strict";

const { LocalSkillRunner } = require("./local_runner");
const { SkillError } = require("../utils/errors");

class SkillRegistryProxy {
  constructor({ remote = null, local = null, prefer = "local" } = {}) {
    this.remote = remote;
    this.local = local || new LocalSkillRunner();
    this.prefer = prefer;
  }

  async run(skill_name, input_data = {}) {
    if (this.prefer === "remote" && this.remote && this.remote.enabled) {
      return this.remote.run(skill_name, input_data);
    }
    try {
      return this.local.run(skill_name, input_data);
    } catch (error) {
      if (!(error instanceof SkillError)) {
        throw error;
      }
      if (this.remote && this.remote.enabled) {
        return this.remote.run(skill_name, input_data);
      }
      throw error;
    }
  }

  list_skills() {
    try {
      return this.local.list_skills();
    } catch (_) {
      return [];
    }
  }
}

module.exports = {
  SkillRegistryProxy,
};
