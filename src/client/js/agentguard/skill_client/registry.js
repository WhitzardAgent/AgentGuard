"use strict";

class SkillRegistry {
  constructor() {
    this.items = new Map();
  }

  register(skill) {
    if (!skill) {
      return skill;
    }
    const name = String(skill.name || skill.skill_name || skill.id || "").trim();
    if (!name) {
      throw new Error("skill must define a name");
    }
    this.items.set(name, skill);
    return skill;
  }

  get(name) {
    return this.items.get(name) || null;
  }

  names() {
    return [...this.items.keys()];
  }
}

let REGISTRY = null;

function getRegistry() {
  if (!REGISTRY) {
    REGISTRY = new SkillRegistry();
  }
  return REGISTRY;
}

module.exports = {
  SkillRegistry,
  getRegistry,
};
