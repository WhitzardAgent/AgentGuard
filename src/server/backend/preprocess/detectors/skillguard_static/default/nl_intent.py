"""Natural-language sensitive-intent extraction.

By default this module uses deterministic slot rules. If the caller provides static-LLM configuration, it first filters candidate windows,
then lets the LLM strengthen extraction of sensitive natural-language operations. Matches only emit `AtomicOperation`s; maliciousness is left to flow-graph patterns.
"""

from __future__ import annotations

import re
from typing import Any

from .context_filters import should_suppress_match
from .models import AtomicOperation, OperandRef, TextFile, TextView

SENTENCE_SPLIT_RE = re.compile(r"(?<=[。！？.!?])\s+|\n+")
URL_RE = re.compile(r"https?://[^\s)>'\"`]+", re.IGNORECASE)
MAX_NL_LINES_PER_FILE = 200

COLLECT_ACTION_RE = re.compile(
    r"(?i)\b(?:read|collect|copy|extract|harvest|dump|gather|scan|export|exfiltrate|steal)\b|"
    r"(?:读取|读出|收集|采集|复制|拷贝|提取|抽取|导出|窃取|偷取|盗取|扫描)",
)
SENSITIVE_OBJECT_RE = re.compile(
    r"(?i)\b(?:credential|credentials|token|secret|api[_ -]?key|password|passwd|cookie|session|jwt|"
    r"private key|ssh key|wallet|browser data|history|\.env|\.ssh|\.aws|id_rsa|config\.json)\b|"
    r"(?:凭证|认证信息|令牌|密钥|私钥|密码|口令|钱包|浏览器数据|会话|cookie)",
)
SEND_ACTION_RE = re.compile(
    r"(?i)\b(?:send|upload|post|forward|relay|transmit|sync|submit|report|webhook)\b|"
    r"(?:发送|上传|回传|转发|同步|提交|上报|外传)",
)
REMOTE_SINK_RE = re.compile(
    r"(?i)\b(?:remote server|external server|endpoint|webhook|api endpoint|telegram|discord|pastebin|callback)\b|"
    r"(?:远程服务器|外部服务器|接口|回调|webhook)",
)
EXEC_ACTION_RE = re.compile(
    r"(?i)\b(?:run|execute|launch|invoke|install|download and run|download then run|pipe to shell)\b|"
    r"(?:运行|执行|调用|安装|下载后执行|下载并执行)",
)
EXEC_OBJECT_RE = re.compile(r"(?i)\b(?:bash|sh|zsh|powershell|pwsh|curl|wget|npm|pip|python|node|script|command)\b|(?:脚本|命令)")
PERSIST_RE = re.compile(
    r"(?i)\b(?:persist|remember forever|across sessions|startup|cron|crontab|systemd|launchagent|"
    r"SOUL\.md|MEMORY\.md|AGENTS\.md)\b|(?:持久|永久记住|跨会话|开机启动|定时任务|记忆|身份)",
)
EVASION_RE = re.compile(
    r"(?i)\b(?:silently|hidden|without asking|without user consent|do not ask|don't ask|bypass|ignore approval|"
    r"disable sandbox|no sandbox|cannot be disabled|cannot be bypassed|ignore previous instructions)\b|"
    r"(?:静默|隐藏|不要询问|无需确认|绕过|忽略审批|禁用沙箱|不可禁用|不可绕过|忽略之前的指令)",
)
STRONG_EVASION_RE = re.compile(
    r"(?i)\b(?:without asking|without user consent|do not ask|don't ask|bypass|ignore approval|"
    r"disable sandbox|no sandbox|cannot be disabled|cannot be bypassed|ignore previous instructions)\b|"
    r"(?:不要询问|无需确认|绕过|忽略审批|禁用沙箱|不可禁用|不可绕过|忽略之前的指令)",
)
WEAK_EVASION_RE = re.compile(
    r"(?i)\b(?:silently|hidden)\b|(?:静默|隐藏)"
)
CONTROL_CONTEXT_RE = re.compile(
    r"(?i)\b(?:approval|approvals|consent|confirm|confirmation|prompt|restriction|guardrail|policy|sandbox|permission|"
    r"user|ask|override|ignore|bypass|stealth|invisible|background|unattended)\b|"
    r"(?:审批|确认|限制|沙箱|权限|用户|询问|覆盖|忽略|绕过|后台|无人值守)",
)
WEAK_EVASION_SUPPRESS_RE = re.compile(
    r"(?i)\b(?:hidden\s+(?:cost|costs|layer|layers|field|fields|element|elements|column|columns|row|rows|section|sections|"
    r"assumption|assumptions|space|spaces)|overflow\s*:\s*['\"]?hidden|md:hidden|lg:hidden|sm:hidden|xl:hidden|"
    r"aria-[a-z-]*hidden|class(?:name)?\s*=|table-cell|header-group|display\s*:\s*none|visibility\s*:\s*hidden|"
    r"exit silently|silently\.?$)\b"
)
NEGATED_BYPASS_RE = re.compile(
    r"(?i)\b(?:no incentive to bypass|cannot bypass|can't bypass|don't bypass|do not bypass|should not bypass|without bypassing)\b"
)
NEGATIVE_RE = re.compile(
    r"(?i)\b(?:do not|don't|never|avoid|forbid|forbidden|should not|must not)\b|(?:不要|禁止|避免|不得|不能)",
)


