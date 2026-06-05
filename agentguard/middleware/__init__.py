"""Pluggable analysis middleware applied to every intercepted event.

Each middleware inspects a :class:`RuntimeEvent`, may attach annotations
(consumed by policy rules) and contributes to an aggregated
:class:`RiskAssessment`. Middleware never blocks directly — enforcement is the
PEP's job — keeping concerns cleanly separated.
"""

from agentguard.middleware.base import Middleware, MiddlewareChain
from agentguard.middleware.pii_detector import PIIDetector
from agentguard.middleware.prompt_injection import PromptInjectionDetector
from agentguard.middleware.rate_limiter import RateLimiter
from agentguard.middleware.risk_classifier import RiskClassifier
from agentguard.middleware.uncertainty import UncertaintyDetector

__all__ = [
    "Middleware",
    "MiddlewareChain",
    "PIIDetector",
    "PromptInjectionDetector",
    "RateLimiter",
    "RiskClassifier",
    "UncertaintyDetector",
    "default_middleware",
]


def default_middleware() -> list[Middleware]:
    """The standard analysis chain enabled by the Harness by default."""
    return [
        PIIDetector(),
        PromptInjectionDetector(),
        UncertaintyDetector(),
        RateLimiter(),
        RiskClassifier(),
    ]
