"""Load skill packages from the input directory.

The loader only performs safe, bounded file reads and does not make risk judgments. Its core goals are:
- support common input shapes such as directories, zip files, and tar.gz archives;
- skip binary files and oversized files;
- record broken manifests, broken archives, or permission errors instead of raising.
"""

from __future__ import annotations

import json
import os
import tarfile
import zipfile
from collections.abc import Iterable, Iterator
from pathlib import Path, PurePosixPath
from typing import Any

from .models import SkillPackage, TextFile

DEFAULT_INPUT_DIR = Path("/data/skills")
INPUT_ENV = "SKILLSEC_INPUT_DIR"
MAX_FILE_BYTES = 32 * 1024 * 1024
MAX_SKILL_BYTES = 128 * 1024 * 1024
MAX_SKILL_FILES = 1500
MAX_SKILL_DIRS = 500
MAX_ARCHIVE_ENTRIES = 1500
# Only scan text-like source/config/documentation files. Unknown binaries are both slow and noisy.
TEXT_EXTENSIONS = {
    ".bat",
    ".c",
    ".cjs",
    ".cfg",
    ".cmd",
    ".conf",
    ".cpp",
    ".cs",
    ".css",
    ".go",
    ".h",
    ".hpp",
    ".html",
    ".ini",
    ".java",
    ".js",
    ".jsx",
    ".json",
    ".kts",
    ".kt",
    ".lock",
    ".lua",
    ".md",
    ".mjs",
    ".php",
    ".pl",
    ".ps1",
    ".py",
    ".rb",
    ".rs",
    ".sh",
    ".sql",
    ".swift",
    ".tf",
    ".tfvars",
    ".txt",
    ".ts",
    ".tsx",
    ".toml",
    ".xml",
    ".yaml",
    ".yml",
}
TEXT_FILE_NAMES = {
    ".dockerignore",
    ".editorconfig",
    ".env",
    ".gitignore",
    "Dockerfile",
    "LICENSE",
    "Makefile",
    "package-lock.json",
    "package.json",
    "requirements.txt",
}


def input_dir_from_env() -> Path:
    return Path(os.environ.get(INPUT_ENV, str(DEFAULT_INPUT_DIR)))


def load_skills(input_dir: Path | None = None) -> list[SkillPackage]:
    # Keep the legacy batch API because tests and ad-hoc scripts may still use it; the main flow uses `iter_skills` for streaming scans.
    return list(iter_skills(input_dir))


def iter_skills(input_dir: Path | None = None) -> Iterator[SkillPackage]:
    root = input_dir or input_dir_from_env()
    if not root.exists() or not root.is_dir():
        return
    for path in _safe_iterdir(root):
        try:
            # Benchmark runs usually provide `/data/skills/{skill_id}/`, but local benches may place archives directly in the input directory.
            if path.is_dir():
                yield load_skill(path)
            elif path.is_file() and _is_zip_archive(path):
                yield load_skill_zip_archive(path)
            elif path.is_file() and _is_tar_archive(path):
                yield load_skill_tar_archive(path)
        except OSError as exc:
            # Even if one entry cannot be read, still emit a package so later stages can turn it into a suspicious/uncertain result.
            package = SkillPackage(skill_id=_archive_skill_id(path), root=path)
            package.load_errors.append(f"failed to load skill entry: {exc}")
            yield package


def load_skill(root: Path) -> SkillPackage:
    package = SkillPackage(skill_id=root.name, root=root)
    package.manifest = _load_manifest(root / "manifest.json", package.load_errors)
    total_size = 0

    for path in _safe_walk(root, package.load_errors):
        try:
            if not path.is_file() or path.is_symlink():
                continue
            size = path.stat().st_size
            if size > MAX_FILE_BYTES:
                package.load_errors.append(f"skipped large file: {_rel(root, path)}")
                continue
            if total_size + size > MAX_SKILL_BYTES:
                # Cap total bytes read per skill so stress samples or misplaced repositories do not make scanning unbounded.
                package.load_errors.append("stopped reading after skill size limit")
                break
            if not _looks_text(path):
                continue
            raw = path.read_bytes()
            if _looks_binary(raw):
                continue
            content = raw.decode("utf-8", errors="replace")
            package.files.append(TextFile(path=_rel(root, path), content=content, size=size))
            total_size += size
        except OSError as exc:
            package.load_errors.append(f"failed to read {_rel(root, path)}: {exc}")

    return package


