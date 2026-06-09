"""Skill registry proxy: route a skill to a local or remote runner."""
from __future__ import annotations

from typing import Any

from agentguard.skill_client.local_runner import LocalSkillRunner
from agentguard.skill_client.remote_runner import RemoteSkillRunner
from agentguard.utils.errors import SkillError


class SkillRegistryProxy:
    def __init__(
        self,
        local: LocalSkillRunner | None = None,
        remote: RemoteSkillRunner | None = None,
        prefer: str = "local",
    ) -> None:
        self.local = local or LocalSkillRunner()
        self.remote = remote
        self.prefer = prefer

    def run(self, skill_name: str, input_data: dict[str, Any]) -> dict[str, Any]:
        if self.prefer == "remote" and self.remote and self.remote.enabled:
            return self.remote.run(skill_name, input_data)
        try:
            return self.local.run(skill_name, input_data)
        except SkillError:
            if self.remote and self.remote.enabled:
                return self.remote.run(skill_name, input_data)
            raise

    def list_skills(self) -> list[str]:
        try:
            return self.local.list_skills()
        except SkillError:
            return []
