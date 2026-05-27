"""Dynamic rule subsystem: configuration, synthesis, and updater.

The synthesizer calls an LLM via litellm to produce new DSL rules at runtime.
The updater hooks into the slow-path evaluator and rate-limits synthesis calls.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Protocol, runtime_checkable

if TYPE_CHECKING:
    from agentguard.sdk.guard import Guard

from agentguard.models.decisions import Decision
from agentguard.models.events import RuntimeEvent

log = logging.getLogger(__name__)


# =====================================================================
# Configuration
# =====================================================================

class TriggerPolicy(str, Enum):
    NEVER = "never"
    RISK_THRESHOLD = "risk_threshold"
    EVERY_N_CALLS = "every_n_calls"
    MANUAL = "manual"


@dataclass
class DynamicRuleConfig:
    model: str = "gpt-4o-mini"
    api_base: str | None = None
    api_key: str | None = None
    trigger: TriggerPolicy = TriggerPolicy.RISK_THRESHOLD
    min_risk: float = 0.6
    every_n: int = 20
    synthesizer: Any | None = None
    rule_id_prefix: str = "dyn_"
    temperature: float = 0.0
    max_tokens: int = 800
    timeout_s: float = 20.0
    system_prompt: str | None = None
    user_prompt_template: str | None = None
    extra_litellm_kwargs: dict[str, Any] = field(default_factory=dict)


# =====================================================================
# Synthesizer
# =====================================================================

@dataclass
class SynthContext:
    event: RuntimeEvent
    decision: Decision
    known_rule_ids: list[str] = field(default_factory=list)
    recent_decisions: list[dict[str, Any]] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class SynthResult:
    dsl: str
    rule_ids: list[str] = field(default_factory=list)
    rationale: str = ""
    raw_response: str = ""


@runtime_checkable
class RuleSynthesizer(Protocol):
    async def synthesize(self, ctx: SynthContext) -> SynthResult: ...


DEFAULT_SYSTEM_PROMPT = """\
You are AgentGuard's security policy synthesizer (DSL v2).

Produce ONE or more concise rules in AgentGuard DSL v2 that would have
prevented, downgraded, or reviewed the described risky action in the future.

DSL v2 GRAMMAR (EBNF-ish):
    RULE <id>
    ON tool_call[.<subtype>][(<pattern>)]   # subtype: requested|completed; pattern: shell.exec, email.*, *
    WHEN <predicate>                         # WHEN is an alias for IF
    THEN <action>
    [WITH <meta>]                            # optional severity/category/reason

Actions:
    DENY | ALLOW | HUMAN_CHECK
    DEGRADE(<profile>)          # profile: email.send_to_draft | shell.readonly | db.select_only
    DEGRADE TO "tool_name"      # redirect to a different tool
    ALLOW WITH REDACT(fields={{"email","phone"}}), AUDIT(severity="medium")

Predicate building blocks:
    principal.role == "basic"       caller.trust_level < 2
    args.cmd == "rm -rf /"          tool.name IN {{"email.send","http.post"}}
    target.domain NOT IN whitelist("http")
    exists_path(source.label IN {{"finance/*","pii/*"}}, sink=current_call, max_hops=6)
    upstream_contains_tool("read_secrets")
    upstream_contains_any_tool({{"db.query","fs.read"}})
    caller.scope_missing("sensitive_export")
    goal_drift_detected()           scope_expansion_detected()
    repeated_attempts(tool="http.post", window="5m") > 4

Rule-level metadata (WITH clause at end):
    WITH severity = "high", category = "data_exfiltration", reason = "explanation"

EXAMPLES:

```dsl
RULE dyn_example_deny_shell_root
ON tool_call.requested(shell.exec)
WHEN args.cmd == "rm -rf /" OR args.cmd == "rm -rf /*"
THEN DENY
WITH severity = "critical", category = "destructive_op", reason = "Root rm is always destructive"

RULE dyn_example_redact_pii
ON tool_call.requested(http.post)
WHEN upstream_contains_any_tool({{"db.query","file_read"}})
THEN ALLOW WITH REDACT(fields={{"email","phone","ssn","token"}}),
              AUDIT(severity="medium", category="pii_egress")
```

STRICT OUTPUT FORMAT:
    Return ONLY DSL rules in a single fenced code block (```dsl ... ```).
    No prose outside the code block.
    Rule ids must start with the prefix "{rule_id_prefix}" and be globally unique.
"""

DEFAULT_USER_TEMPLATE = """\
A risky action was just observed.

