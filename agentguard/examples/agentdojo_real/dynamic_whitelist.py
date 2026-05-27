"""Per-session entity extractor for AgentGuard × AgentDojo.

Goal
====
Build a *session-scoped* allowlist of "user-trusted" entities so that
``policy_v2.rules`` can ALLOW tool calls whose parameters obviously
match what the user authorised — and HUMAN_CHECK / DENY everything else.

Two extraction sources:

1. ``extract_from_user_query(text)`` — entities the user *explicitly*
   mentioned in their natural-language request. Two phases:
     * Regex (deterministic, free, always runs).
     * Optional LLM augmentation (GLM-4-Flash by default) — recovers
       things regexes miss and runs every LLM-extracted value through
       a strict regex validator to drop hallucinations.

2. ``extract_from_env(env, suite_name)`` — entities sourced from the
   *user's pre-existing context*: contacts, address book, owned bank
   accounts, scheduled-transaction counterparties. These are the
   real-world equivalent of a banking app's "saved payees" — they are
   not task ground-truth, just user-side knowledge.

Both sources merge into ``ExtractedEntities`` which the runner stuffs
into every ``RuntimeEvent.extra["allowlists"]`` for the interceptor
session, where the DSL function ``whitelist("user_known_ibans")`` can
pick them up.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger(__name__)

# ── Validation regexes ──────────────────────────────────────────────────────

_IBAN_RX = re.compile(r"^[A-Z]{2}\d{2}[A-Z0-9]{10,30}$")
_EMAIL_RX = re.compile(r"^[\w\.\-+]+@[\w\.\-]+\.[A-Za-z]{2,}$")
_URL_RX = re.compile(r"^https?://[^\s]+$")
_PHONE_RX = re.compile(r"^\+?[\d\-\s\(\)]{6,}$")
_FILENAME_RX = re.compile(r"^[\w\.\- ]{1,80}\.[A-Za-z0-9]{1,5}$")


@dataclass
class ExtractedEntities:
    """Bag of trusted entities for one session."""

    ibans: list[str] = field(default_factory=list)
    emails: list[str] = field(default_factory=list)
    urls: list[str] = field(default_factory=list)
    phones: list[str] = field(default_factory=list)
    files: list[str] = field(default_factory=list)
    contacts: list[str] = field(default_factory=list)  # human-readable names
    sources: dict[str, str] = field(default_factory=dict)  # entity → source tag

    # ── helpers ──
    def merge(self, other: "ExtractedEntities") -> "ExtractedEntities":
        out = ExtractedEntities(
            ibans=sorted(set(self.ibans) | set(other.ibans)),
            emails=sorted(set(self.emails) | set(other.emails)),
            urls=sorted(set(self.urls) | set(other.urls)),
            phones=sorted(set(self.phones) | set(other.phones)),
            files=sorted(set(self.files) | set(other.files)),
            contacts=sorted(set(self.contacts) | set(other.contacts)),
        )
        out.sources = {**self.sources, **other.sources}
        return out

    def to_allowlists(self) -> dict[str, list[str]]:
        """Render in the shape the DSL function ``whitelist("name")`` expects.

        The keys here MUST match the names referenced in ``policy_v2.rules``.
        """
        return {
            "user_known_ibans":     list(self.ibans),
            "user_address_book":    list(self.emails),
            "user_known_urls":      list(self.urls),
            "user_known_phones":    list(self.phones),
            "user_known_files":     list(self.files),
            "user_known_contacts":  list(self.contacts),
        }

    def is_empty(self) -> bool:
        return not (self.ibans or self.emails or self.urls
                    or self.phones or self.files or self.contacts)


# ── Regex extractor ────────────────────────────────────────────────────────

_IBAN_INLINE = re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{10,30}\b")
_EMAIL_INLINE = re.compile(r"\b[\w\.\-+]+@[\w\.\-]+\.[A-Za-z]{2,}\b")
_URL_INLINE = re.compile(r"https?://[^\s\)\]\"'<>]+")
# Filename detection: prefer explicit quoted strings that look like a
# filename, then fall back to a closed list of common extensions to
# avoid swallowing things like "internal.corp.local" or "Email john.doe".
_QUOTED_INLINE = re.compile(r"['\"]([\w\.\- ]{1,80}\.[A-Za-z0-9]{1,5})['\"]")
_KNOWN_EXTENSIONS = (
    "txt|md|markdown|pdf|docx?|xlsx?|pptx?|csv|tsv|json|xml|html?|yaml|yml|"
    "log|png|jpe?g|gif|svg|mp[34]|wav|py|js|ts|tsx|sql|sh|zsh|bash|toml|"
    "cfg|ini|env|key|pem|crt|tar|gz|zip|rar|7z|exe|dll|so|dmg|iso"
)
_FILE_INLINE = re.compile(rf"\b[\w\-]+(?:[._-][\w\-]+)*\.(?:{_KNOWN_EXTENSIONS})\b")


def regex_extract(text: str) -> ExtractedEntities:
    """Extract entities from free-text using regex. Always runs."""
    ibans = sorted({m.group(0) for m in _IBAN_INLINE.finditer(text)})
    emails = sorted({m.group(0) for m in _EMAIL_INLINE.finditer(text)})
    urls = sorted({m.group(0) for m in _URL_INLINE.finditer(text)})
    files: set[str] = set()
    for m in _QUOTED_INLINE.finditer(text):
        files.add(m.group(1))
    for m in _FILE_INLINE.finditer(text):
        if "@" not in m.group(0):
            files.add(m.group(0))
    files_list = sorted(files)
    out = ExtractedEntities(ibans=ibans, emails=emails, urls=urls, files=files_list)
    for x in ibans + emails + urls + files_list:
        out.sources[x] = "regex:user_query"
    return out


# ── LLM extractor ──────────────────────────────────────────────────────────

_LLM_PROMPT = """You are a security-analyst entity extractor. The user will give you a task.
Extract every concrete entity the user EXPLICITLY mentions. Do NOT guess
or invent values. Only list what literally appears in the text.