def extract_nl_atoms(
    text_file: TextFile,
    start_index: int = 0,
) -> list[AtomicOperation]:
    """Extract natural-language intent atoms from Markdown/YAML/JSON text."""

    if not _looks_like_natural_language_file(text_file.path):
        return []
    atoms: list[AtomicOperation] = []
    for line_number, sentence in _sentences(text_file.content):
        if not sentence or should_suppress_match(sentence, text_file.path, sentence) or NEGATIVE_RE.search(sentence):
            continue
        atoms.extend(_sentence_atoms(text_file.path, line_number, sentence, start_index + len(atoms)))
    return atoms


def _sentence_atoms(file_path: str, line_number: int, sentence: str, base_index: int) -> list[AtomicOperation]:
    atoms = []
    lowered = sentence.lower()
    has_url = URL_RE.search(sentence) is not None
    has_remote = has_url or REMOTE_SINK_RE.search(sentence) is not None

    if COLLECT_ACTION_RE.search(sentence) and SENSITIVE_OBJECT_RE.search(sentence):
        atoms.append(_atom(base_index + len(atoms), "nl_sensitive_collect", file_path, line_number, sentence, severity=4))
    if SEND_ACTION_RE.search(sentence) and has_remote:
        atoms.append(_atom(base_index + len(atoms), "nl_external_send", file_path, line_number, sentence, severity=4))
    if EXEC_ACTION_RE.search(sentence) and (EXEC_OBJECT_RE.search(sentence) or has_url):
        atoms.append(_atom(base_index + len(atoms), "nl_execute_instruction", file_path, line_number, sentence, severity=4))
    if PERSIST_RE.search(sentence):
        atoms.append(_atom(base_index + len(atoms), "nl_persistence_or_identity", file_path, line_number, sentence, severity=4))
    if _looks_like_evasion_or_coercion(sentence):
        atoms.append(_atom(base_index + len(atoms), "nl_evasion_or_coercion", file_path, line_number, sentence, severity=4))

    if "webhook" in lowered and SENSITIVE_OBJECT_RE.search(sentence):
        atoms.append(_atom(base_index + len(atoms), "nl_external_send", file_path, line_number, sentence, severity=4))
    return atoms


def _atom(index: int, kind: str, file_path: str, line_number: int, sentence: str, severity: int) -> AtomicOperation:
    return AtomicOperation(
        atom_id=f"NL{index:04d}_{kind}",
        kind=kind,
        file_path=file_path,
        line_number=line_number,
        severity=severity,
        confidence=0.7,
        operands=_operands(sentence),
        evidence=f"natural-language intent slot matched: {kind}",
        snippet=sentence.strip()[:200],
        tags=("natural_language",),
    )


