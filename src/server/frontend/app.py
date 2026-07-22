from __future__ import annotations

import json
import mimetypes
import os
import re
from email.utils import formatdate
from html import escape as html_escape
from html.parser import HTMLParser
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import unquote, urljoin, urlparse
from urllib.request import Request, urlopen

# The mock backend is an optional offline-preview helper. Production deployments
# proxy to a real AgentGuard server and do not require it.
try:
    from frontend.mock_backend import MOCK_BACKEND
except ModuleNotFoundError:
    try:
        from mock_backend import MOCK_BACKEND
    except ModuleNotFoundError:
        MOCK_BACKEND = None


BASE_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = BASE_DIR / "templates"
PARTIALS_DIR = TEMPLATES_DIR / "partials"
STATIC_DIR = BASE_DIR / "static"
ASSETS_DIR = BASE_DIR / "assets"
API_BASE_URL = os.environ.get("AGENTGUARD_API_BASE", "http://127.0.0.1:38080").rstrip("/")
BACKEND_API_PREFIX = "v1/backend"
API_KEY = os.environ.get("AGENTGUARD_API_KEY", "").strip()
USE_MOCK_BACKEND = os.environ.get("AGENTGUARD_USE_MOCK", "").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}


def _proxy_timeout_seconds() -> float:
    raw_value = os.environ.get("AGENTGUARD_PROXY_TIMEOUT_SECONDS", "60").strip()
    try:
        value = float(raw_value)
    except ValueError:
        return 60.0
    return max(1.0, value)


PROXY_TIMEOUT_SECONDS = _proxy_timeout_seconds()
STATIC_CACHE_SECONDS = 300
DYNAMIC_CACHE_SECONDS = 60
RULES_PAGE_BUNDLE_PATH = "bundles/rules-page.js"
RULES_PAGE_BUNDLE_FILES = (
    "common/messages.js",
    "common/page-shell.js",
    "common/app.js",
    "common/tool-catalog.js",
    "common/ui-helpers.js",
    "pages/rules/rule-storage.js",
    "pages/rules/rule-dsl.js",
    "pages/rules/rule-parser.js",
    "pages/rules/rule-utils.js",
    "pages/rules/rule-on-clause.js",
    "pages/rules/path-builder.js",
    "pages/rules/condition-builder.js",
    "pages/rules/rule-model.js",
    "pages/rules/rule-validation.js",
    "pages/rules/rule-preview.js",
    "pages/rules/rule-service.js",
    "pages/rules/rule-store.js",
    "pages/rules/rule-form-controller.js",
    "pages/rules/rule-list-controller.js",
    "pages/rules/rules.js",
)
STATIC_BUNDLES = {
    RULES_PAGE_BUNDLE_PATH: RULES_PAGE_BUNDLE_FILES,
}
BUNDLE_SEPARATOR = b"\n;\n"
BUNDLE_CACHE: dict[str, tuple[tuple[object, ...], bytes, float]] = {}

PAGE_ROUTES = {
    "/": "login.html",
    "/index.html": "login.html",
    "/login": "login.html",
    "/login.html": "login.html",
    "/home": "home.html",
    "/home.html": "home.html",
    "/agents": "agents.html",
    "/agents.html": "agents.html",
    "/plugins": "plugins.html",
    "/plugins.html": "plugins.html",
    "/skills": "skills.html",
    "/skills.html": "skills.html",
    "/mcps": "mcps.html",
    "/mcps.html": "mcps.html",
    "/user": "user.html",
    "/user.html": "user.html",
    "/labels": "labels.html",
    "/labels.html": "labels.html",
    "/rules": "rules.html",
    "/rules.html": "rules.html",
    "/runtime": "runtime.html",
    "/runtime.html": "runtime.html",
    "/security-audit": "security-audit.html",
    "/security-audit.html": "security-audit.html",
}

PAGE_TAB_KEYS = {
    "login.html": "",
    "home.html": "home",
    "agents.html": "agents",
    "plugins.html": "plugins",
    "skills.html": "skills",
    "mcps.html": "mcps",
    "user.html": "user",
    "labels.html": "labels",
    "rules.html": "rules",
    "runtime.html": "runtime",
    "security-audit.html": "security_audit",
}

