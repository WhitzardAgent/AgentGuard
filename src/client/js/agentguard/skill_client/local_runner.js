"use strict";

class LocalSkillRunner {
  constructor(registry = {}) {
    this.registry = registry;
  }

  async run(skill_name, input_data = {}) {
    const skill = this.registry[skill_name];
    if (typeof skill !== "function") {
      throw new Error(`skill not found: ${skill_name}`);
    }
    return await skill(input_data);
  }
}

module.exports = {
  LocalSkillRunner,
};
