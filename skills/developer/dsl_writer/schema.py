"""DSLWriterSkill input/output schema."""
from __future__ import annotations

INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "instruction": {"type": "string", "description": "Natural-language policy intent."}
    },
    "required": ["instruction"],
}

OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "rules": {"type": "array", "items": {"type": "object"}},
    },
}
