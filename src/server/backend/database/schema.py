"""Schema initialization entrypoint for persistent backend modules."""
from __future__ import annotations

from backend.database.config import get_mysql_config


def ensure_schema() -> None:
    """Initialize configured persistent schemas.

    No-op when MySQL is not configured so existing in-memory-only development
    and tests keep working. When AGENTGUARD_MYSQL_URL is set, failures propagate
    and FastAPI startup fails fast.
    """
    if get_mysql_config() is None:
        return
    from backend.agents.store import ensure_agent_schema  # noqa: PLC0415
    from backend.auth.session_store import ensure_runtime_session_schema  # noqa: PLC0415
    from backend.runtime.trace_store import ensure_trace_schema  # noqa: PLC0415
    from backend.user.store import ensure_user_schema  # noqa: PLC0415

    ensure_user_schema()
    ensure_agent_schema()
    ensure_runtime_session_schema()
    ensure_trace_schema()
