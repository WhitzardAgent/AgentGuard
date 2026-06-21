"use strict";

const { getRegistry } = require("./registry");
const { SkillError } = require("../utils/errors");

class LocalSkillRunner {
  constructor(registry = null) {
    this.registry = registry;
  }

  async run(skill_name, input_data = {}) {
    const registry = this.registry || getRegistry();
    const skill = registry.get ? registry.get(skill_name) : registry[skill_name];
    if (!skill) {
      throw new SkillError(`unknown skill: ${skill_name}`);
    }
    if (typeof skill === "function") {
      return await skill(input_data);
    }
    if (typeof skill.run === "function") {
      return await skill.run(input_data);
    }
    throw new SkillError(`skill is not runnable: ${skill_name}`);
  }

  list_skills() {
    const registry = this.registry || getRegistry();
    if (typeof registry.names === "function") {
      return registry.names();
    }
    return Object.keys(registry);
  }
}

module.exports = {
  LocalSkillRunner,
};
