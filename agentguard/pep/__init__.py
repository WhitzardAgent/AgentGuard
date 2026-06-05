"""Policy Enforcement Point (PEP) — the client-side enforcement core.

The PEP gathers middleware annotations, asks either the remote PDP or the
local evaluator for a :class:`Decision`, applies obligations, and hands an
:class:`EnforcementResult` back to the Harness wrappers which act on it.
"""

from agentguard.pep.decision_cache import DecisionCache
from agentguard.pep.enforcer import EnforcementResult, Enforcer, EnforcerConfig
from agentguard.pep.fallback import FallbackPolicy
from agentguard.pep.local_evaluator import LocalEvaluator
from agentguard.pep.policy_snapshot import PolicySnapshot
from agentguard.pep.policy_sync import PolicySync

__all__ = [
    "Enforcer",
    "EnforcerConfig",
    "EnforcementResult",
    "DecisionCache",
    "FallbackPolicy",
    "LocalEvaluator",
    "PolicySnapshot",
    "PolicySync",
]