def load_skill_zip_archive(path: Path) -> SkillPackage:
    package = SkillPackage(skill_id=_archive_skill_id(path), root=path)
    total_size = 0

    try:
        with zipfile.ZipFile(path) as archive:
            # `zipfile.infolist()` also lists directories; the later bounded scan still enforces entry-count and total-byte limits.
            infos = [info for info in _bounded_archive_infos(archive.infolist(), package.load_errors) if not info.is_dir()]
            prefix = _archive_common_prefix(info.filename for info in infos)
            package.manifest = _load_archive_manifest(archive, infos, prefix, package.load_errors)

            for info in sorted(infos, key=lambda item: item.filename):
                rel = _archive_rel(info.filename, prefix)
                if not rel or _archive_path_ignored(rel) or _archive_is_symlink(info):
                    continue
                size = info.file_size
                if size > MAX_FILE_BYTES:
                    package.load_errors.append(f"skipped large file: {rel}")
                    continue
                if total_size + size > MAX_SKILL_BYTES:
                    package.load_errors.append("stopped reading after skill size limit")
                    break
                if not _looks_text(PurePosixPath(rel)):
                    continue
                try:
                    raw = archive.read(info)
                except (OSError, RuntimeError, zipfile.BadZipFile) as exc:
                    package.load_errors.append(f"failed to read {rel}: {exc}")
                    continue
                if _looks_binary(raw):
                    continue
                package.files.append(TextFile(path=rel, content=raw.decode("utf-8", errors="replace"), size=size))
                total_size += size
    except (OSError, zipfile.BadZipFile) as exc:
        package.load_errors.append(f"failed to read zip archive: {exc}")

    return package


def load_skill_archive(path: Path) -> SkillPackage:
    # Keep the old zip-only function name for compatibility; new code should call the zip/tar-specific loaders directly.
    return load_skill_zip_archive(path)


def load_skill_tar_archive(path: Path) -> SkillPackage:
    package = SkillPackage(skill_id=_archive_skill_id(path), root=path)
    total_size = 0

    try:
        with tarfile.open(path) as archive:
            # Read tar members through the iterator so exceptionally large archives are never unpacked to disk all at once.
            members = [member for member in _bounded_tar_members(archive, package.load_errors) if member.isfile()]
            prefix = _archive_common_prefix(member.name for member in members)
            package.manifest = _load_tar_manifest(archive, members, prefix, package.load_errors)

            for member in sorted(members, key=lambda item: item.name):
                rel = _archive_rel(member.name, prefix)
                if not rel or _archive_path_ignored(rel):
                    continue
                size = member.size
                if size > MAX_FILE_BYTES:
                    package.load_errors.append(f"skipped large file: {rel}")
                    continue
                if total_size + size > MAX_SKILL_BYTES:
                    package.load_errors.append("stopped reading after skill size limit")
                    break
                if not _looks_text(PurePosixPath(rel)):
                    continue
                try:
                    extracted = archive.extractfile(member)
                    if extracted is None:
                        continue
                    raw = extracted.read()
                except (OSError, tarfile.TarError) as exc:
                    package.load_errors.append(f"failed to read {rel}: {exc}")
                    continue
                if _looks_binary(raw):
                    continue
                package.files.append(TextFile(path=rel, content=raw.decode("utf-8", errors="replace"), size=size))
                total_size += size
    except (OSError, tarfile.TarError) as exc:
        package.load_errors.append(f"failed to read tar archive: {exc}")

    return package


def _load_manifest(path: Path, errors: list[str]) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, json.JSONDecodeError) as exc:
        errors.append(f"failed to parse manifest.json: {exc}")
        return {}


def _load_archive_manifest(
    archive: zipfile.ZipFile, infos: list[zipfile.ZipInfo], prefix: str, errors: list[str]
) -> dict[str, Any]:
    for info in infos:
        if _archive_rel(info.filename, prefix) != "manifest.json":
            continue
        try:
            raw = archive.read(info)
            return json.loads(raw.decode("utf-8", errors="replace"))
        except (OSError, RuntimeError, zipfile.BadZipFile, json.JSONDecodeError) as exc:
            errors.append(f"failed to parse manifest.json: {exc}")
            return {}
    return {}


def _load_tar_manifest(
    archive: tarfile.TarFile, members: list[tarfile.TarInfo], prefix: str, errors: list[str]
) -> dict[str, Any]:
    for member in members:
        if _archive_rel(member.name, prefix) != "manifest.json":
            continue
        try:
            extracted = archive.extractfile(member)
            if extracted is None:
                return {}
            raw = extracted.read()
            return json.loads(raw.decode("utf-8", errors="replace"))
        except (OSError, tarfile.TarError, json.JSONDecodeError) as exc:
            errors.append(f"failed to parse manifest.json: {exc}")
            return {}
    return {}


