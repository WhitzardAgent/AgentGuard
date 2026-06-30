"""Static detector registry.

The benchmark environment prioritizes offline execution, auditability, and stable startup, so this module avoids dynamic imports and plugin scanning.
New detectors must be added explicitly to `DETECTORS`, and the order stays readable on purpose.
"""

from __future__ import annotations

from . import (
    attention_semantics,
    cross_platform,
    dependency,
    execution,
    explicit_malicious_intent,
    governance,
    isolation,
    metadata,
    network,
    obfuscation,
    openclaw_campaign,
    persistence,
    platform_shape,
    privilege,
    resource_abuse,
    sensitive_access,
    third_party_static,
)

DETECTORS = [
    # Emit concrete behavioral signals first, then shape/governance/platform uncertainty signals.
    sensitive_access.scan,
    execution.scan,
    network.scan,
    attention_semantics.scan,
    explicit_malicious_intent.scan,
    openclaw_campaign.scan,
    dependency.scan,
    metadata.scan,
    platform_shape.scan,
    obfuscation.scan,
    resource_abuse.scan,
    persistence.scan,
    privilege.scan,
    third_party_static.scan,
    isolation.scan,
    governance.scan,
    cross_platform.scan,
]