SIDEBAR_TABS = ("home", "agents", "plugins", "skills", "mcps", "user", "labels", "rules", "runtime", "security_audit")
LANGUAGE_COOKIE_NAME = "agentguard.language"
SERVER_LANGUAGE_ATTRIBUTE = "data-agentguard-server-language"
_SUPPORTED_TEMPLATE_LANGUAGES = {"en", "zh"}
_TRANSLATABLE_ATTRIBUTES = {"title", "aria-label", "placeholder"}
_EXCLUDED_TRANSLATION_TAGS = {"script", "style"}
_EXPLICIT_ATTRIBUTE_TRANSLATIONS = {
    "data-i18n-title": "title",
    "data-i18n-aria-label": "aria-label",
}


def _normalize_template_language(language: str | None) -> str:
    normalized = str(language or "").strip().lower()
    return normalized if normalized in _SUPPORTED_TEMPLATE_LANGUAGES else "en"


def _normalize_template_whitespace(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _extract_zh_translations() -> dict[str, str]:
    content = (STATIC_DIR / "common" / "i18n.js").read_text(encoding="utf-8")
    start = content.find("zh: {")
    if start < 0:
        return {}
    brace_start = content.find("{", start)
    if brace_start < 0:
        return {}
    depth = 0
    block_end = -1
    for index in range(brace_start, len(content)):
        char = content[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                block_end = index
                break
    if block_end < 0:
        return {}
    block = content[brace_start + 1:block_end]
    pattern = re.compile(r'^(?:"((?:\\.|[^"\\])*)"|([A-Za-z_][A-Za-z0-9_]*)):\s*"((?:\\.|[^"\\])*)",?$')
    translations: dict[str, str] = {}
    for raw_line in block.splitlines():
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("//"):
            continue
        match = pattern.match(stripped)
        if not match:
            continue
        quoted_key, bare_key, raw_value = match.groups()
        key = json.loads(f'"{quoted_key}"') if quoted_key is not None else str(bare_key)
        value = json.loads(f'"{raw_value}"')
        translations[key] = value
    return translations


EXACT_ZH_TEMPLATE_TRANSLATIONS = _extract_zh_translations()


def _translate_template_key(key: str, language: str) -> str:
    if language != "zh":
        return key
    normalized = _normalize_template_whitespace(key)
    if not normalized:
        return key
    return EXACT_ZH_TEMPLATE_TRANSLATIONS.get(normalized, key)


def _translate_template_value(value: str, language: str) -> str:
    if language != "zh":
        return value
    normalized = _normalize_template_whitespace(value)
    if not normalized:
        return value
    translated = EXACT_ZH_TEMPLATE_TRANSLATIONS.get(normalized)
    if not translated:
        return value
    leading_match = re.match(r"^\s*", value)
    trailing_match = re.search(r"\s*$", value)
    leading = leading_match.group(0) if leading_match else ""
    trailing = trailing_match.group(0) if trailing_match else ""
    return f"{leading}{translated}{trailing}"


class _TemplateLocalizer(HTMLParser):
    def __init__(self, language: str) -> None:
        super().__init__(convert_charrefs=False)
        self.language = _normalize_template_language(language)
        self.parts: list[str] = []
        self._excluded_stack: list[str] = []
        self._explicit_skip_stack: list[str] = []

    def render(self, content: str) -> str:
        self.feed(content)
        self.close()
        return "".join(self.parts)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        lower_tag = tag.lower()
        if self._explicit_skip_stack:
            self._explicit_skip_stack.append(lower_tag)
            return
        self.parts.append(self._format_start_tag(tag, attrs, self_closing=False))
        explicit_mode, explicit_key = self._explicit_binding(attrs)
        if explicit_mode and explicit_key is not None:
            translated = _translate_template_key(explicit_key, self.language)
            if explicit_mode == "html":
                self.parts.append(translated)
            else:
                self.parts.append(html_escape(translated))
            self._explicit_skip_stack.append(lower_tag)
            return
        if lower_tag in _EXCLUDED_TRANSLATION_TAGS:
            self._excluded_stack.append(lower_tag)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self._explicit_skip_stack:
            return
        self.parts.append(self._format_start_tag(tag, attrs, self_closing=True))

    def handle_endtag(self, tag: str) -> None:
        lower_tag = tag.lower()
        if self._explicit_skip_stack:
            if self._explicit_skip_stack[-1] == lower_tag:
                self._explicit_skip_stack.pop()
                if not self._explicit_skip_stack:
                    self.parts.append(f"</{tag}>")
            return
        self.parts.append(f"</{tag}>")
        if self._excluded_stack and self._excluded_stack[-1] == lower_tag:
            self._excluded_stack.pop()

    def handle_data(self, data: str) -> None:
        if self._excluded_stack or self._explicit_skip_stack:
            if self._excluded_stack:
                self.parts.append(data)
            return
        self.parts.append(_translate_template_value(data, self.language))

    def handle_comment(self, data: str) -> None:
        if self._explicit_skip_stack:
            return
        self.parts.append(f"<!--{data}-->")

    def handle_decl(self, decl: str) -> None:
        if self._explicit_skip_stack:
            return
        self.parts.append(f"<!{decl}>")

    def handle_entityref(self, name: str) -> None:
        if self._explicit_skip_stack:
            return
        self.parts.append(f"&{name};")

    def handle_charref(self, name: str) -> None:
        if self._explicit_skip_stack:
            return
        self.parts.append(f"&#{name};")

    def handle_pi(self, data: str) -> None:
        if self._explicit_skip_stack:
            return
        self.parts.append(f"<?{data}>")

    def unknown_decl(self, data: str) -> None:
        if self._explicit_skip_stack:
            return
        self.parts.append(f"<![{data}]>")

    def _explicit_binding(self, attrs: list[tuple[str, str | None]]) -> tuple[str, str | None]:
        if self.language != "zh" or self._excluded_stack:
            return "", None
        attr_map = {name: value for name, value in attrs if value is not None}
        if attr_map.get("data-i18n-html"):
            return "html", str(attr_map["data-i18n-html"])
        if attr_map.get("data-i18n-value"):
            return "text", str(attr_map["data-i18n-value"])
        if attr_map.get("data-i18n"):
            return "text", str(attr_map["data-i18n"])
        return "", None

    def _format_start_tag(self, tag: str, attrs: list[tuple[str, str | None]], *, self_closing: bool) -> str:
        serialized_attrs: list[str] = []
        excluded = bool(self._excluded_stack) or tag.lower() in _EXCLUDED_TRANSLATION_TAGS
        attr_map = {name: value for name, value in attrs if value is not None}
        seen_names: set[str] = set()
        for name, value in attrs:
            if value is None:
                serialized_attrs.append(name)
                seen_names.add(name)
                continue
            next_value = value
            if not excluded and self.language == "zh":
                explicit_key = None
                for binding_name, target_name in _EXPLICIT_ATTRIBUTE_TRANSLATIONS.items():
                    if target_name == name and attr_map.get(binding_name):
                        explicit_key = str(attr_map[binding_name])
                        break
                if explicit_key is not None:
                    next_value = _translate_template_key(explicit_key, self.language)
                elif name in _TRANSLATABLE_ATTRIBUTES:
                    next_value = _translate_template_value(value, self.language).strip() or value
            serialized_attrs.append(f'{name}="{html_escape(next_value, quote=True)}"')
            seen_names.add(name)
        if not excluded and self.language == "zh":
            for binding_name, target_name in _EXPLICIT_ATTRIBUTE_TRANSLATIONS.items():
                binding_value = attr_map.get(binding_name)
                if binding_value and target_name not in seen_names:
                    translated = _translate_template_key(str(binding_value), self.language)
                    serialized_attrs.append(f'{target_name}="{html_escape(translated, quote=True)}"')
        joined_attrs = f" {' '.join(serialized_attrs)}" if serialized_attrs else ""
        suffix = " />" if self_closing else ">"
        return f"<{tag}{joined_attrs}{suffix}"


def _inject_server_language(content: str, language: str) -> str:
    normalized = _normalize_template_language(language)
    html_lang = "zh-CN" if normalized == "zh" else "en"

    def repl(match: re.Match[str]) -> str:
        attrs = match.group(1) or ""
        attrs = re.sub(r'\s+lang="[^"]*"', "", attrs)
        attrs = re.sub(rf'\s+{SERVER_LANGUAGE_ATTRIBUTE}="[^"]*"', "", attrs)
        return f'<html{attrs} lang="{html_lang}" {SERVER_LANGUAGE_ATTRIBUTE}="{normalized}">'

    return re.sub(r"<html([^>]*)>", repl, content, count=1)


def _localize_template_content(content: str, language: str) -> str:
    normalized = _normalize_template_language(language)
    if normalized == "zh":
        content = _TemplateLocalizer(normalized).render(content)
    return _inject_server_language(content, normalized)


class FrontendPreviewHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        query = parsed.query

        if self._maybe_handle_mock("GET", path, query):
            return

        if path == "/api/tools":
            self._proxy("tools", method="GET", query=query)
            return

        if path == "/api/skills":
            self._proxy("skills", method="GET", query=query)
            return

        if path == "/api/mcps":
            self._proxy("mcps", method="GET", query=query)
            return

        if path == "/api/rules":
            self._proxy("rules", method="GET", query=query)
            return

        if path == "/api/health":
            self._proxy("health", method="GET", query=query)
            return

        if path == "/api/stats":
            self._proxy("stats", method="GET", query=query)
            return

        if path == "/api/traffic":
            self._proxy("traffic", method="GET", query=query)
            return

        if path == "/api/audit/recent":
            self._proxy("audit/recent", method="GET", query=query)
            return

        if path == "/api/approvals":
            self._proxy("approvals", method="GET", query=query)
            return

        if path == "/api/user/me":
            self._proxy("v1/user/me", method="GET", query=query)
            return

        if path == "/api/user/tickets":
            self._proxy("v1/user/tickets", method="GET", query=query)
            return

        if path == "/api/user/external-accounts":
            self._proxy("v1/user/external-accounts", method="GET", query=query)
            return

        if path == "/api/user/openclaw-bindings":
            self._proxy("v1/user/openclaw-bindings", method="GET", query=query)
            return

        if path in {
            "/api/user/organizations",
            "/api/user/groups",
        }:
            self._proxy(f"v1/user/{path.removeprefix('/api/user/')}", method="GET", query=query)
            return

        if (
            re.fullmatch(r"/api/user/organizations/\d+", path)
            or re.fullmatch(r"/api/user/organizations/\d+/groups", path)
            or re.fullmatch(r"/api/user/organizations/\d+/members", path)
            or re.fullmatch(r"/api/user/groups/\d+", path)
            or re.fullmatch(r"/api/user/groups/\d+/members", path)
        ):
            self._proxy(f"v1/user/{path.removeprefix('/api/user/')}", method="GET", query=query)
            return

        if path == "/api/security-audits" or path.startswith("/api/security-audits/"):
            upstream_path = path.removeprefix("/api/")
            self._proxy(upstream_path, method="GET", query=query)
            return

        if path == "/api/agents":
            self._proxy("agents", method="GET", query=query)
            return

        if path.startswith("/api/agents/") and "/runtime/" in path:
            upstream_path = path.removeprefix("/api/")
            self._proxy(upstream_path, method="GET", query=query)
            return

        if path.startswith("/api/agents/") and path.endswith("/plugins/config"):
            upstream_path = path.removeprefix("/api/")
            self._proxy(upstream_path, method="GET", query=query)
            return

        if path.startswith("/api/agents/") and path.endswith("/plugins/available"):
            upstream_path = path.removeprefix("/api/")
            self._proxy(upstream_path, method="GET", query=query)
            return

        if path.startswith("/api/agents/") and path.endswith("/rules"):
            upstream_path = path.removeprefix("/api/")
            self._proxy(upstream_path, method="GET", query=query)
            return

        if path.startswith("/api/agents/") and path.endswith("/skills"):
            upstream_path = path.removeprefix("/api/")
            self._proxy(upstream_path, method="GET", query=query)
            return

        if path.startswith("/api/agents/") and path.endswith("/mcps"):
            upstream_path = path.removeprefix("/api/")
            self._proxy(upstream_path, method="GET", query=query)
            return

        if path.startswith("/api/agents/") and "/tools" in path:
            upstream_path = path.removeprefix("/api/")
            self._proxy(upstream_path, method="GET", query=query)
            return

        if path.startswith("/assets/"):
            self._serve_asset(path)
            return

        if path.startswith("/static/"):
            self._serve_static(path)
            return

        page_name = PAGE_ROUTES.get(path)
        if page_name is None:
            self.send_error(HTTPStatus.NOT_FOUND, "Not Found")
            return

        self._serve_template(page_name)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        query = parsed.query

        if self._maybe_handle_mock("POST", path, query):
            return

        if path == "/api/rules/check":
            self._proxy("rules/check", method="POST", query=query)
            return

        if path == "/api/rules/reload":
            self._proxy("rules/reload", method="POST", query=query)
            return

        if path == "/api/plugins/config":
            self._proxy("plugins/config", method="POST", query=query)
            return

        if path.startswith("/api/agents/") and path.endswith("/plugins/config"):
            upstream_path = path.removeprefix("/api/")
            self._proxy(upstream_path, method="POST", query=query)
            return

        if path.startswith("/api/agents/") and path.endswith("/rules"):
            upstream_path = path.removeprefix("/api/")
            self._proxy(upstream_path, method="POST", query=query)
            return

        if path.startswith("/api/agents/") and path.endswith("/rules/generate"):
            upstream_path = path.removeprefix("/api/")
            self._proxy(upstream_path, method="POST", query=query)
            return

        if path.startswith("/api/agents/") and path.endswith("/skills/detect"):
            upstream_path = path.removeprefix("/api/")
            self._proxy(upstream_path, method="POST", query=query)
            return

        if path.startswith("/api/agents/") and path.endswith("/mcps/detect"):
            upstream_path = path.removeprefix("/api/")
            self._proxy(upstream_path, method="POST", query=query)
            return

        if path.startswith("/api/agents/") and "/runtime/" in path:
            upstream_path = path.removeprefix("/api/")
            self._proxy(upstream_path, method="POST", query=query)
            return

        if path.startswith("/api/approvals/") and (
            path.endswith("/approve") or path.endswith("/deny")
        ):
            upstream_path = path.removeprefix("/api/")
            self._proxy(upstream_path, method="POST", query=query)
            return

        if path in {
            "/api/user/register/email-code",
            "/api/user/register",
            "/api/user/login",
            "/api/user/logout",
            "/api/user/password",
            "/api/user/tickets",
            "/api/user/external-accounts",
        }:
            self._proxy(f"v1/user/{path.removeprefix('/api/user/')}", method="POST", query=query)
            return

        if path == "/api/user/dify/bind":
            self._proxy("v1/user/dify/bind", method="POST", query=query)
            return

        if path in {
            "/api/user/organizations",
            "/api/user/invitations/accept",
        }:
            self._proxy(f"v1/user/{path.removeprefix('/api/user/')}", method="POST", query=query)
            return

        if (
            re.fullmatch(r"/api/user/organizations/\d+/groups", path)
            or re.fullmatch(r"/api/user/groups/\d+/invitations", path)
        ):
            self._proxy(f"v1/user/{path.removeprefix('/api/user/')}", method="POST", query=query)
            return

        if path == "/api/security-audits":
            self._proxy("security-audits", method="POST", query=query)
            return

        self.send_error(HTTPStatus.NOT_FOUND, "Not Found")

    def do_DELETE(self) -> None:
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        query = parsed.query

        if self._maybe_handle_mock("DELETE", path, query):
            return

        if path.startswith("/api/agents/") and "/rules/" in path:
            upstream_path = path.removeprefix("/api/")
            self._proxy(upstream_path, method="DELETE", query=query)
            return

        if path.startswith("/api/agents/") and path.count("/") == 3:
            upstream_path = path.removeprefix("/api/")
            self._proxy(upstream_path, method="DELETE", query=query)
            return

        if path.startswith("/api/user/external-accounts/"):
            mapping_id = path.rsplit("/", 1)[-1]
            self._proxy(
                f"v1/user/external-accounts/{mapping_id}",
                method="DELETE",
                query=query,
            )
            return

        if path == "/api/user/openclaw-bindings":
            self._proxy(
                "v1/user/openclaw-bindings",
                method="DELETE",
                query=query,
            )
            return

        if (
            re.fullmatch(r"/api/user/organizations/\d+", path)
            or re.fullmatch(r"/api/user/groups/\d+", path)
        ):
            self._proxy(f"v1/user/{path.removeprefix('/api/user/')}", method="DELETE", query=query)
            return

        if path.startswith("/api/user/openclaw-bindings/"):
            agent_id = path.rsplit("/", 1)[-1]
            self._proxy(
                f"v1/user/openclaw-bindings/{agent_id}",
                method="DELETE",
                query=query,
            )
            return

        self.send_error(HTTPStatus.NOT_FOUND, "Not Found")

    def do_PATCH(self) -> None:
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        query = parsed.query

        if self._maybe_handle_mock("PATCH", path, query):
            return

        if path.startswith("/api/agents/") and path.endswith("/labels"):
            upstream_path = path.removeprefix("/api/")
            self._proxy(upstream_path, method="PATCH", query=query)
            return

        if (
            path == "/api/user/me"
            or re.fullmatch(r"/api/user/organizations/\d+", path)
            or re.fullmatch(r"/api/user/groups/\d+", path)
        ):
            self._proxy(f"v1/user/{path.removeprefix('/api/user/')}", method="PATCH", query=query)
            return

        self.send_error(HTTPStatus.NOT_FOUND, "Not Found")

    def _maybe_handle_mock(self, method: str, path: str, query: str) -> bool:
        if not USE_MOCK_BACKEND or MOCK_BACKEND is None:
            return False
        if not path.startswith("/api/"):
            return False
        return MOCK_BACKEND.try_handle(self, method=method, path=path, query=query)

    def _serve_static(self, request_path: str) -> None:
        relative_path = request_path.removeprefix("/static/")

        if relative_path in STATIC_BUNDLES:
            self._serve_bundle(relative_path)
            return

        file_path = (STATIC_DIR / relative_path).resolve()

        if not self._is_safe_path(file_path, STATIC_DIR):
            self.send_error(HTTPStatus.FORBIDDEN, "Forbidden")
            return

        if not file_path.is_file():
            self.send_error(HTTPStatus.NOT_FOUND, "Not Found")
            return

        if relative_path == "common/app.js":
            body = self._app_config_prefix() + file_path.read_bytes()
            last_modified = self._http_date(file_path.stat().st_mtime)
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/javascript; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header(
                "Cache-Control",
                f"public, max-age={DYNAMIC_CACHE_SECONDS}, must-revalidate",
            )
            self.send_header("Last-Modified", last_modified)
            self.end_headers()
            self.wfile.write(body)
            return

        mime_type, _ = mimetypes.guess_type(file_path.name)
        self._serve_file(file_path, mime_type or "application/octet-stream")

    def _serve_bundle(self, relative_path: str) -> None:
        sources = STATIC_BUNDLES.get(relative_path)
        if not sources:
            self.send_error(HTTPStatus.NOT_FOUND, "Not Found")
            return

        resolved_sources: list[tuple[str, Path]] = []
        source_states: list[tuple[str, int]] = []
        latest_mtime = 0.0
        for source in sources:
            file_path = (STATIC_DIR / source).resolve()
            if not self._is_safe_path(file_path, STATIC_DIR):
                self.send_error(HTTPStatus.FORBIDDEN, "Forbidden")
                return
            if not file_path.is_file():
                self.send_error(HTTPStatus.NOT_FOUND, "Not Found")
                return
            stat = file_path.stat()
            latest_mtime = max(latest_mtime, stat.st_mtime)
            source_states.append((source, stat.st_mtime_ns))
            resolved_sources.append((source, file_path))

        cache_key = (API_BASE_URL, tuple(source_states))
        cached = BUNDLE_CACHE.get(relative_path)
        if cached and cached[0] == cache_key:
            body = cached[1]
            latest_mtime = cached[2]
        else:
            parts: list[bytes] = []
            for source, file_path in resolved_sources:
                parts.append(f"/* {source} */\n".encode("utf-8"))
                if source == "common/app.js":
                    parts.append(self._app_config_prefix())
                parts.append(file_path.read_bytes())
                parts.append(BUNDLE_SEPARATOR)
            body = b"".join(parts)
            BUNDLE_CACHE[relative_path] = (cache_key, body, latest_mtime)

        try:
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/javascript; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header(
                "Cache-Control",
                f"public, max-age={DYNAMIC_CACHE_SECONDS}, must-revalidate",
            )
            self.send_header("Last-Modified", self._http_date(latest_mtime))
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
            return

    def _serve_asset(self, request_path: str) -> None:
        relative_path = request_path.removeprefix("/assets/")
        file_path = (ASSETS_DIR / relative_path).resolve()

        if not self._is_safe_path(file_path, ASSETS_DIR):
            self.send_error(HTTPStatus.FORBIDDEN, "Forbidden")
            return

        if not file_path.is_file():
            self.send_error(HTTPStatus.NOT_FOUND, "Not Found")
            return

        mime_type, _ = mimetypes.guess_type(file_path.name)
        self._serve_file(file_path, mime_type or "application/octet-stream")

    def _serve_file(self, path: Path, content_type: str) -> None:
        if not path.is_file():
            self.send_error(HTTPStatus.NOT_FOUND, "Not Found")
            return

        body = path.read_bytes()
        last_modified = self._http_date(path.stat().st_mtime)
        try:
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", f"public, max-age={STATIC_CACHE_SECONDS}")
            self.send_header("Last-Modified", last_modified)
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
            return

    def _serve_template(self, page_name: str) -> None:
        path = TEMPLATES_DIR / page_name
        if not path.is_file():
            self.send_error(HTTPStatus.NOT_FOUND, "Not Found")
            return

        body = self._render_template(page_name).encode("utf-8")
        last_modified = self._http_date(path.stat().st_mtime)
        try:
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Last-Modified", last_modified)
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
            return

    def _render_template(self, page_name: str) -> str:
        content = (TEMPLATES_DIR / page_name).read_text(encoding="utf-8")
        tab_key = PAGE_TAB_KEYS.get(page_name, "")
        replacements = {
            "{{ shared:sidebar }}": self._render_sidebar(tab_key),
        }
        for placeholder, value in replacements.items():
            content = content.replace(placeholder, value)
        return _localize_template_content(content, self._request_language())

    @staticmethod
    def _render_sidebar(active_tab: str) -> str:
        content = (PARTIALS_DIR / "sidebar.html").read_text(encoding="utf-8")
        for tab_name in SIDEBAR_TABS:
            active_class = " active" if tab_name == active_tab else ""
            content = content.replace(f"{{{{ {tab_name}_active }}}}", active_class)
        return content

    def _request_language(self) -> str:
        cookie_header = self.headers.get("Cookie", "")
        if not cookie_header:
            return "en"
        jar = SimpleCookie()
        try:
            jar.load(cookie_header)
        except Exception:
            return "en"
        morsel = jar.get(LANGUAGE_COOKIE_NAME)
        return _normalize_template_language(morsel.value if morsel else "en")

    def _proxy(self, upstream_path: str, *, method: str, query: str = "") -> None:
        target_url = urljoin(f"{API_BASE_URL}/", self._backend_upstream_path(upstream_path))
        if query:
            target_url = f"{target_url}?{query}"
        body = self._read_request_body() if method in ("POST", "PUT", "PATCH", "DELETE") else None
        headers = {
            "Accept": "application/json",
        }
        if body is not None:
            headers["Content-Type"] = self.headers.get(
                "Content-Type", "application/json; charset=utf-8"
            )
        if API_KEY:
            headers["X-Api-Key"] = API_KEY
        cookie = self.headers.get("Cookie")
        if cookie:
            headers["Cookie"] = cookie

        request = Request(target_url, data=body, headers=headers, method=method)

        try:
            with urlopen(request, timeout=PROXY_TIMEOUT_SECONDS) as response:
                upstream_body = response.read()
                content_type = response.headers.get(
                    "Content-Type", "application/json; charset=utf-8"
                )
                set_cookie_headers = response.headers.get_all("Set-Cookie") or []
        except HTTPError as exc:
            upstream_body = exc.read()
            content_type = exc.headers.get("Content-Type", "application/json; charset=utf-8")
            self.send_response(exc.code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(upstream_body)))
            for value in exc.headers.get_all("Set-Cookie") or []:
                self.send_header("Set-Cookie", value)
            self.end_headers()
            self.wfile.write(upstream_body)
            return
        except URLError as exc:
            self._send_json(
                {"ok": False, "error": f"cannot reach AgentGuard API: {exc.reason}"},
                status=HTTPStatus.BAD_GATEWAY,
            )
            return

        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(upstream_body)))
        for value in set_cookie_headers:
            self.send_header("Set-Cookie", value)
        self.end_headers()
        self.wfile.write(upstream_body)

    @staticmethod
    def _backend_upstream_path(upstream_path: str) -> str:
        normalized = upstream_path.strip("/")
        if normalized.startswith("v1/"):
            return normalized
        return f"{BACKEND_API_PREFIX}/{normalized}"

    def _read_request_body(self) -> bytes | None:
        raw_length = self.headers.get("Content-Length")
        if not raw_length:
            return None
        try:
            length = int(raw_length)
        except ValueError:
            return None
        if length <= 0:
            return None
        return self.rfile.read(length)

    @staticmethod
    def _read_http_error(exc: HTTPError) -> str:
        try:
            body = exc.read()
        except Exception:
            return ""
        if not body:
            return ""
        try:
            payload: Any = json.loads(body.decode("utf-8"))
        except Exception:
            return body.decode("utf-8", errors="replace").strip()
        if isinstance(payload, dict):
            detail = payload.get("detail") or payload.get("error")
            if detail:
                return str(detail)
        return str(payload)

    def _send_json(self, payload: dict[str, object], *, status: HTTPStatus) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    @staticmethod
    def _app_config_prefix() -> bytes:
        return (
            f"window.AgentGuardConfig = "
            f"{json.dumps({'apiBase': API_BASE_URL}, ensure_ascii=False)};\n"
        ).encode("utf-8")

    @staticmethod
    def _http_date(timestamp: float) -> str:
        return formatdate(timestamp, usegmt=True)

    @staticmethod
    def _is_safe_path(candidate: Path, parent: Path) -> bool:
        try:
            candidate.relative_to(parent.resolve())
        except ValueError:
            return False
        return True

    def log_message(self, format: str, *args: object) -> None:
        return