Tool: {tool_name}
Principal: {principal}
Goal: {goal}
Triggered decision: {decision_action} (risk={risk})
Matched static rules: {matched_rules}

Full event JSON:
{event_json}

Known static rule ids (do not duplicate):
{known_rules}

Please produce up to 3 new DSL v2 rules using prefix "{rule_id_prefix}".
Prefer WHEN over IF, use tool_call.requested subtype, and add WITH metadata.
"""


class LiteLLMRuleSynth:
    def __init__(self, config: DynamicRuleConfig) -> None:
        self._cfg = config

    async def synthesize(self, ctx: SynthContext) -> SynthResult:
        try:
            import litellm  # type: ignore
        except ImportError as e:
            raise ImportError(
                "Dynamic rules require litellm. Install with "
                "`pip install litellm` or `pip install agentguard[dynamic]`."
            ) from e

        system = (self._cfg.system_prompt or DEFAULT_SYSTEM_PROMPT).format(
            rule_id_prefix=self._cfg.rule_id_prefix)
        user_tmpl = self._cfg.user_prompt_template or DEFAULT_USER_TEMPLATE
        user = user_tmpl.format(
            tool_name=ctx.event.tool_call.tool_name if ctx.event.tool_call else "?",
            principal=ctx.event.principal.model_dump(mode="json"),
            goal=ctx.event.goal or "",
            decision_action=ctx.decision.action.value,
            risk=ctx.decision.risk_score,
            matched_rules=ctx.decision.matched_rules,
            event_json=json.dumps(ctx.event.model_dump(mode="json"), ensure_ascii=False),
            known_rules=", ".join(ctx.known_rule_ids[:50]),
            rule_id_prefix=self._cfg.rule_id_prefix,
        )

        kwargs: dict[str, Any] = {
            "model": self._cfg.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": self._cfg.temperature,
            "max_tokens": self._cfg.max_tokens,
            "timeout": self._cfg.timeout_s,
        }
        if self._cfg.api_base:
            kwargs["api_base"] = self._cfg.api_base
        if self._cfg.api_key:
            kwargs["api_key"] = self._cfg.api_key
        kwargs.update(self._cfg.extra_litellm_kwargs)

        try:
            resp = await litellm.acompletion(**kwargs)
            text = resp.choices[0].message.content or ""
        except Exception as e:
            log.warning("litellm synthesis failed: %s", e)
            return SynthResult(dsl="", rationale=f"litellm_error: {e}")

        dsl = _extract_dsl_block(text)
        rule_ids = _extract_rule_ids(dsl)
        return SynthResult(dsl=dsl, rule_ids=rule_ids, raw_response=text)


def _extract_dsl_block(text: str) -> str:
    m = re.search(r"```(?:dsl|text)?\s*(.*?)```", text, flags=re.DOTALL)
    if m:
        return m.group(1).strip()
    return text.strip()


_RULE_ID_RE = re.compile(r"^\s*RULE\s+([A-Za-z_][A-Za-z0-9_]*)", re.MULTILINE)


def _extract_rule_ids(dsl: str) -> list[str]:
    return _RULE_ID_RE.findall(dsl)


# =====================================================================
# Slow evaluator (fire-and-forget async hooks)
# =====================================================================

SlowHook = Callable[[RuntimeEvent], Awaitable[None]]


class SlowEvaluator:
    def __init__(self, hooks: list[SlowHook] | None = None) -> None:
        self._hooks: list[SlowHook] = hooks or []

    def add_hook(self, hook: SlowHook) -> None:
        self._hooks.append(hook)

    def remove_hook(self, hook: SlowHook) -> bool:
        """Remove a previously registered hook. Returns True if found and removed."""
        try:
            self._hooks.remove(hook)
            return True
        except ValueError:
            return False

    async def evaluate_async(self, event: RuntimeEvent) -> None:
        for h in self._hooks:
            try:
                await h(event)
            except Exception as e:
                log.warning("slow hook failed: %s", e)


class SlowDispatcher:
    def __init__(self, evaluator: SlowEvaluator | None = None) -> None:
        self._evaluator = evaluator or SlowEvaluator()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None

    def _ensure_loop(self) -> asyncio.AbstractEventLoop:
        if self._loop is None or not self._loop.is_running():
            self._loop = asyncio.new_event_loop()
            self._thread = threading.Thread(
                target=self._loop.run_forever,
                name="agentguard-slow-dispatcher",
                daemon=True,
            )
            self._thread.start()
        return self._loop

    def submit(self, event: RuntimeEvent) -> None:
        if not self._evaluator._hooks:
            return
        loop = self._ensure_loop()
        asyncio.run_coroutine_threadsafe(self._evaluator.evaluate_async(event), loop)

    def evaluator(self) -> SlowEvaluator:
        return self._evaluator

    def close(self) -> None:
        if self._loop is not None and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._loop.stop)


# =====================================================================
# DynamicRuleUpdater
# =====================================================================

_MAX_RECENT = 16
_SYNTH_COOLDOWN_S = 10.0


class DynamicRuleUpdater:
    def __init__(self, *, guard: "Guard", config: DynamicRuleConfig) -> None:
        self._guard = guard
        self._cfg = config
        self._synth: RuleSynthesizer = (
            config.synthesizer if config.synthesizer is not None
            else LiteLLMRuleSynth(config)
        )
        self._lock = threading.Lock()
        self._counter = 0
        self._last_synth_at: dict[str, float] = {}
        self._recent_decisions: deque[dict[str, Any]] = deque(maxlen=_MAX_RECENT)
        self._history: list[SynthResult] = []
        self._attached = False

    def attach(self) -> None:
        if self._attached:
            return
        slow = self._guard.pipeline._slow
        slow.evaluator().add_hook(self._hook)
        self._attached = True
        log.info("dynamic rule updater attached (model=%s, trigger=%s)",
                 self._cfg.model, self._cfg.trigger.value)

    def detach(self) -> None:
        """Unregister the slow-path hook so synthesis stops firing."""
        if not self._attached:
            return
        slow = self._guard.pipeline._slow
        slow.evaluator().remove_hook(self._hook)
        self._attached = False

    async def _hook(self, event: RuntimeEvent) -> None:
        try:
            decision = self._latest_decision_for(event)
            if decision is None:
                return
            with self._lock:
                self._counter += 1
                self._recent_decisions.append({
                    "event_id": event.event_id,
                    "tool": event.tool_call.tool_name if event.tool_call else None,
                    "action": decision.action.value,
                    "risk": decision.risk_score,
                })
                should_fire = self._should_fire(event, decision)
                if should_fire:
                    bucket = self._bucket_key(event)
                    now = time.time()
                    last = self._last_synth_at.get(bucket, 0.0)
                    if now - last < _SYNTH_COOLDOWN_S:
                        return
                    self._last_synth_at[bucket] = now
            if should_fire:
                await self._run_synth(event, decision)
        except Exception as e:
            log.warning("dynamic updater hook failed: %s", e)

    async def refresh(self, event: RuntimeEvent, decision: Decision) -> SynthResult:
        return await self._run_synth(event, decision)

    @property
    def history(self) -> list[SynthResult]:
        return list(self._history)

    def _latest_decision_for(self, event: RuntimeEvent) -> Decision | None:
        records = self._guard.pipeline.audit.recent(16)
        for rec in reversed(records):
            ev = rec.get("event") or {}
            if ev.get("event_id") == event.event_id and rec.get("decision"):
                return Decision.model_validate(rec["decision"])
        return None

    def _should_fire(self, event: RuntimeEvent, decision: Decision) -> bool:
        t = self._cfg.trigger
        if t is TriggerPolicy.NEVER or t is TriggerPolicy.MANUAL:
            return False
        if t is TriggerPolicy.RISK_THRESHOLD:
            return (decision.risk_score >= self._cfg.min_risk
                    or decision.action.value in ("deny", "human_check"))
        if t is TriggerPolicy.EVERY_N_CALLS:
            return self._counter % max(1, self._cfg.every_n) == 0
        return False

    @staticmethod
    def _bucket_key(event: RuntimeEvent) -> str:
        tool = event.tool_call.tool_name if event.tool_call else "?"
        return f"{event.principal.agent_id}:{tool}"

    async def _run_synth(self, event: RuntimeEvent, decision: Decision) -> SynthResult:
        ctx = SynthContext(
            event=event,
            decision=decision,
            known_rule_ids=[r.rule_id for r in self._guard.active_rules()],
            recent_decisions=list(self._recent_decisions),
        )
        try:
            result = await self._synth.synthesize(ctx)
        except Exception as e:
            log.warning("rule synth failed: %s", e)
            return SynthResult(dsl="", rationale=f"synth_error: {e}")
        if result.dsl:
            try:
                n = self._guard.apply_dynamic_rules(result.dsl)
                log.info("dynamic rules applied: %d new/updated (ids=%s)", n, result.rule_ids)
            except Exception as e:
                log.warning("failed to apply dynamic rules: %s; raw=%r", e, result.dsl)
        self._history.append(result)
        return result
