"""AgentDog audit configuration.

Only these two environment variables are required for the audit integration:

- AGENTDOG_URL: OpenAI-compatible chat completions endpoint.
- AGENTDOG_API_KEY: bearer token for the AgentDog endpoint.
"""
from __future__ import annotations

from dataclasses import dataclass
import os


AGENTDOG_URL_ENV = "AGENTDOG_URL"
AGENTDOG_API_KEY_ENV = "AGENTDOG_API_KEY"
DEFAULT_AGENTDOG_URL = (
    "http://gw-bzokqkvr2cblz8ok6y.cn-wulanchabu-acdr-1.pai-eas.aliyuncs.com"
    "/api/predict/llm_hg_agentdogs_1_5_qwen_3_5_4b/v1/chat/completions"
)
DEFAULT_AGENTDOG_API_KEY = "NTg1MWM4MDViNWZlNjEzZDkyMDkzNGU5YjcxNzkxYzFjZjdkYjlhZA=="


@dataclass(frozen=True)
class AgentDogAuditConfig:
    agentdog_url: str = ""
    agentdog_apiKey: str = ""

    @classmethod
    def from_env(cls) -> "AgentDogAuditConfig":
        return cls(
            agentdog_url=(os.environ.get(AGENTDOG_URL_ENV) or DEFAULT_AGENTDOG_URL).strip(),
            agentdog_apiKey=(os.environ.get(AGENTDOG_API_KEY_ENV) or DEFAULT_AGENTDOG_API_KEY).strip(),
        )

    def to_plugin_style_dict(self) -> dict[str, str]:
        return {
            "agentdog_url": self.agentdog_url,
            "agentdog_apiKey": self.agentdog_apiKey,
        }
