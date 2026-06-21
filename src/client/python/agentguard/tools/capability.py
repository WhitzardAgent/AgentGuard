"""Tool capability constants used for policy and sandbox decisions."""
from __future__ import annotations

CAP_READ_FILE = "read_file"
CAP_WRITE_FILE = "write_file"
CAP_NETWORK = "network"
CAP_EXTERNAL_SEND = "external_send"
CAP_SHELL = "shell"
CAP_MEMORY_WRITE = "memory_write"
CAP_DATABASE_WRITE = "database_write"
CAP_PAYMENT = "payment"
CAP_BROWSER_ACTION = "browser_action"

ALL_CAPABILITIES = {
    CAP_READ_FILE,
    CAP_WRITE_FILE,
    CAP_NETWORK,
    CAP_EXTERNAL_SEND,
    CAP_SHELL,
    CAP_MEMORY_WRITE,
    CAP_DATABASE_WRITE,
    CAP_PAYMENT,
    CAP_BROWSER_ACTION,
}

# Capabilities considered high-risk; these tend to require remote review.
HIGH_RISK_CAPABILITIES = {
    CAP_EXTERNAL_SEND,
    CAP_SHELL,
    CAP_DATABASE_WRITE,
    CAP_PAYMENT,
}


def is_high_risk(capabilities: list[str] | set[str]) -> bool:
    return bool(set(capabilities) & HIGH_RISK_CAPABILITIES)
