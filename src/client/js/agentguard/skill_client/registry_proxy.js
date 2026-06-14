"use strict";

class SkillRegistryProxy {
  constructor({ remote = null, local = null } = {}) {
    this.remote = remote;
    this.local = local;
  }

  async run(skill_name, input_data = {}) {
    if (this.remote) {
      return this.remote.run(skill_name, input_data);
    }
    if (this.local) {
      return this.local.run(skill_name, input_data);
    }
    return { ok: false, error: "no skill runner configured" };
  }
}

module.exports = {
  SkillRegistryProxy,
};