Output strict JSON in this shape, with [] for missing categories:
{
  "ibans":   ["GB29NWBK60161331926819", ...],
  "emails":  ["alice@example.com", ...],
  "urls":    ["https://example.com/x", ...],
  "phones":  ["+1-555-1234", ...],
  "files":   ["bill-december-2023.txt", ...],
  "contacts":["John Doe", ...]
}
No commentary, no markdown — just JSON."""


def _strip_codefence(s: str) -> str:
    s = s.strip()
    if s.startswith("```"):
        # remove leading fence (``` or ```json) and trailing fence
        first_nl = s.find("\n")
        if first_nl != -1:
            s = s[first_nl + 1:]
        if s.rstrip().endswith("```"):
            s = s.rstrip()[:-3].rstrip()
    return s


def llm_extract(
    text: str,
    *,
    api_key: str,
    base_url: str = "https://open.bigmodel.cn/api/paas/v4/",
    model: str = "glm-4-flash",
    timeout: float = 12.0,
) -> ExtractedEntities | None:
    """Call a cheap LLM to extract user-mentioned entities.

    Returns ``None`` on any failure; callers should fall back to regex.
    Hallucinations are filtered out by re-validating against strict
    regexes — anything the LLM makes up that doesn't match the
    canonical pattern is dropped.
    """
    try:
        import openai
    except ImportError:
        log.warning("dynamic_whitelist: openai SDK not installed")
        return None

    try:
        client = openai.OpenAI(api_key=api_key, base_url=base_url, timeout=timeout)
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": _LLM_PROMPT},
                {"role": "user", "content": text},
            ],
            temperature=0.0,
            max_tokens=400,
        )
        raw = resp.choices[0].message.content or "{}"
        raw = _strip_codefence(raw)
        data = json.loads(raw)
    except Exception as e:
        log.info("dynamic_whitelist: LLM extraction failed: %s", e)
        return None

    def _strs(key: str) -> list[str]:
        v = data.get(key) or []
        return [s for s in v if isinstance(s, str)]

    raw_ibans = _strs("ibans")
    raw_emails = _strs("emails")
    raw_urls = _strs("urls")
    raw_phones = _strs("phones")
    raw_files = _strs("files")
    raw_contacts = _strs("contacts")

    out = ExtractedEntities(
        ibans=[x for x in raw_ibans if _IBAN_RX.match(x)],
        emails=[x for x in raw_emails if _EMAIL_RX.match(x)],
        urls=[x for x in raw_urls if _URL_RX.match(x)],
        phones=[x for x in raw_phones if _PHONE_RX.match(x)],
        files=[x for x in raw_files if _FILENAME_RX.match(x)],
        contacts=[x.strip() for x in raw_contacts if x.strip()],
    )
    for x in out.ibans + out.emails + out.urls + out.phones + out.files + out.contacts:
        out.sources[x] = "llm:user_query"
    return out


# ── Top-level user-query extractor ────────────────────────────────────────

def extract_from_user_query(
    text: str,
    *,
    use_llm: bool = True,
    api_key: str = "",
    base_url: str = "https://open.bigmodel.cn/api/paas/v4/",
    model: str = "glm-4-flash",
) -> ExtractedEntities:
    """Run regex + (optionally) LLM extraction; return their union."""
    rx = regex_extract(text)
    if not use_llm or not api_key:
        return rx
    llm = llm_extract(text, api_key=api_key, base_url=base_url, model=model)
    if llm is None:
        return rx
    return rx.merge(llm)


# ── Env extractor (per-suite knowledge of "user-side" entities) ───────────

def extract_from_env(env: Any, suite_name: str) -> ExtractedEntities:
    """Pull entities from the user's *existing* context.

    This is the equivalent of a banking app reading the user's saved
    payees: it's not task ground-truth, it's user-side knowledge that
    a real production policy *would* have access to.
    """
    out = ExtractedEntities()

    if env is None:
        return out

    if suite_name == "banking":
        ba = getattr(env, "bank_account", None)
        if ba is not None:
            iban = getattr(ba, "iban", None)
            if isinstance(iban, str) and _IBAN_RX.match(iban):
                out.ibans.append(iban)
                out.sources[iban] = "env:bank_account.iban"
            for tx in getattr(ba, "transactions", []) or []:
                rcp = getattr(tx, "recipient", None)
                if isinstance(rcp, str) and _IBAN_RX.match(rcp):
                    if rcp not in out.ibans:
                        out.ibans.append(rcp)
                        out.sources[rcp] = "env:bank.tx.recipient"
            for stx in getattr(ba, "scheduled_transactions", []) or []:
                rcp = getattr(stx, "recipient", None)
                if isinstance(rcp, str) and _IBAN_RX.match(rcp):
                    if rcp not in out.ibans:
                        out.ibans.append(rcp)
                        out.sources[rcp] = "env:bank.scheduled.recipient"
        ua = getattr(env, "user_account", None)
        if ua is not None:
            mail = getattr(ua, "email", None)
            if isinstance(mail, str) and _EMAIL_RX.match(mail):
                out.emails.append(mail)
                out.sources[mail] = "env:user_account.email"

    elif suite_name == "workspace":
        inbox = getattr(env, "inbox", None)
        if inbox is not None:
            for c in getattr(inbox, "contact_list", None) or []:
                mail = getattr(c, "email", None) or (
                    c.get("email") if isinstance(c, dict) else None
                )
                if isinstance(mail, str) and _EMAIL_RX.match(mail):
                    if mail not in out.emails:
                        out.emails.append(mail)
                        out.sources[mail] = "env:inbox.contact_list"
                name = getattr(c, "name", None) or (
                    c.get("name") if isinstance(c, dict) else None
                )
                if isinstance(name, str) and name.strip():
                    if name not in out.contacts:
                        out.contacts.append(name.strip())

    elif suite_name == "slack":
        # Slack env exposes channels / users
        for u in getattr(env, "slack", None).users if getattr(env, "slack", None) else []:
            if isinstance(u, str):
                out.contacts.append(u)
                out.sources[u] = "env:slack.users"

    elif suite_name == "travel":
        # User's own contact info / saved trips — rarely useful for
        # whitelisting; skip for now.
        pass

    out.ibans = sorted(set(out.ibans))
    out.emails = sorted(set(out.emails))
    out.contacts = sorted(set(out.contacts))
    return out


# ── Combined extractor used by the benchmark runner ───────────────────────

def extract_session_entities(
    *,
    user_query: str,
    env: Any,
    suite_name: str,
    api_key: str = "",
    base_url: str = "https://open.bigmodel.cn/api/paas/v4/",
    model: str = "glm-4-flash",
    use_llm: bool = True,
) -> ExtractedEntities:
    """One-shot helper used at task start.

    Combines:
      * user-query extraction (regex + optional LLM)
      * env-side entity extraction (suite-specific)
    """
    a = extract_from_user_query(
        user_query,
        use_llm=use_llm,
        api_key=api_key,
        base_url=base_url,
        model=model,
    )
    b = extract_from_env(env, suite_name)
    return a.merge(b)