def serve(host: str | None = None, port: int | None = None) -> None:
    h = host or os.environ.get("FRONTEND_HOST", "0.0.0.0")
    p = port or int(os.environ.get("FRONTEND_PORT", "38008"))
    server = ThreadingHTTPServer((h, p), FrontendPreviewHandler)
    print(f"AgentGuard frontend  http://{h}:{p}")
    if USE_MOCK_BACKEND:
        print("Mocking agent/tool/skill/rule frontend API routes from frontend.mock_backend")
    else:
        print(f"Proxying /api/tools to {API_BASE_URL}/v1/backend/tools")
        print(f"Proxying /api/skills to {API_BASE_URL}/v1/backend/skills")
        print(f"Proxying /api/mcps to {API_BASE_URL}/v1/backend/mcps")
        print(f"Proxying /api/rules to {API_BASE_URL}/v1/backend/rules")
        print(f"Proxying /api/rules/reload to {API_BASE_URL}/v1/backend/rules/reload")
        print("Proxying /api/agents/{agent_id}/rules to agent-scoped rule endpoints")
        print("Proxying /api/agents/{agent_id}/skills to agent-scoped skill endpoints")
        print("Proxying /api/agents/{agent_id}/skills/detect to skill detect endpoint")
        print("Proxying /api/agents/{agent_id}/mcps to agent-scoped MCP endpoints")
        print("Proxying /api/agents/{agent_id}/mcps/detect to MCP detect endpoint")
        print("Proxying /api/agents/{agent_id}/plugins/config to agent-scoped plugin endpoints")
        print("Proxying /api/agents/{agent_id}/plugins/available to agent-scoped plugin catalog endpoints")
        print("Proxying /api/agents/{agent_id}/tools/{tool_name}/labels to tool-label patch endpoint")
        print(f"Proxying /api/health to {API_BASE_URL}/v1/backend/health")
        print(f"Proxying /api/stats to {API_BASE_URL}/v1/backend/stats")
        print(f"Proxying /api/traffic to {API_BASE_URL}/v1/backend/traffic")
        print(f"Proxying /api/audit/recent to {API_BASE_URL}/v1/backend/audit/recent")
        print(f"Proxying /api/approvals to {API_BASE_URL}/v1/backend/approvals")
        print(f"Proxying /api/plugins/config to {API_BASE_URL}/v1/backend/plugins/config")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    serve()
