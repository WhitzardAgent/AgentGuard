"""Static metadata describing a registered tool."""

from __future__ import annotations

from pydantic import BaseModel, Field

from agentguard.tools.capability import Capability, capabilities_for_sink


class ToolMetadata(BaseModel):
    name: str
    description: str = ""
    sink_type: str = "none"
    capabilities: list[Capability] = Field(default_factory=list)
    sensitivity: str = "low"      # low | moderate | high
    boundary: str = "internal"    # internal | external | privileged
    integrity: str = "trusted"    # trusted | unfiltered
    tags: list[str] = Field(default_factory=list)
    param_names: list[str] = Field(default_factory=list)

    @classmethod
    def build(
        cls,
        name: str,
        *,
        sink_type: str = "none",
        capabilities: list[str] | None = None,
        **kwargs: object,
    ) -> "ToolMetadata":
        caps = (
            [Capability(c) for c in capabilities]
            if capabilities
            else capabilities_for_sink(sink_type)
        )
        return cls(name=name, sink_type=sink_type, capabilities=caps, **kwargs)

    def capability_values(self) -> list[str]:
        return [c.value for c in self.capabilities]