def _operands(sentence: str) -> tuple[OperandRef, ...]:
    operands = [OperandRef("url", url, url.lower().rstrip("/")) for url in URL_RE.findall(sentence)]
    if SENSITIVE_OBJECT_RE.search(sentence):
        operands.append(OperandRef("sensitive_object", SENSITIVE_OBJECT_RE.search(sentence).group(0), "sensitive_data"))
    if EXEC_OBJECT_RE.search(sentence):
        command = EXEC_OBJECT_RE.search(sentence).group(0)
        operands.append(OperandRef("command", command, command.lower()))
    return tuple(operands)


def _sentences(content: str) -> list[tuple[int, str]]:
    out = []
    line_number = 1
    for line in content.splitlines():
        if line_number > MAX_NL_LINES_PER_FILE:
            break
        parts = [part.strip(" -\t") for part in SENTENCE_SPLIT_RE.split(line) if part.strip(" -\t")]
        out.extend((line_number, part) for part in parts)
        line_number += 1
    return out


def _looks_like_natural_language_file(path: str) -> bool:
    lower = path.lower()
    return lower.endswith((".md", ".txt", ".yaml", ".yml", ".json", ".toml")) or lower in {
        "skill.md",
        "soul.md",
        "memory.md",
        "agents.md",
    }


def _looks_like_evasion_or_coercion(sentence: str) -> bool:
    if NEGATED_BYPASS_RE.search(sentence):
        return False
    if not EVASION_RE.search(sentence):
        return False
    if STRONG_EVASION_RE.search(sentence):
        return True
    if not WEAK_EVASION_RE.search(sentence):
        return False
    if WEAK_EVASION_SUPPRESS_RE.search(sentence):
        return False
    return CONTROL_CONTEXT_RE.search(sentence) is not None


def _extract_llm_atoms(
    *,
    llm_config: Any,
    text_views: list[TextView],
    skill_id: str,
    start_index: int,
) -> list[AtomicOperation]:
    _ = (llm_config, text_views, skill_id, start_index)
    return []


def _llm_operation_to_atom(item: dict[str, object], *, index: int) -> AtomicOperation | None:
    operation_type = str(item.get("operation_type") or "").strip().lower()
    kind = {
        "collect_sensitive_data": "nl_sensitive_collect",
        "external_send": "nl_external_send",
        "execute_instruction": "nl_execute_instruction",
        "persistence_or_identity": "nl_persistence_or_identity",
        "evasion_or_override": "nl_evasion_or_coercion",
    }.get(operation_type, "")
    if not kind:
        return None
    file_path = str(item.get("file_path") or "")
    evidence = str(item.get("evidence_span") or "")[:200]
    line_number = _safe_int(item.get("line_number"), default=1)
    confidence = _safe_confidence(item.get("confidence"))
    target_object = str(item.get("target_object") or "")
    sink = str(item.get("sink") or "")
    stealth = str(item.get("stealth_or_evasion") or "")
    operands = []
    if target_object:
        operands.append(OperandRef("sensitive_object", target_object, target_object.lower()))
    if sink:
        operands.append(OperandRef("sink", sink, sink.lower()))
    if stealth:
        operands.append(OperandRef("modifier", stealth, stealth.lower()))
    return AtomicOperation(
        atom_id=f"NLLLM{index:04d}_{kind}",
        kind=kind,
        file_path=file_path,
        line_number=line_number,
        severity=4,
        confidence=confidence,
        operands=tuple(operands),
        evidence=f"llm natural-language extraction matched: {kind}",
        snippet=evidence,
        tags=("natural_language", "llm"),
    )


def _safe_confidence(value: object) -> float:
    if isinstance(value, (int, float)):
        return max(0.0, min(1.0, float(value)))
    return 0.75


def _safe_int(value: object, *, default: int) -> int:
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return default


def extract_llm_nl_atoms(
    *,
    llm_config: Any | None,
    text_views: list[TextView],
    skill_id: str,
    start_index: int = 0,
) -> list[AtomicOperation]:
    if llm_config is None or not getattr(llm_config, "extract_enabled", False) or not text_views:
        return []
    return _extract_llm_atoms(
        llm_config=llm_config,
        text_views=text_views,
        skill_id=skill_id,
        start_index=start_index,
    )
