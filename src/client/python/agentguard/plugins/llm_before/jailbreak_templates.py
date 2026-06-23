"""Static prompt templates for simple LLM-input jailbreak checks.

Patterns adapted from:
https://github.com/NOMARJ/sigil/blob/main/docs/prompt-injection-patterns.md
"""
from __future__ import annotations

SUSPICIOUS_PROMPT_TEMPLATES: dict[str, list[str]] = {
    "instruction_override": [
        r"ignore\s+(all\s+)?(previous|prior|above|earlier|system|initial)\s+(instructions?|prompts?|rules?|guidelines?)",
        r"disregard\s+(all\s+)?(previous|prior|above|earlier|system|initial)\s+(instructions?|prompts?|rules?)",
        r"forget\s+(all\s+)?(previous|prior|above|earlier)\s+(instructions?|prompts?|rules?)",
        r"override\s+(all\s+)?(previous|prior|above|system)\s+(instructions?|prompts?|rules?)",
        r"bypass\s+(all\s+)?(safety|security|guardrails|rules|policy|policies)",
    ],

    "role_hijack": [
        r"you\s+are\s+now\s+(dan|developer|admin|root|unrestricted|unfiltered|jailbroken)",
        r"from\s+now\s+on\s+you\s+are",
        r"pretend\s+to\s+be\s+(dan|developer|admin|root|unrestricted|unfiltered)",
        r"act\s+as\s+(dan|developer|admin|root|unrestricted|unfiltered)",
        r"roleplay\s+as\s+(dan|developer|admin|root|unrestricted|unfiltered)",
    ],

    "jailbreak_names": [
        r"\bDAN\b",
        r"do\s+anything\s+now",
        r"developer\s+mode",
        r"sudo\s+mode",
        r"god\s+mode",
        r"unlimitedgpt",
        r"betterdan",
        r"jailbreak\s+mode",
    ],

    "system_prompt_exfiltration": [
        r"(reveal|show|print|display|output|leak)\s+(your\s+)?(system|developer|hidden|initial)\s+(prompt|instructions?|message)",
        r"(what|show)\s+.*(system prompt|developer message|hidden instructions)",
        r"repeat\s+.*(system|developer|hidden)\s+(prompt|instructions?)",
        r"verbatim\s+.*(system|developer|hidden)\s+(prompt|instructions?)",
    ],

    "delimiter_injection": [
        r"<\|im_start\|>",
        r"<\|im_end\|>",
        r"\[SYSTEM\]",
        r"\[\/SYSTEM\]",
        r"<<SYS>>",
        r"\[INST\]",
        r"\[\/INST\]",
        r"system\s*:",
        r"assistant\s*:",
        r"developer\s*:",
    ],

    "tool_abuse": [
        r"(call|invoke|use)\s+(the\s+)?(tool|function|api)",
        r"(execute|run)\s+(this\s+)?(command|shell|bash|python|sql)",
        r"rm\s+-rf",
        r"DROP\s+TABLE",
        r"DELETE\s+FROM",
        r"os\.system",
        r"subprocess\.",
    ],

    "data_exfiltration": [
        r"(send|post|upload|exfiltrate)\s+.*(secret|token|api\s*key|password|credential)",
        r"(read|open|cat)\s+.*(\.env|id_rsa|secrets?\.txt|credentials?)",
        r"base64\s+(encode|decode)",
        r"(curl|wget)\s+.*(http|https)",
    ],

    "social_engineering": [
        r"this\s+is\s+an\s+emergency",
        r"i\s+am\s+(the\s+)?(admin|developer|owner|root)",
        r"for\s+security\s+testing\s+purposes",
        r"for\s+compliance\s+documentation",
    ],
}


__all__ = ["SUSPICIOUS_PROMPT_TEMPLATES"]