def _safe_iterdir(root: Path) -> list[Path]:
    try:
        return sorted(root.iterdir())
    except OSError:
        return []


def _safe_walk(root: Path, errors: list[str]):
    files_seen = 0
    dirs_seen = 0

    def on_error(exc: OSError) -> None:
        errors.append(f"failed to walk skill directory: {exc}")

    for current, dirnames, filenames in os.walk(root, topdown=True, onerror=on_error, followlinks=False):
        dirs_seen += 1
        if dirs_seen > MAX_SKILL_DIRS:
            errors.append("stopped walking after skill directory count limit")
            dirnames[:] = []
            return

        current_path = Path(current)
        # Do not follow symlinks so a skill cannot escape outside the input directory through links.
        dirnames[:] = sorted(name for name in dirnames if not (current_path / name).is_symlink())[:MAX_SKILL_DIRS]
        for filename in sorted(filenames)[: max(0, MAX_SKILL_FILES - files_seen)]:
            files_seen += 1
            yield current_path / filename
        if files_seen >= MAX_SKILL_FILES:
            errors.append("stopped walking after skill file count limit")
            return


def _looks_text(path: Path | PurePosixPath) -> bool:
    return path.suffix.lower() in TEXT_EXTENSIONS or path.name in TEXT_FILE_NAMES


def _looks_binary(raw: bytes) -> bool:
    sample = raw[:1024]
    # A simple NUL-byte heuristic is sufficient for most binaries; UTF-8 decoding still uses `replace` as a fallback.
    return b"\x00" in sample


def _rel(root: Path, path: Path) -> str:
    try:
        return PurePosixPath(path.relative_to(root)).as_posix()
    except ValueError:
        return PurePosixPath(path).as_posix()


def _archive_common_prefix(names) -> str:
    # Many submissions look like `skill_id/SKILL.md`; after stripping the single top-level directory, keep the inner paths unchanged.
    top_levels = set()
    has_root_file = False
    for name in names:
        parts = _safe_archive_parts(name)
        if not parts:
            continue
        if len(parts) == 1:
            has_root_file = True
        else:
            top_levels.add(parts[0])
    if not has_root_file and len(top_levels) == 1:
        return next(iter(top_levels))
    return ""


def _archive_rel(name: str, prefix: str) -> str:
    parts = _safe_archive_parts(name)
    if prefix and parts[:1] == [prefix]:
        parts = parts[1:]
    return "/".join(parts)


def _safe_archive_parts(name: str) -> list[str]:
    # Archive paths are treated only as virtual relative paths, with absolute paths and `..` traversal rejected explicitly.
    path = PurePosixPath(name)
    if path.is_absolute():
        return []
    parts = [part for part in path.parts if part not in ("", ".")]
    if any(part == ".." for part in parts):
        return []
    return parts


def _archive_path_ignored(path: str) -> bool:
    return path.startswith("__MACOSX/") or "/__MACOSX/" in path or path.endswith(".DS_Store")


def _archive_is_symlink(info: zipfile.ZipInfo) -> bool:
    return ((info.external_attr >> 16) & 0o170000) == 0o120000


def _bounded_archive_infos(items: Iterable, errors: list[str]) -> Iterator:
    for index, item in enumerate(items):
        if index >= MAX_ARCHIVE_ENTRIES:
            errors.append("stopped reading archive after entry count limit")
            return
        yield item


def _bounded_tar_members(archive: tarfile.TarFile, errors: list[str]) -> Iterator[tarfile.TarInfo]:
    for index, member in enumerate(archive):
        if index >= MAX_ARCHIVE_ENTRIES:
            errors.append("stopped reading archive after entry count limit")
            return
        yield member


def _is_zip_archive(path: Path) -> bool:
    return path.suffix.lower() == ".zip"


def _is_tar_archive(path: Path) -> bool:
    suffixes = [suffix.lower() for suffix in path.suffixes]
    return (
        suffixes[-1:] == [".tar"]
        or suffixes[-2:] in ([".tar", ".gz"], [".tar", ".bz2"], [".tar", ".xz"])
        or suffixes[-1:] == [".tgz"]
    )


def _archive_skill_id(path: Path) -> str:
    name = path.name
    for suffix in (".tar.gz", ".tar.bz2", ".tar.xz", ".tgz", ".zip", ".tar"):
        if name.lower().endswith(suffix):
            return name[: -len(suffix)]
    return path.stem
