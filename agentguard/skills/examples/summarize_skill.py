"""A dependency-free extractive summarisation skill."""

from __future__ import annotations

import re
from typing import Any

from agentguard.schemas.context import RuntimeContext
from agentguard.skills.base import Skill


class SummarizeSkill(Skill):
    name = "summarize"
    input_schema = {"text": "the text to summarise"}

    def __init__(self, *, max_sentences: int = 3) -> None:
        self.max_sentences = max_sentences

    def run(self, context: RuntimeContext, **inputs: Any) -> str:
        text = str(inputs["text"]).strip()
        sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]
        if not sentences:
            return ""
        # Rank sentences by word-frequency score (a tiny TextRank-ish heuristic).
        freq: dict[str, int] = {}
        for word in re.findall(r"[a-zA-Z]+", text.lower()):
            freq[word] = freq.get(word, 0) + 1
        scored = sorted(
            enumerate(sentences),
            key=lambda pair: sum(freq.get(w, 0) for w in re.findall(r"[a-zA-Z]+", pair[1].lower())),
            reverse=True,
        )
        chosen = sorted(scored[: self.max_sentences], key=lambda pair: pair[0])
        return " ".join(s for _, s in chosen)

    def fallback(self, context: RuntimeContext, reason: str, **inputs: Any) -> str:
        text = str(inputs.get("text", ""))
        return text[:200]
