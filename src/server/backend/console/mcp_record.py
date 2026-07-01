"""Console-facing MCP records used by backend registration and detection."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from backend.preprocess.detectors.base import DetectionResult

McpDetectionResult = DetectionResult


@dataclass
class McpResource:
    """Full MCP resource submitted by an adapter."""

    descriptor: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_descriptor(cls, descriptor: dict[str, Any]) -> McpResource:
        return cls(descriptor=dict(descriptor or {}))

    def to_dict(self) -> dict[str, Any]:
        return dict(self.descriptor)


@dataclass
class McpRecord:
    """A registered MCP server scoped to one agent/session/user."""

    agent_id: str
    user_id: str | None
    session_id: str | None
    mcp_unique_id: str
    name: str
    description: str = ""
    source_framework: str = ""
    object_type: str = "mcp"
    transport: str = ""
    remote: bool = False
    root_path: str = ""
    entry_file: str = ""
    url: str = ""
    sha256: str = ""
    tool_count: int = 0
    file_count: int = 0
    total_size: int = 0
    extraction: dict[str, Any] = field(default_factory=dict)
    mcp_resource: McpResource = field(default_factory=McpResource)
    detect_result: DetectionResult | None = None

    @classmethod
    def from_descriptor(
        cls,
        *,
        agent_id: str,
        user_id: str | None,
        session_id: str | None,
        descriptor: dict[str, Any],
    ) -> McpRecord | None:
        item = dict(descriptor or {})
        name = str(item.get("name") or "").strip()
        if not name:
            return None
        mcp_unique_id = _mcp_unique_id(agent_id, item)
        return cls(
            agent_id=agent_id,
            user_id=user_id,
            session_id=session_id,
            mcp_unique_id=mcp_unique_id,
            name=name,
            description=str(item.get("description") or ""),
            source_framework=str(item.get("source_framework") or ""),
            object_type=str(item.get("object_type") or "mcp"),
            transport=str(item.get("transport") or ""),
            remote=bool(item.get("remote", False)),
            root_path=str(item.get("root_path") or ""),
            entry_file=str(item.get("entry_file") or ""),
            url=str(item.get("url") or ""),
            sha256=str(item.get("sha256") or ""),
            tool_count=_int_value(item.get("tool_count")),
            file_count=_int_value(item.get("file_count")),
            total_size=_int_value(item.get("total_size")),
            extraction=dict(item.get("extraction") or {}),
            mcp_resource=McpResource.from_descriptor(item),
            detect_result=DetectionResult.from_value(
                item.get("detect_result"),
                object_id=mcp_unique_id,
                object_type="mcp",
                name=name,
            ),
        )

    @property
    def owner_agent_id(self) -> str:
        return self.agent_id

    def to_dict(self) -> dict[str, Any]:
        descriptor = self.mcp_resource.to_dict()
        return {
            "owner_agent_id": self.agent_id,
            "agent_id": self.agent_id,
            "user_id": self.user_id,
            "session_id": self.session_id,
            "mcp_unique_id": self.mcp_unique_id,
            "name": self.name,
            "description": self.description,
            "source_framework": self.source_framework,
            "object_type": self.object_type,
            "transport": self.transport,
            "remote": self.remote,
            "root_path": self.root_path,
            "entry_file": self.entry_file,
            "url": self.url,
            "sha256": self.sha256,
            "tool_count": self.tool_count,
            "file_count": self.file_count,
            "total_size": self.total_size,
            "extraction": dict(self.extraction),
            "detect_result": self.detect_result.to_dict() if self.detect_result else None,
            "mcp_resource": descriptor,
            "descriptor": descriptor,
        }


def _mcp_unique_id(agent_id: str, descriptor: dict[str, Any]) -> str:
    explicit = str(descriptor.get("mcp_unique_id") or descriptor.get("id") or "").strip()
    if explicit:
        return explicit
    sha256 = str(descriptor.get("sha256") or "").strip()
    if sha256:
        return f"{agent_id}:{sha256}"
    name = str(descriptor.get("name") or "").strip()
    root_path = str(descriptor.get("root_path") or "").strip()
    transport = str(descriptor.get("transport") or "").strip()
    return f"{agent_id}:{name}:{transport}:{root_path}"


def _int_value(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0
