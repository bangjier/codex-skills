#!/usr/bin/env python3
"""Deterministic release README and Git publishing helper."""

from __future__ import annotations

import argparse
import ast
import dataclasses
import datetime as dt
import difflib
import hashlib
import json
import os
import re
import secrets
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python < 3.11
    tomllib = None


MAX_DIFF_CHARS = 250_000
MAX_SCAN_BYTES = 2_000_000
KNOWN_RELEASE_HEADINGS = {
    "release notes",
    "releases",
    "changelog",
    "change log",
    "version history",
    "version updates",
    "版本更新记录",
    "版本记录",
    "更新日志",
    "发布说明",
}
SENSITIVE_SUFFIXES = {
    ".cer", ".crt", ".der", ".jks", ".key", ".keystore", ".mobileprovision",
    ".ovpn", ".p8", ".p12", ".pem", ".pfx", ".provisionprofile",
}
SENSITIVE_BASENAMES = {
    "google-services.json", "googleservice-info.plist", "id_dsa", "id_ecdsa",
    "id_ed25519", "id_rsa", "key.properties", "keystore.properties",
}
SAFE_ENV_TEMPLATES = {".env.example", ".env.sample", ".env.template"}
GENERIC_SECRET_ASSIGNMENT = re.compile(
    r"(?im)^(\s*(?:remote:\s*)?[A-Z0-9_.-]*(?:TOKEN|SECRET|PASSWORD|PASSWD|API[_-]?KEY|"
    r"ACCESS[_-]?KEY|PRIVATE[_-]?KEY|CREDENTIAL)[A-Z0-9_.-]*\s*[:=]\s*)"
    r"(?:(['\"])[^'\"\r\n]{8,}\2\s*[,;]?|[^\s'\"#(),;\[\]{}]{8,})"
    r"\s*(?:#.*)?$"
)
AUTHORIZATION_SECRET = re.compile(
    r"(?im)^(\s*(?:remote:\s*)?Authorization\s*:\s*(?:Bearer|Basic)\s+)(\S+)"
)
CREDENTIAL_URL = re.compile(r"\b(https?://)[^/\s:@]+:[^@\s/]+@", re.I)
JWT_SECRET = re.compile(
    r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"
)
OPAQUE_SECRET_PATTERNS = [
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\b(?:ghp|gho|ghu|ghs|github_pat)_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\b(?:sk|rk)_(?:live|test)_[A-Za-z0-9]{16,}\b", re.I),
    re.compile(r"\bsk-(?:(?:proj|svcacct)-)?[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{16,}\b"),
    re.compile(r"\bglpat-[A-Za-z0-9_-]{16,}\b"),
    re.compile(r"\bnpm_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bpypi-[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\bAIza[0-9A-Za-z_-]{30,}\b"),
]
CONTENT_SECRET_PATTERNS = [
    re.compile(r"-----BEGIN (?:[A-Z ]+ )?PRIVATE KEY-----"),
    CREDENTIAL_URL,
    GENERIC_SECRET_ASSIGNMENT,
    AUTHORIZATION_SECRET,
    JWT_SECRET,
    *OPAQUE_SECRET_PATTERNS,
]


class ReleaseError(Exception):
    def __init__(self, code: str, message: str, details: Optional[dict[str, Any]] = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}


@dataclasses.dataclass(frozen=True)
class VersionInfo:
    provider: str
    version: str
    build: Optional[str]
    source_files: tuple[str, ...]
    selection: dict[str, str]
    details: dict[str, Any]

    @property
    def signature(self) -> tuple[str, str]:
        return self.version, self.build or ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "version": self.version,
            "build": self.build,
            "source_files": list(self.source_files),
            "selection": self.selection,
            "details": self.details,
        }


def sanitize_output(value: str) -> str:
    value = re.sub(r"(https?://)[^/@\s]+@", r"\1[REDACTED]@", value)
    value = GENERIC_SECRET_ASSIGNMENT.sub(r"\1[REDACTED]", value)
    value = AUTHORIZATION_SECRET.sub(r"\1[REDACTED]", value)
    value = JWT_SECRET.sub("[REDACTED JWT]", value)
    for pattern in OPAQUE_SECRET_PATTERNS:
        value = pattern.sub("[REDACTED TOKEN]", value)
    return re.sub(
        r"-----BEGIN (?:[A-Z ]+ )?PRIVATE KEY-----.*?-----END (?:[A-Z ]+ )?PRIVATE KEY-----",
        "[REDACTED PRIVATE KEY]",
        value,
        flags=re.S,
    )


def sanitize_payload(value: Any) -> Any:
    if isinstance(value, str):
        return sanitize_output(value)
    if isinstance(value, dict):
        return {key: sanitize_payload(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [sanitize_payload(item) for item in value]
    return value


def emit(payload: dict[str, Any], exit_code: int = 0) -> None:
    print(json.dumps(sanitize_payload(payload), ensure_ascii=False, indent=2))
    raise SystemExit(exit_code)


def fail(error: ReleaseError) -> None:
    payload: dict[str, Any] = {
        "ok": False,
        "error": {"code": error.code, "message": error.message},
    }
    if error.details:
        payload["error"]["details"] = error.details
    emit(payload, 2)


def run_process(
    args: list[str],
    cwd: Path,
    *,
    check: bool = True,
    text: bool = True,
    timeout: Optional[int] = None,
) -> subprocess.CompletedProcess[Any]:
    env = os.environ.copy()
    env.update({"GIT_TERMINAL_PROMPT": "0", "GIT_OPTIONAL_LOCKS": "0"})
    result = subprocess.run(
        args,
        cwd=cwd,
        env=env,
        capture_output=True,
        text=text,
        timeout=timeout,
        check=False,
    )
    if check and result.returncode:
        stderr = result.stderr if text else result.stderr.decode("utf-8", "replace")
        raise ReleaseError(
            "command_failed",
            f"Command failed ({result.returncode}): {sanitize_output(shlex.join(args))}",
            {"output": sanitize_output(stderr)[-4000:]},
        )
    return result


def git(repo: Path, *args: str, check: bool = True) -> str:
    return run_process(["git", *args], repo, check=check).stdout


def git_bytes(repo: Path, *args: str, check: bool = True) -> bytes:
    return run_process(["git", *args], repo, check=check, text=False).stdout


def resolve_repo(path: str) -> Path:
    candidate = Path(path).expanduser().resolve()
    result = run_process(["git", "rev-parse", "--show-toplevel"], candidate, check=False)
    if result.returncode:
        raise ReleaseError("not_git_repository", "The selected path is not inside a Git repository.")
    return Path(result.stdout.strip()).resolve()


def safe_relative_path(repo: Path, value: str, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ReleaseError("invalid_config", f"{label} must be a non-empty path.")
    candidate = Path(value)
    if candidate.is_absolute():
        raise ReleaseError("invalid_config", f"{label} must be repository-relative.")
    resolved = (repo / candidate).resolve()
    try:
        rel = resolved.relative_to(repo)
    except ValueError as exc:
        raise ReleaseError("invalid_config", f"{label} escapes the repository.") from exc
    return rel.as_posix()


def strip_yaml_comment(line: str) -> str:
    quote: Optional[str] = None
    escaped = False
    for index, char in enumerate(line):
        if escaped:
            escaped = False
            continue
        if char == "\\" and quote == '"':
            escaped = True
            continue
        if char in {"'", '"'}:
            if quote is None:
                quote = char
            elif quote == char:
                quote = None
            continue
        if char == "#" and quote is None and (index == 0 or line[index - 1].isspace()):
            return line[:index].rstrip()
    return line.rstrip()


def parse_yaml_scalar(raw: str, line_number: int) -> Any:
    value = raw.strip()
    if not value:
        return None
    if value.startswith(("&", "*", "!", "|", ">")):
        raise ReleaseError(
            "unsupported_yaml",
            f"Unsupported YAML feature at line {line_number}; use mappings, scalars, and scalar lists.",
        )
    if value[0] in "[{":
        try:
            return json.loads(value)
        except json.JSONDecodeError as exc:
            raise ReleaseError("invalid_config", f"Invalid inline JSON at line {line_number}.") from exc
    if value[0] in {"'", '"'}:
        try:
            parsed = ast.literal_eval(value)
        except (SyntaxError, ValueError) as exc:
            raise ReleaseError("invalid_config", f"Invalid quoted value at line {line_number}.") from exc
        if not isinstance(parsed, str):
            raise ReleaseError("invalid_config", f"Quoted value at line {line_number} must be a string.")
        return parsed
    lowered = value.lower()
    if lowered in {"null", "~"}:
        return None
    if lowered in {"true", "false"}:
        return lowered == "true"
    if re.fullmatch(r"-?(?:0|[1-9]\d*)", value):
        return int(value)
    if re.fullmatch(r"-?(?:0|[1-9]\d*)\.\d+", value):
        return float(value)
    return value


def parse_yaml_subset(text: str) -> Any:
    stripped = text.lstrip()
    if stripped.startswith(("{", "[")):
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise ReleaseError("invalid_config", "Invalid JSON/YAML configuration.") from exc
    lines: list[tuple[int, str, int]] = []
    for number, original in enumerate(text.splitlines(), 1):
        if "\t" in original[: len(original) - len(original.lstrip())]:
            raise ReleaseError("invalid_config", f"Tabs are not allowed for indentation (line {number}).")
        cleaned = strip_yaml_comment(original)
        if not cleaned.strip():
            continue
        lines.append((len(cleaned) - len(cleaned.lstrip(" ")), cleaned.strip(), number))

    def parse_block(index: int, indent: int) -> tuple[Any, int]:
        if index >= len(lines):
            return {}, index
        list_mode = lines[index][1].startswith("- ")
        container: Any = [] if list_mode else {}
        while index < len(lines):
            current_indent, content, line_number = lines[index]
            if current_indent < indent:
                break
            if current_indent != indent:
                raise ReleaseError("invalid_config", f"Unexpected indentation at line {line_number}.")
            if list_mode:
                if not content.startswith("- "):
                    raise ReleaseError("invalid_config", f"Mixed list and mapping at line {line_number}.")
                item = content[2:].strip()
                if not item:
                    raise ReleaseError("unsupported_yaml", f"Nested list items are unsupported at line {line_number}.")
                container.append(parse_yaml_scalar(item, line_number))
                index += 1
                continue
            if content.startswith("- ") or ":" not in content:
                raise ReleaseError("invalid_config", f"Expected a mapping entry at line {line_number}.")
            key, raw_value = content.split(":", 1)
            key = key.strip()
            if not re.fullmatch(r"[A-Za-z0-9_.-]+", key):
                raise ReleaseError("invalid_config", f"Invalid mapping key at line {line_number}.")
            if key in container:
                raise ReleaseError("invalid_config", f"Duplicate key '{key}' at line {line_number}.")
            raw_value = raw_value.strip()
            index += 1
            if raw_value:
                container[key] = parse_yaml_scalar(raw_value, line_number)
            elif index < len(lines) and lines[index][0] > indent:
                container[key], index = parse_block(index, lines[index][0])
            else:
                container[key] = {}
        return container, index

    if not lines:
        return {}
    if lines[0][0] != 0:
        raise ReleaseError("invalid_config", "Top-level YAML must not be indented.")
    parsed, final_index = parse_block(0, 0)
    if final_index != len(lines):
        raise ReleaseError("invalid_config", "Could not parse the complete configuration.")
    return parsed


def load_config(repo: Path) -> dict[str, Any]:
    path = repo / ".release-readme.yaml"
    if not path.exists():
        return {}
    if not path.is_file():
        raise ReleaseError("invalid_config", ".release-readme.yaml is not a regular file.")
    try:
        config = parse_yaml_subset(path.read_text(encoding="utf-8"))
    except UnicodeDecodeError as exc:
        raise ReleaseError("invalid_config", ".release-readme.yaml must be UTF-8.") from exc
    if not isinstance(config, dict):
        raise ReleaseError("invalid_config", "Configuration root must be a mapping.")
    for section in ("version", "readme", "boundary", "checks", "git"):
        if section in config and not isinstance(config[section], (dict, str)):
            raise ReleaseError("invalid_config", f"Configuration section '{section}' must be a mapping.")
    return config


def read_repo_file(repo: Path, relative: str, revision: Optional[str] = None) -> str:
    if revision:
        result = run_process(["git", "show", f"{revision}:{relative}"], repo, check=False)
        if result.returncode:
            raise ReleaseError("version_not_found", f"Version source '{relative}' is absent at a required history commit.")
        return result.stdout
    path = repo / relative
    if not path.is_file():
        raise ReleaseError("version_not_found", f"Version source '{relative}' does not exist.")
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise ReleaseError("version_not_found", f"Version source '{relative}' is not UTF-8 text.") from exc


def list_tree_files(repo: Path, revision: Optional[str] = None) -> list[str]:
    if revision:
        return [line for line in git(repo, "ls-tree", "-r", "--name-only", revision).splitlines() if line]
    return sorted(
        {
            part.decode("utf-8", "surrogateescape")
            for part in git_bytes(
                repo,
                "ls-files",
                "--cached",
                "--others",
                "--exclude-standard",
                "-z",
            ).split(b"\0")
            if part
        }
    )


def clean_scalar(value: str) -> str:
    value = value.strip().rstrip(",")
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        try:
            return str(ast.literal_eval(value))
        except (SyntaxError, ValueError):
            return value[1:-1]
    return value


def detect_flutter(
    repo: Path, version_cfg: dict[str, Any], revision: Optional[str], selection: Optional[dict[str, str]]
) -> VersionInfo:
    configured = selection.get("file") if selection else version_cfg.get("file")
    if configured:
        relative = safe_relative_path(repo, str(configured), "version.file")
    else:
        files = [path for path in list_tree_files(repo, revision) if path.endswith("pubspec.yaml")]
        versioned_files = []
        for path in files:
            content = read_repo_file(repo, path, revision)
            if re.search(r"(?m)^version:\s*[^#\r\n]+", content):
                versioned_files.append(path)
        if len(versioned_files) > 1:
            raise ReleaseError(
                "ambiguous_version",
                "Multiple versioned Flutter pubspec.yaml files require version.file configuration.",
            )
        if len(versioned_files) == 1:
            relative = versioned_files[0]
        elif not files:
            raise ReleaseError("version_not_found", "No Flutter pubspec.yaml was found.")
        else:
            raise ReleaseError("version_not_found", "No Flutter pubspec.yaml contains a version.")
    text = read_repo_file(repo, relative, revision)
    matches = re.findall(r"(?m)^version:\s*([^#\r\n]+?)\s*(?:#.*)?$", text)
    if len(matches) != 1:
        raise ReleaseError("ambiguous_version", f"Expected exactly one top-level version in '{relative}'.")
    release = clean_scalar(matches[0])
    if not release or re.search(r"\s", release):
        raise ReleaseError("ambiguous_version", f"Flutter version in '{relative}' is invalid.")
    version, plus, build = release.partition("+")
    return VersionInfo(
        "flutter",
        release,
        build if plus else None,
        (relative,),
        {"provider": "flutter", "file": relative},
        {"semantic_version": version},
    )


def get_dotted(data: Any, field: str) -> Any:
    current = data
    for component in field.split("."):
        if isinstance(current, dict) and component in current:
            current = current[component]
        else:
            raise ReleaseError("version_not_found", f"Configured field '{field}' was not found.")
    return current


def detect_custom(
    repo: Path, version_cfg: dict[str, Any], revision: Optional[str], selection: Optional[dict[str, str]]
) -> VersionInfo:
    configured = selection.get("file") if selection else version_cfg.get("file")
    if not configured:
        raise ReleaseError("invalid_config", "Custom version provider requires version.file.")
    relative = safe_relative_path(repo, str(configured), "version.file")
    text = read_repo_file(repo, relative, revision)
    pattern = version_cfg.get("pattern")
    field = version_cfg.get("field")
    if bool(pattern) == bool(field):
        raise ReleaseError("invalid_config", "Custom provider requires exactly one of version.field or version.pattern.")
    build: Any = None
    if pattern:
        try:
            regex = re.compile(str(pattern), re.M)
        except re.error as exc:
            raise ReleaseError("invalid_config", f"Invalid version.pattern: {exc}.") from exc
        if "version" not in regex.groupindex:
            raise ReleaseError("invalid_config", "version.pattern must define a named 'version' group.")
        matches = list(regex.finditer(text))
        values = {(match.group("version"), match.groupdict().get("build")) for match in matches}
        if len(values) != 1:
            raise ReleaseError("ambiguous_version", "Custom version.pattern did not produce one unique version.")
        version, build = next(iter(values))
    else:
        suffix = Path(relative).suffix.lower()
        try:
            if suffix == ".json":
                data = json.loads(text)
            elif suffix == ".toml":
                if tomllib is None:
                    raise ReleaseError("missing_dependency", "TOML fields require Python 3.11 or newer.")
                data = tomllib.loads(text)
            elif suffix in {".yaml", ".yml"}:
                data = parse_yaml_subset(text)
            else:
                raise ReleaseError("invalid_config", "Custom version.field supports JSON, TOML, or YAML files.")
        except (json.JSONDecodeError, ValueError) as exc:
            raise ReleaseError("invalid_config", f"Could not parse custom version source '{relative}'.") from exc
        version = get_dotted(data, str(field))
        build_field = version_cfg.get("build_field")
        if build_field:
            build = get_dotted(data, str(build_field))
    if not isinstance(version, (str, int, float)) or str(version).strip() == "":
        raise ReleaseError("ambiguous_version", "Custom version value must be a non-empty scalar.")
    if build is not None and not isinstance(build, (str, int, float)):
        raise ReleaseError("ambiguous_version", "Custom build value must be a scalar.")
    return VersionInfo(
        "custom",
        str(version),
        None if build is None else str(build),
        (relative,),
        {"provider": "custom", "file": relative},
        {"field": field, "pattern_configured": bool(pattern)},
    )


def object_blocks(text: str) -> dict[str, tuple[str, str]]:
    pattern = re.compile(r"(?m)^\s*([A-F0-9]{24})\s*(?:/\*\s*(.*?)\s*\*/)?\s*=\s*\{")
    objects: dict[str, tuple[str, str]] = {}
    for match in pattern.finditer(text):
        brace = text.find("{", match.start())
        depth = 0
        quote: Optional[str] = None
        escaped = False
        end = -1
        for index in range(brace, len(text)):
            char = text[index]
            if escaped:
                escaped = False
                continue
            if char == "\\" and quote:
                escaped = True
                continue
            if char in {"'", '"'}:
                quote = None if quote == char else char if quote is None else quote
                continue
            if quote:
                continue
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    end = index + 1
                    break
        if end > 0:
            objects[match.group(1)] = (match.group(2) or "", text[brace:end])
    return objects


def pbx_assignment(body: str, key: str) -> Optional[str]:
    match = re.search(rf"(?m)^\s*{re.escape(key)}\s*=\s*(.*?);\s*$", body)
    return clean_scalar(match.group(1)) if match else None


def pbx_build_settings(body: str) -> dict[str, str]:
    marker = re.search(r"\bbuildSettings\s*=\s*\{", body)
    if not marker:
        return {}
    start = body.find("{", marker.start())
    depth = 0
    end = -1
    for index in range(start, len(body)):
        if body[index] == "{":
            depth += 1
        elif body[index] == "}":
            depth -= 1
            if depth == 0:
                end = index
                break
    if end < 0:
        raise ReleaseError("ambiguous_version", "Malformed Xcode buildSettings block.")
    settings: dict[str, str] = {}
    pattern = r"(?m)^\s*([A-Za-z_][A-Za-z0-9_.-]*)\s*=\s*(.*?);\s*$"
    for match in re.finditer(pattern, body[start + 1 : end]):
        settings[match.group(1)] = clean_scalar(match.group(2))
    return settings


def resolve_project_file(
    repo: Path, project_file: str, raw_path: str, revision: Optional[str]
) -> str:
    path = clean_scalar(raw_path).replace("$(SRCROOT)/", "").replace("$(PROJECT_DIR)/", "")
    candidates = [
        (Path(project_file).parent / path).as_posix(),
        Path(path).as_posix(),
    ]
    files = set(list_tree_files(repo, revision))
    for candidate in candidates:
        normalized = Path(candidate).as_posix()
        if normalized in files:
            return normalized
    matches = [item for item in files if item.endswith("/" + Path(path).name) or item == Path(path).name]
    if len(matches) == 1:
        return matches[0]
    raise ReleaseError("ambiguous_version", f"Could not uniquely resolve Xcode configuration file '{path}'.")


def load_xcconfig(
    repo: Path,
    relative: str,
    revision: Optional[str],
    seen: Optional[set[str]] = None,
) -> tuple[dict[str, str], set[str]]:
    seen = set() if seen is None else seen
    if relative in seen:
        raise ReleaseError("ambiguous_version", "Circular xcconfig include detected.")
    seen.add(relative)
    text = read_repo_file(repo, relative, revision)
    settings: dict[str, str] = {}
    sources = {relative}
    for line in text.splitlines():
        stripped = line.strip()
        include = re.match(r'#include(?:\?)?\s+["<]([^">]+)[">]', stripped)
        if include:
            child = (Path(relative).parent / include.group(1)).as_posix()
            child_settings, child_sources = load_xcconfig(repo, child, revision, seen)
            settings.update(child_settings)
            sources.update(child_sources)
            continue
        if not stripped or stripped.startswith("//"):
            continue
        match = re.match(r"([A-Za-z_][A-Za-z0-9_.-]*)\s*=\s*(.*?)\s*(?://.*)?$", line)
        if match:
            settings[match.group(1)] = clean_scalar(match.group(2))
    return settings, sources


VARIABLE_PATTERN = re.compile(r"\$\(([^):]+)(?::[^)]+)?\)|\$\{([^}:]+)(?::[^}]+)?\}")


def resolve_variables(value: str, environment: dict[str, str], stack: tuple[str, ...] = ()) -> str:
    def replace(match: re.Match[str]) -> str:
        name = match.group(1) or match.group(2)
        if name == "inherited":
            return ""
        if name in stack:
            raise ReleaseError("ambiguous_version", f"Circular version variable reference involving '{name}'.")
        if name not in environment:
            raise ReleaseError("ambiguous_version", f"Unresolved version variable '{name}'.")
        return resolve_variables(environment[name], environment, stack + (name,))

    previous = value
    for _ in range(20):
        resolved = VARIABLE_PATTERN.sub(replace, previous)
        if resolved == previous:
            return clean_scalar(resolved)
        previous = resolved
    raise ReleaseError("ambiguous_version", "Version variable expansion exceeded the safety limit.")


def setting_uses_base(
    key: str,
    build_settings: dict[str, str],
    base_settings: dict[str, str],
    seen: Optional[set[str]] = None,
) -> bool:
    seen = set() if seen is None else seen
    if key in seen:
        return False
    seen.add(key)
    if key not in build_settings:
        return key in base_settings
    for match in VARIABLE_PATTERN.finditer(build_settings[key]):
        name = match.group(1) or match.group(2)
        if name in base_settings and name not in build_settings:
            return True
        if setting_uses_base(name, build_settings, base_settings, seen):
            return True
    return False


def detect_ios(
    repo: Path, version_cfg: dict[str, Any], revision: Optional[str], selection: Optional[dict[str, str]]
) -> VersionInfo:
    configured_file = selection.get("file") if selection else version_cfg.get("file")
    if configured_file:
        project_file = safe_relative_path(repo, str(configured_file), "version.file")
    else:
        projects = [path for path in list_tree_files(repo, revision) if path.endswith(".xcodeproj/project.pbxproj")]
        if len(projects) != 1:
            raise ReleaseError(
                "ambiguous_version",
                "iOS detection requires exactly one .xcodeproj or version.file configuration.",
            )
        project_file = projects[0]
    text = read_repo_file(repo, project_file, revision)
    objects = object_blocks(text)
    if not objects:
        raise ReleaseError("ambiguous_version", f"Could not parse Xcode project '{project_file}'.")

    file_refs: dict[str, str] = {}
    configs: dict[str, tuple[str, dict[str, str], Optional[str]]] = {}
    lists: dict[str, list[str]] = {}
    targets: list[tuple[str, str]] = []
    for object_id, (comment, body) in objects.items():
        isa = pbx_assignment(body, "isa")
        if isa == "PBXFileReference":
            path = pbx_assignment(body, "path") or pbx_assignment(body, "name")
            if path:
                file_refs[object_id] = path
        elif isa == "XCBuildConfiguration":
            configs[object_id] = (
                pbx_assignment(body, "name") or comment,
                pbx_build_settings(body),
                pbx_assignment(body, "baseConfigurationReference"),
            )
        elif isa == "XCConfigurationList":
            list_match = re.search(r"buildConfigurations\s*=\s*\((.*?)\);", body, re.S)
            if list_match:
                lists[object_id] = re.findall(r"\b[A-F0-9]{24}\b", list_match.group(1))
        elif isa == "PBXNativeTarget":
            target_name = pbx_assignment(body, "name") or comment
            product_type = pbx_assignment(body, "productType") or ""
            config_list = pbx_assignment(body, "buildConfigurationList")
            if config_list and "application" in product_type and "extension" not in product_type:
                targets.append((target_name, config_list.split()[0]))

    requested_target = selection.get("target") if selection else version_cfg.get("target")
    if requested_target:
        selected_targets = [target for target in targets if target[0] == requested_target]
        if len(selected_targets) != 1:
            raise ReleaseError("ambiguous_version", f"Configured iOS target '{requested_target}' was not found uniquely.")
    else:
        if len(targets) != 1:
            raise ReleaseError("ambiguous_version", "Multiple or missing iOS application targets require version.target.")
        selected_targets = targets

    signatures: set[tuple[str, str]] = set()
    source_files: set[str] = set()
    config_names: list[str] = []
    for target_name, config_list in selected_targets:
        config_ids = lists.get(config_list, [])
        if not config_ids:
            raise ReleaseError("ambiguous_version", f"Target '{target_name}' has no readable build configurations.")
        for config_id in config_ids:
            if config_id not in configs:
                continue
            config_name, build_settings, base_ref = configs[config_id]
            base_settings: dict[str, str] = {}
            base_sources: set[str] = set()
            if base_ref:
                ref_id = base_ref.split()[0]
                if ref_id not in file_refs:
                    raise ReleaseError("ambiguous_version", f"Unresolved base configuration for '{config_name}'.")
                xcconfig_path = resolve_project_file(repo, project_file, file_refs[ref_id], revision)
                base_settings, base_sources = load_xcconfig(repo, xcconfig_path, revision)
            environment = {
                **base_settings,
                **build_settings,
                "PROJECT_DIR": str(Path(project_file).parent.parent),
                "SRCROOT": str(Path(project_file).parent.parent),
            }
            required = ("MARKETING_VERSION", "CURRENT_PROJECT_VERSION")
            if any(key not in environment for key in required):
                raise ReleaseError(
                    "ambiguous_version",
                    f"Both MARKETING_VERSION and CURRENT_PROJECT_VERSION are required for '{target_name}/{config_name}'.",
                )
            marketing = resolve_variables(environment["MARKETING_VERSION"], environment)
            build = resolve_variables(environment["CURRENT_PROJECT_VERSION"], environment)
            if not marketing or not build or "$(" in marketing + build or "$" + "{" in marketing + build:
                raise ReleaseError("ambiguous_version", "Unresolved iOS version value.")
            signatures.add((marketing, build))
            for key in required:
                if setting_uses_base(key, build_settings, base_settings):
                    source_files.update(base_sources)
                else:
                    source_files.add(project_file)
            config_names.append(config_name)
    if len(signatures) != 1:
        raise ReleaseError("ambiguous_version", "iOS build configurations contain conflicting versions.")
    marketing, build = next(iter(signatures))
    return VersionInfo(
        "ios",
        marketing,
        build,
        tuple(sorted(source_files)),
        {"provider": "ios", "file": project_file, "target": selected_targets[0][0]},
        {"target": selected_targets[0][0], "configurations": sorted(set(config_names))},
    )


def strip_gradle_comments(text: str) -> str:
    return re.sub(r"(?m)//.*$", "", re.sub(r"/\*.*?\*/", "", text, flags=re.S))


def parse_properties(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith(("#", "!")):
            continue
        match = re.match(r"([^:=\s]+)\s*[:=]\s*(.*)$", line)
        if match:
            values[match.group(1).strip()] = match.group(2).strip()
    return values


def resolve_gradle_expression(
    expression: str,
    variables: dict[str, str],
    properties: dict[str, str],
    used_property: list[bool],
    stack: tuple[str, ...] = (),
) -> str:
    expression = expression.strip().rstrip(",")
    property_match = re.search(
        r"(?:findProperty|property|gradleProperty)\s*\(\s*['\"]([^'\"]+)['\"]\s*\)",
        expression,
    )
    if property_match:
        name = property_match.group(1)
        if name not in properties:
            raise ReleaseError("ambiguous_version", f"Android property '{name}' is unresolved.")
        used_property[0] = True
        return properties[name]
    extra_match = re.search(r"(?:extra|ext)\s*\[\s*['\"]([^'\"]+)['\"]\s*\]", expression)
    if extra_match:
        name = extra_match.group(1)
        if name not in properties and name not in variables:
            raise ReleaseError("ambiguous_version", f"Android extra property '{name}' is unresolved.")
        if name in properties:
            used_property[0] = True
            return properties[name]
        expression = variables[name]
    if len(expression) >= 2 and expression[0] == expression[-1] and expression[0] in {"'", '"'}:
        expression = clean_scalar(expression)

    def replace(match: re.Match[str]) -> str:
        name = match.group(1) or match.group(2)
        if name in stack:
            raise ReleaseError("ambiguous_version", f"Circular Android version variable '{name}'.")
        if name in variables:
            return resolve_gradle_expression(variables[name], variables, properties, used_property, stack + (name,))
        if name in properties:
            used_property[0] = True
            return properties[name]
        raise ReleaseError("ambiguous_version", f"Android version variable '{name}' is unresolved.")

    interpolation = r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}|\$([A-Za-z_][A-Za-z0-9_]*)"
    expression = re.sub(interpolation, replace, expression)
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", expression):
        if expression in variables:
            return resolve_gradle_expression(variables[expression], variables, properties, used_property, stack + (expression,))
        if expression in properties:
            used_property[0] = True
            return properties[expression]
    return clean_scalar(expression)


def detect_android(
    repo: Path, version_cfg: dict[str, Any], revision: Optional[str], selection: Optional[dict[str, str]]
) -> VersionInfo:
    configured_file = selection.get("file") if selection else version_cfg.get("file")
    if configured_file:
        gradle_file = safe_relative_path(repo, str(configured_file), "version.file")
    else:
        candidates: list[str] = []
        for path in list_tree_files(repo, revision):
            if not (path.endswith("build.gradle") or path.endswith("build.gradle.kts")):
                continue
            text = strip_gradle_comments(read_repo_file(repo, path, revision))
            marker = r"(?:id\s*\(?\s*['\"]com\.android\.application|apply\s+plugin:\s*['\"]com\.android\.application)"
            has_version_values = (
                re.search(r"\bversionName\b", text)
                and re.search(r"\bversionCode\b", text)
            )
            if has_version_values and (
                re.search(marker, text) or re.search(r"\bandroid\s*\{", text)
            ):
                candidates.append(path)
        requested_module = selection.get("module") if selection else version_cfg.get("module")
        if requested_module:
            normalized = str(requested_module).strip("/")
            candidates = [
                path for path in candidates
                if Path(path).parent.as_posix() == normalized or Path(path).parent.name == normalized
            ]
        if len(candidates) != 1:
            raise ReleaseError("ambiguous_version", "Multiple or missing Android application modules require version.module or version.file.")
        gradle_file = candidates[0]
    text = strip_gradle_comments(read_repo_file(repo, gradle_file, revision))
    variables: dict[str, str] = {}
    variable_pattern = r"(?m)^\s*(?:(?:val|var|def)\s+|ext\.)([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.+?)\s*$"
    for match in re.finditer(variable_pattern, text):
        variables[match.group(1)] = match.group(2)
    properties_file = (Path(gradle_file).parent / "gradle.properties").as_posix()
    all_files = set(list_tree_files(repo, revision))
    property_sources = [path for path in ("gradle.properties", properties_file) if path in all_files]
    properties: dict[str, str] = {}
    for path in property_sources:
        properties.update(parse_properties(read_repo_file(repo, path, revision)))

    def expressions(name: str) -> list[str]:
        patterns = [
            rf"(?m)^\s*{name}\s*=\s*(.+?)\s*$",
            rf"(?m)^\s*{name}\s+([^=\s].*?)\s*$",
        ]
        return [match.group(1) for pattern in patterns for match in re.finditer(pattern, text)]

    name_exprs = expressions("versionName")
    code_exprs = expressions("versionCode")
    if not name_exprs or not code_exprs:
        raise ReleaseError("ambiguous_version", f"Both versionName and versionCode are required in '{gradle_file}'.")
    used_property = [False]
    names = {resolve_gradle_expression(value, variables, properties, used_property) for value in name_exprs}
    codes = {resolve_gradle_expression(value, variables, properties, used_property) for value in code_exprs}
    if len(names) != 1 or len(codes) != 1:
        raise ReleaseError("ambiguous_version", "Android build variants contain conflicting versionName/versionCode values.")
    version = next(iter(names))
    build = next(iter(codes))
    if not version or not build:
        raise ReleaseError("ambiguous_version", "Android versionName/versionCode resolved to an empty value.")
    sources = tuple(sorted(property_sources)) if used_property[0] else (gradle_file,)
    module = Path(gradle_file).parent.as_posix()
    return VersionInfo(
        "android",
        version,
        build,
        sources,
        {"provider": "android", "file": gradle_file, "module": module},
        {"module": module},
    )


def detect_version(
    repo: Path,
    config: dict[str, Any],
    revision: Optional[str] = None,
    selection: Optional[dict[str, str]] = None,
) -> VersionInfo:
    raw_version_cfg = config.get("version", {})
    if isinstance(raw_version_cfg, str):
        raw_version_cfg = {"provider": raw_version_cfg}
    version_cfg = dict(raw_version_cfg)
    provider = selection.get("provider") if selection else version_cfg.get("provider")
    if provider:
        provider = str(provider).lower()
        detectors: dict[str, Callable[..., VersionInfo]] = {
            "flutter": detect_flutter,
            "ios": detect_ios,
            "android": detect_android,
            "custom": detect_custom,
        }
        if provider not in detectors:
            raise ReleaseError("invalid_config", f"Unknown version provider '{provider}'.")
        return detectors[provider](repo, version_cfg, revision, selection)

    files = set(list_tree_files(repo, revision))
    if "pubspec.yaml" in files:
        return detect_flutter(repo, version_cfg, revision, selection)

    flutter_sources = []
    android_sources = []
    android_marker = (
        r"(?:id\s*\(?\s*['\"]com\.android\.application|"
        r"apply\s+plugin:\s*['\"]com\.android\.application)"
    )
    for path in files:
        if path.endswith("pubspec.yaml"):
            content = read_repo_file(repo, path, revision)
            if re.search(r"(?m)^version:\s*[^#\r\n]+", content):
                flutter_sources.append(path)
        if path.endswith(("build.gradle", "build.gradle.kts")):
            content = strip_gradle_comments(read_repo_file(repo, path, revision))
            has_version_values = (
                re.search(r"\bversionName\b", content)
                and re.search(r"\bversionCode\b", content)
            )
            if has_version_values and (
                re.search(android_marker, content)
                or re.search(r"\bandroid\s*\{", content)
            ):
                android_sources.append(path)
    provider_candidates: set[str] = set()
    if flutter_sources:
        provider_candidates.add("flutter")
    if any(path.endswith(".xcodeproj/project.pbxproj") for path in files):
        provider_candidates.add("ios")
    if android_sources:
        provider_candidates.add("android")
    if len(provider_candidates) > 1:
        raise ReleaseError(
            "ambiguous_version",
            "Multiple project version providers were detected; configure version.provider and version.file.",
        )
    if provider_candidates == {"flutter"}:
        return detect_flutter(repo, version_cfg, revision, selection)
    if provider_candidates == {"ios"}:
        return detect_ios(repo, version_cfg, revision, selection)
    if provider_candidates == {"android"}:
        return detect_android(repo, version_cfg, revision, selection)
    raise ReleaseError(
        "version_not_found",
        "No supported version source was detected; configure .release-readme.yaml.",
    )


def validate_git_state(repo: Path) -> str:
    branch_result = run_process(["git", "symbolic-ref", "--quiet", "--short", "HEAD"], repo, check=False)
    if branch_result.returncode or not branch_result.stdout.strip():
        raise ReleaseError("detached_head", "Detached HEAD is not allowed.")
    git_dir = Path(git(repo, "rev-parse", "--git-dir").strip())
    if not git_dir.is_absolute():
        git_dir = repo / git_dir
    states = {
        "MERGE_HEAD": "merge",
        "REVERT_HEAD": "revert",
        "CHERRY_PICK_HEAD": "cherry-pick",
        "rebase-merge": "rebase",
        "rebase-apply": "rebase",
    }
    active = sorted({label for marker, label in states.items() if (git_dir / marker).exists()})
    if active:
        raise ReleaseError("git_operation_in_progress", f"Git operation in progress: {', '.join(active)}.")
    return branch_result.stdout.strip()


def parse_status(repo: Path) -> list[dict[str, Any]]:
    parts = git_bytes(repo, "status", "--porcelain=v1", "-z", "--untracked-files=all").split(b"\0")
    entries: list[dict[str, Any]] = []
    index = 0
    while index < len(parts) and parts[index]:
        record = parts[index].decode("utf-8", "surrogateescape")
        if len(record) < 4:
            raise ReleaseError("git_status_failed", "Unexpected git status output.")
        code = record[:2]
        entry: dict[str, Any] = {"index": code[0], "worktree": code[1], "path": record[3:]}
        index += 1
        if code[0] in {"R", "C"} or code[1] in {"R", "C"}:
            if index >= len(parts) or not parts[index]:
                raise ReleaseError("git_status_failed", "Incomplete rename entry in git status.")
            entry["original_path"] = parts[index].decode("utf-8", "surrogateescape")
            index += 1
        entries.append(entry)
    conflicts = [
        entry
        for entry in entries
        if entry["index"] == "U"
        or entry["worktree"] == "U"
        or entry["index"] + entry["worktree"] in {"AA", "DD"}
    ]
    if conflicts:
        raise ReleaseError("merge_conflicts", "Unresolved Git conflict entries are present.")
    return entries


def summarize_status(entries: list[dict[str, Any]]) -> dict[str, list[str]]:
    summary = {"staged": [], "unstaged": [], "untracked": [], "deleted": [], "renamed": []}
    for entry in entries:
        path = entry["path"]
        x, y = entry["index"], entry["worktree"]
        if x == "?" and y == "?":
            summary["untracked"].append(path)
            continue
        if x != " ":
            summary["staged"].append(path)
        if y != " ":
            summary["unstaged"].append(path)
        if "D" in {x, y}:
            summary["deleted"].append(path)
        if "R" in {x, y}:
            summary["renamed"].append(path)
    return {key: sorted(set(value)) for key, value in summary.items()}


def is_sensitive_path(path: str) -> bool:
    lower = path.lower()
    name = Path(lower).name
    if name in SAFE_ENV_TEMPLATES:
        return False
    if name == ".env" or name.startswith(".env."):
        return True
    if name in SENSITIVE_BASENAMES or Path(name).suffix in SENSITIVE_SUFFIXES:
        return True
    return bool(re.search(r"(?:^|/)(?:private[-_]?key|signing[-_]?key|credentials?)(?:[./_-]|$)", lower))


def text_contains_secret(text: str) -> bool:
    diff_content = re.sub(r"(?m)^[+-](?![+-])", "", text)
    return any(
        pattern.search(text) or pattern.search(diff_content)
        for pattern in CONTENT_SECRET_PATTERNS
    )


def changed_paths_since(repo: Path, boundary: str, status: list[dict[str, Any]]) -> set[str]:
    paths = {
        part.decode("utf-8", "surrogateescape")
        for part in git_bytes(repo, "diff", "--name-only", "-z", boundary, "--").split(b"\0")
        if part
    }
    for entry in status:
        paths.add(entry["path"])
        if entry.get("original_path"):
            paths.add(entry["original_path"])
    return paths


def check_sensitive_changes(repo: Path, boundary: str, status: list[dict[str, Any]]) -> None:
    paths = changed_paths_since(repo, boundary, status)
    if any(is_sensitive_path(path) for path in paths):
        raise ReleaseError(
            "sensitive_content",
            "Potentially sensitive files are present in changes since the release boundary.",
        )
    patch = git(repo, "diff", "--no-color", "--no-ext-diff", boundary, "--")
    if text_contains_secret(patch):
        raise ReleaseError(
            "sensitive_content",
            "Potential secret material is present in changes since the release boundary.",
        )
    check_sensitive_commits(
        repo,
        git(repo, "rev-list", f"{boundary}..HEAD").splitlines(),
        "release history",
    )
    for entry in status:
        if entry["index"] == "?" and entry["worktree"] == "?":
            path = repo / entry["path"]
            if path.is_file() and path.stat().st_size <= MAX_SCAN_BYTES:
                try:
                    content = path.read_text(encoding="utf-8")
                except (UnicodeDecodeError, OSError):
                    continue
                if text_contains_secret(content):
                    raise ReleaseError(
                        "sensitive_content",
                        "Potential secret material is present in an untracked file.",
                    )


def commits_to_push(repo: Path, remote_plan: dict[str, Any]) -> list[str]:
    if remote_plan["upstream"]:
        revision_range = f"{remote_plan['upstream']}..HEAD"
        return git(repo, "rev-list", revision_range).splitlines()
    return git(
        repo,
        "rev-list",
        "HEAD",
        "--not",
        f"--remotes={remote_plan['remote']}",
    ).splitlines()


def check_sensitive_commits(
    repo: Path, commits: Iterable[str], scope: str
) -> None:
    for commit in commits:
        commit_message = git(repo, "log", "-1", "--format=%B", commit)
        if text_contains_secret(commit_message):
            raise ReleaseError(
                "sensitive_content",
                f"Potential secret material exists in commit metadata for {scope}.",
            )
        commit_paths = git(
            repo,
            "diff-tree",
            "--root",
            "--no-commit-id",
            "--name-only",
            "-r",
            commit,
        ).splitlines()
        if any(is_sensitive_path(path) for path in commit_paths):
            raise ReleaseError(
                "sensitive_content",
                f"Potentially sensitive files exist in {scope}.",
            )
        commit_patch = git(
            repo,
            "show",
            "--format=",
            "--no-color",
            "--no-ext-diff",
            commit,
            "--",
        )
        if text_contains_secret(commit_patch):
            raise ReleaseError(
                "sensitive_content",
                f"Potential secret material exists in {scope}.",
            )


def check_sensitive_push_range(repo: Path, remote_plan: dict[str, Any]) -> None:
    check_sensitive_commits(
        repo,
        commits_to_push(repo, remote_plan),
        "commits that would be pushed",
    )


def history_for_source(
    repo: Path, config: dict[str, Any], current: VersionInfo, source: str
) -> tuple[str, bool]:
    commits = [
        line
        for line in git(
            repo,
            "log",
            "--first-parent",
            "--follow",
            "--format=%H",
            "--",
            source,
        ).splitlines()
        if line
    ]
    if not commits:
        raise ReleaseError("boundary_not_found", f"No Git history exists for version source '{source}'.")
    observed: list[tuple[str, tuple[str, str]]] = []
    for commit in commits:
        try:
            info = detect_version(repo, config, commit, current.selection)
        except ReleaseError:
            break
        observed.append((commit, info.signature))
    if not observed:
        raise ReleaseError("boundary_not_found", f"Version history for '{source}' is unreadable.")
    head_info = detect_version(repo, config, "HEAD", current.selection)
    uncommitted = current.signature != head_info.signature
    upgrades = [
        observed[index]
        for index in range(len(observed) - 1)
        if observed[index][1] != observed[index + 1][1]
    ]
    if uncommitted:
        matches = [item for item in upgrades if item[1] == head_info.signature]
        if not matches:
            raise ReleaseError(
                "boundary_not_found",
                "The previous committed version has no trustworthy introduction commit.",
            )
        return matches[0][0], True
    matching_indexes = [index for index, item in enumerate(upgrades) if item[1] == current.signature]
    if not matching_indexes:
        raise ReleaseError(
            "boundary_not_found",
            "The current version has no trustworthy introduction commit.",
        )
    previous_index = matching_indexes[0] + 1
    if previous_index >= len(upgrades):
        raise ReleaseError(
            "boundary_not_found",
            "No prior version upgrade commit exists for the current version.",
        )
    return upgrades[previous_index][0], False


def find_boundary(repo: Path, config: dict[str, Any], current: VersionInfo) -> tuple[str, str, bool]:
    raw = config.get("boundary", {})
    boundary_cfg = {"strategy": raw} if isinstance(raw, str) else dict(raw)
    strategy = str(boundary_cfg.get("strategy", "version-file-history"))
    if strategy in {"commit", "tag"}:
        key = "commit" if strategy == "commit" else "ref"
        value = boundary_cfg.get(key)
        if not value:
            raise ReleaseError("invalid_config", f"boundary.{key} is required for strategy '{strategy}'.")
        result = run_process(["git", "rev-parse", "--verify", f"{value}^{{commit}}"], repo, check=False)
        if result.returncode:
            raise ReleaseError("boundary_not_found", f"Configured boundary {key} could not be resolved.")
        commit = result.stdout.strip()
        ancestor = run_process(["git", "merge-base", "--is-ancestor", commit, "HEAD"], repo, check=False)
        if ancestor.returncode:
            raise ReleaseError("boundary_not_found", "Configured boundary is not an ancestor of HEAD.")
        head_info = detect_version(repo, config, "HEAD", current.selection)
        return commit, f"explicit-{strategy}", current.signature != head_info.signature
    if strategy != "version-file-history":
        raise ReleaseError("invalid_config", f"Unknown boundary strategy '{strategy}'.")
    if not current.source_files:
        raise ReleaseError("boundary_not_found", "Version detector did not identify a source file.")
    results = [history_for_source(repo, config, current, source) for source in current.source_files]
    commits = {result[0] for result in results}
    uncommitted_flags = {result[1] for result in results}
    if len(commits) != 1 or len(uncommitted_flags) != 1:
        raise ReleaseError(
            "boundary_not_found",
            "Version source histories disagree on the release boundary.",
        )
    return next(iter(commits)), "version-file-history", next(iter(uncommitted_flags))


def resolve_readme(repo: Path, config: dict[str, Any]) -> str:
    readme_cfg = config.get("readme", {})
    if isinstance(readme_cfg, str):
        readme_cfg = {"file": readme_cfg}
    configured = readme_cfg.get("file")
    if configured:
        return safe_relative_path(repo, str(configured), "readme.file")
    if (repo / "README.md").is_file():
        return "README.md"
    candidates = [
        path.name
        for path in repo.iterdir()
        if path.is_file() and path.name.lower() == "readme.md"
    ]
    if len(candidates) > 1:
        raise ReleaseError("ambiguous_readme", "Multiple root README files require readme.file configuration.")
    return candidates[0] if candidates else "README.md"


def infer_checks(repo: Path, config: dict[str, Any], provider: str) -> list[str]:
    checks_cfg = config.get("checks", {})
    if isinstance(checks_cfg, str):
        if text_contains_secret(checks_cfg):
            raise ReleaseError(
                "sensitive_content",
                "A configured static-check command appears to contain secret material.",
            )
        return [checks_cfg]
    if "commands" in checks_cfg:
        commands = checks_cfg["commands"]
        if not isinstance(commands, list) or not all(
            isinstance(item, str) and item.strip() for item in commands
        ):
            raise ReleaseError("invalid_config", "checks.commands must be a list of non-empty strings.")
        if any(text_contains_secret(command) for command in commands):
            raise ReleaseError(
                "sensitive_content",
                "A configured static-check command appears to contain secret material.",
            )
        return commands
    if provider == "flutter" and shutil.which("flutter"):
        return ["flutter analyze"]
    if provider == "android" and (repo / "gradlew").is_file():
        return ["./gradlew lint"]
    if provider == "ios" and shutil.which("swiftlint") and any(repo.glob(".swiftlint.y*ml")):
        return ["swiftlint lint --quiet"]
    package_json = repo / "package.json"
    if package_json.is_file():
        try:
            scripts = json.loads(package_json.read_text(encoding="utf-8")).get("scripts", {})
        except (json.JSONDecodeError, UnicodeDecodeError):
            scripts = {}
        if "lint" in scripts:
            if (repo / "pnpm-lock.yaml").exists() and shutil.which("pnpm"):
                return ["pnpm lint"]
            if (repo / "yarn.lock").exists() and shutil.which("yarn"):
                return ["yarn lint"]
            if shutil.which("npm"):
                return ["npm run lint"]
    makefile = next(
        (path for path in (repo / "Makefile", repo / "makefile") if path.is_file()),
        None,
    )
    if makefile and shutil.which("make"):
        try:
            make_text = makefile.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            make_text = ""
        if re.search(r"(?m)^lint\s*:(?![=])", make_text):
            return ["make lint"]
    if (repo / "Cargo.toml").is_file() and shutil.which("cargo"):
        return ["cargo check"]
    if (repo / "go.mod").is_file() and shutil.which("go"):
        return ["go vet ./..."]
    pyproject = repo / "pyproject.toml"
    if pyproject.is_file():
        try:
            pyproject_text = pyproject.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            pyproject_text = ""
        if shutil.which("ruff") and re.search(r"(?m)^\[tool\.ruff(?:\.|])", pyproject_text):
            return ["ruff check ."]
        if shutil.which("mypy") and re.search(r"(?m)^\[tool\.mypy]", pyproject_text):
            return ["mypy ."]
    return []


def resolve_remote(repo: Path, config: dict[str, Any]) -> dict[str, Any]:
    remotes = set(git(repo, "remote").splitlines())
    upstream_result = run_process(
        ["git", "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"],
        repo,
        check=False,
    )
    if not upstream_result.returncode:
        upstream = upstream_result.stdout.strip()
        branch = git(repo, "symbolic-ref", "--quiet", "--short", "HEAD").strip()
        tracking = run_process(
            [
                "git",
                "for-each-ref",
                "--format=%(upstream:remotename)%09%(upstream:remoteref)",
                "--count=1",
                f"refs/heads/{branch}",
            ],
            repo,
            check=False,
        )
        parts = tracking.stdout.strip().split("\t", 1)
        if tracking.returncode or len(parts) != 2:
            raise ReleaseError(
                "remote_not_found",
                "The branch upstream could not be resolved to a remote branch.",
            )
        remote, upstream_ref = parts
        if not remote or remote not in remotes:
            raise ReleaseError(
                "remote_not_found",
                "The branch upstream has no resolvable remote.",
            )
        if not upstream_ref.startswith("refs/heads/"):
            raise ReleaseError(
                "remote_not_found",
                "The branch upstream is not a pushable remote branch.",
            )
        remote_check = run_process(["git", "remote", "get-url", remote], repo, check=False)
        if remote_check.returncode:
            raise ReleaseError("remote_not_found", "The branch upstream remote is invalid.")
        return {
            "remote": remote,
            "upstream": upstream,
            "upstream_ref": upstream_ref,
            "set_upstream": False,
        }
    git_cfg = config.get("git", {})
    if isinstance(git_cfg, str):
        git_cfg = {"remote": git_cfg}
    remote = git_cfg.get("remote")
    if remote:
        remote = str(remote)
        if remote not in remotes:
            raise ReleaseError("remote_not_found", f"Configured Git remote '{remote}' does not exist.")
    elif "origin" in remotes:
        remote = "origin"
    else:
        raise ReleaseError("remote_not_found", "No upstream or explicit origin remote is available.")
    return {
        "remote": remote,
        "upstream": None,
        "upstream_ref": None,
        "set_upstream": True,
    }


def safe_cumulative_diff(
    repo: Path, boundary: str, status: list[dict[str, Any]]
) -> tuple[str, bool]:
    patch = git(
        repo,
        "diff",
        "--no-color",
        "--no-ext-diff",
        "--find-renames",
        "--unified=2",
        boundary,
        "--",
    )
    chunks = [patch]
    for entry in status:
        if entry["index"] != "?" or entry["worktree"] != "?":
            continue
        relative = entry["path"]
        path = repo / relative
        if not path.is_file() or path.stat().st_size > 50_000:
            chunks.append(f"diff --git a/{relative} b/{relative}\n[new untracked binary or large file]\n")
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            chunks.append(f"diff --git a/{relative} b/{relative}\n[new untracked binary file]\n")
            continue
        chunks.append(
            "".join(
                difflib.unified_diff(
                    [], content.splitlines(keepends=True), f"a/{relative}", f"b/{relative}", n=2
                )
            )
        )
    combined = sanitize_output("\n".join(chunks))
    if len(combined) > MAX_DIFF_CHARS:
        return combined[:MAX_DIFF_CHARS] + "\n[diff truncated]\n", True
    return combined, False


def inspect_repo(repo: Path, mode: str) -> dict[str, Any]:
    config = load_config(repo)
    branch = validate_git_state(repo)
    current = detect_version(repo, config)
    boundary, reason, version_uncommitted = find_boundary(repo, config, current)
    status = parse_status(repo)
    remote = resolve_remote(repo, config) if mode == "publish" else None
    check_sensitive_changes(repo, boundary, status)
    if remote:
        check_sensitive_push_range(repo, remote)
    commits: list[dict[str, str]] = []
    for line in git(repo, "log", "--format=%H%x09%s", f"{boundary}..HEAD").splitlines():
        commit, _, subject = line.partition("\t")
        commits.append({"commit": commit, "subject": sanitize_output(subject)})
    cumulative_diff, truncated = safe_cumulative_diff(repo, boundary, status)
    return {
        "ok": True,
        "mode": mode,
        "repository": str(repo),
        "branch": branch,
        "version": current.to_dict(),
        "version_uncommitted": version_uncommitted,
        "boundary": {
            "commit": boundary,
            "short_commit": boundary[:12],
            "strategy": reason,
            "excluded": True,
        },
        "commits_after_boundary": commits,
        "status": summarize_status(status),
        "status_porcelain": [
            {
                "code": entry["index"] + entry["worktree"],
                "path": entry["path"],
                **({"original_path": entry["original_path"]} if entry.get("original_path") else {}),
            }
            for entry in status
        ],
        "changed_paths_since_boundary": sorted(
            changed_paths_since(repo, boundary, status)
        ),
        "readme": {"file": resolve_readme(repo, config)},
        "static_checks": infer_checks(repo, config, current.provider),
        "remote_plan": remote,
        "cumulative_diff": cumulative_diff,
        "diff_truncated": truncated,
    }


def heading_matches_version(text: str, version: str) -> bool:
    optional_v = "" if version.lower().startswith("v") else "v?"
    return bool(
        re.search(
            rf"(?<![A-Za-z0-9]){optional_v}{re.escape(version)}(?![A-Za-z0-9])",
            text,
            re.I,
        )
    )


def readme_has_version_heading(text: str, version: str) -> bool:
    return any(
        heading_matches_version(match.group(1), version)
        for match in re.finditer(r"(?m)^#{1,6}\s+(.+?)\s*$", text)
    )


def unique_items(items: Iterable[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for raw in items:
        item = re.sub(r"\s+", " ", str(raw)).strip().lstrip("-*+ ").strip()
        key = re.sub(r"[\s\W_]+", "", item, flags=re.UNICODE).lower()
        if item and key and key not in seen:
            seen.add(key)
            result.append(item)
    return result


def render_readme_text(
    existing: str,
    version: str,
    date: str,
    items: list[str],
    readme_cfg: dict[str, Any],
    requested_section: Optional[str],
) -> str:
    normalized = existing.replace("\r\n", "\n").replace("\r", "\n")
    newline = "\r\n" if "\r\n" in existing else "\n"
    lines = normalized.splitlines(keepends=True)
    headings: list[tuple[int, int, str]] = []
    for index, line in enumerate(lines):
        match = re.match(r"^(#{1,6})\s+(.+?)\s*$", line.rstrip("\n"))
        if match:
            headings.append((index, len(match.group(1)), match.group(2).strip()))

    start_marker = readme_cfg.get("start_marker")
    end_marker = readme_cfg.get("end_marker")
    if bool(start_marker) != bool(end_marker):
        raise ReleaseError(
            "invalid_config",
            "readme.start_marker and readme.end_marker must be configured together.",
        )
    region_start = 0
    region_end = len(lines)
    section_heading = readme_cfg.get("section") or requested_section
    section_level = 2
    section_index: Optional[int] = None
    if start_marker:
        starts = [i for i, line in enumerate(lines) if line.rstrip("\n") == str(start_marker)]
        ends = [i for i, line in enumerate(lines) if line.rstrip("\n") == str(end_marker)]
        if len(starts) != 1 or len(ends) != 1 or starts[0] >= ends[0]:
            raise ReleaseError(
                "ambiguous_readme",
                "Configured README markers are missing or ambiguous.",
            )
        region_start, region_end = starts[0] + 1, ends[0]
    else:
        candidates: list[tuple[int, int, str]] = []
        for heading in headings:
            normalized_heading = heading[2].strip().lower()
            if section_heading and normalized_heading == str(section_heading).strip().lower():
                candidates.append(heading)
            elif not section_heading and normalized_heading in KNOWN_RELEASE_HEADINGS:
                candidates.append(heading)
        if len(candidates) > 1:
            raise ReleaseError(
                "ambiguous_readme",
                "Multiple release-note regions require readme.section configuration.",
            )
        if candidates:
            section_index, section_level, found_heading = candidates[0]
            section_heading = found_heading
            region_start = section_index + 1
            region_end = next(
                (
                    idx
                    for idx, level, _ in headings
                    if idx > section_index and level <= section_level
                ),
                len(lines),
            )

    version_headings = [
        (idx, level, title)
        for idx, level, title in headings
        if region_start <= idx < region_end and heading_matches_version(title, version)
    ]
    if len(version_headings) > 1:
        raise ReleaseError(
            "ambiguous_readme",
            "The current version appears in multiple headings in the release-note region.",
        )
    existing_items: list[str] = []
    bullet_style = "-"
    if version_headings:
        version_index, version_level, existing_version_title = version_headings[0]
        v_match = re.search(
            rf"(?<![A-Za-z0-9])(v){re.escape(version)}(?![A-Za-z0-9])",
            existing_version_title,
            re.I,
        )
        version_label = f"{v_match.group(1)}{version}" if v_match else version
        if re.search(r"\d{4}-\d{2}-\d{2}", existing_version_title):
            version_title = re.sub(
                r"\d{4}-\d{2}-\d{2}", date, existing_version_title, count=1
            )
        else:
            version_title = f"{version_label} - {date}"
        version_end = next(
            (
                idx
                for idx, level, _ in headings
                if idx > version_index and idx < region_end and level <= version_level
            ),
            region_end,
        )
        for line in lines[version_index + 1 : version_end]:
            bullet = re.match(r"^\s*([-*+])\s+(.+?)\s*$", line.rstrip("\n"))
            if bullet:
                bullet_style = bullet.group(1)
                existing_items.append(bullet.group(2))
    else:
        version_level = min(section_level + 1, 6)
        version_index = region_start
        version_end = region_start
        for line in lines[region_start:region_end]:
            bullet = re.match(r"^\s*([-*+])\s+", line.rstrip("\n"))
            if bullet:
                bullet_style = bullet.group(1)
                break

    merged_items = unique_items([*existing_items, *items])
    if not merged_items:
        raise ReleaseError(
            "invalid_notes",
            "At least one evidence-based release-note item is required.",
        )
    if version_headings:
        existing_keys = {
            re.sub(r"[\s\W_]+", "", item, flags=re.UNICODE).lower()
            for item in existing_items
        }
        additions = [
            item
            for item in unique_items(items)
            if re.sub(r"[\s\W_]+", "", item, flags=re.UNICODE).lower()
            not in existing_keys
        ]
        preserved_body = list(lines[version_index + 1 : version_end])
        while preserved_body and not preserved_body[-1].strip():
            preserved_body.pop()
        if additions:
            if preserved_body and not re.match(
                r"^\s*[-*+]\s+", preserved_body[-1].rstrip("\n")
            ):
                preserved_body.append("\n")
            preserved_body.extend(f"{bullet_style} {item}\n" for item in additions)
        preserved_body.append("\n")
        lines[version_index:version_end] = [
            f"{'#' * version_level} {version_title}\n",
            *preserved_body,
        ]
    else:
        entry_lines = [f"{'#' * version_level} {version} - {date}\n", "\n"]
        entry_lines.extend(f"{bullet_style} {item}\n" for item in merged_items)
        entry_lines.append("\n")
    if not version_headings and (section_index is not None or start_marker):
        if region_start < len(lines) and lines[region_start].strip():
            entry_lines.append("\n")
        lines[region_start:region_start] = entry_lines
    elif not version_headings:
        chosen = str(
            section_heading
            or (
                "版本更新记录"
                if re.search(r"[\u4e00-\u9fff]", normalized)
                else "Release Notes"
            )
        )
        prefix: list[str] = []
        if lines and lines[-1].strip():
            prefix.append("\n")
        prefix.extend([f"## {chosen}\n", "\n", *entry_lines])
        lines.extend(prefix)

    rendered = "".join(lines)
    if not rendered.endswith("\n"):
        rendered += "\n"
    return rendered.replace("\n", newline)


def render_command(repo: Path, notes_file: Path, preview: bool) -> dict[str, Any]:
    inspection = inspect_repo(repo, "preview")
    config = load_config(repo)
    try:
        notes = json.loads(notes_file.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReleaseError("invalid_notes", "Notes file must be readable UTF-8 JSON.") from exc
    if not isinstance(notes, dict):
        raise ReleaseError("invalid_notes", "Notes JSON root must be an object.")
    detected_version = inspection["version"]["version"]
    if str(notes.get("version", "")) != detected_version:
        raise ReleaseError(
            "invalid_notes",
            "Notes version does not match the detected current version.",
        )
    date = str(notes.get("date", ""))
    try:
        parsed_date = dt.date.fromisoformat(date)
    except ValueError as exc:
        raise ReleaseError("invalid_notes", "Notes date must use YYYY-MM-DD.") from exc
    if parsed_date != dt.date.today():
        raise ReleaseError("invalid_notes", "Release-note date must be today's local date.")
    items = notes.get("items")
    if not isinstance(items, list) or not all(isinstance(item, str) for item in items):
        raise ReleaseError("invalid_notes", "Notes items must be a string list.")
    if any(text_contains_secret(item) for item in items):
        raise ReleaseError(
            "sensitive_content",
            "Potential secret material is present in release-note items.",
        )
    relative = inspection["readme"]["file"]
    path = repo / relative
    try:
        existing = path.read_bytes().decode("utf-8") if path.exists() else ""
    except UnicodeDecodeError as exc:
        raise ReleaseError("ambiguous_readme", "README must be UTF-8 text.") from exc
    if text_contains_secret(existing):
        raise ReleaseError(
            "sensitive_content",
            "Potential secret material is present in the README.",
        )
    readme_cfg = config.get("readme", {})
    if isinstance(readme_cfg, str):
        readme_cfg = {"file": readme_cfg}
    rendered = render_readme_text(
        existing,
        detected_version,
        date,
        items,
        readme_cfg,
        notes.get("section_heading"),
    )
    if text_contains_secret(rendered):
        raise ReleaseError(
            "sensitive_content",
            "Potential secret material is present in the rendered README.",
        )
    changed = rendered != existing
    diff = "".join(
        difflib.unified_diff(
            existing.splitlines(keepends=True),
            rendered.splitlines(keepends=True),
            f"a/{relative}",
            f"b/{relative}",
        )
    )
    if changed and not preview:
        path.parent.mkdir(parents=True, exist_ok=True)
        mode = path.stat().st_mode if path.exists() else None
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=path.parent,
            delete=False,
            newline="",
        ) as handle:
            handle.write(rendered)
            temp_path = Path(handle.name)
        if mode is not None:
            os.chmod(temp_path, mode)
        os.replace(temp_path, path)
    return {
        "ok": True,
        "preview": preview,
        "readme": relative,
        "changed": changed,
        "diff": diff,
    }


def workspace_paths(repo: Path) -> set[str]:
    tracked = {
        part.decode("utf-8", "surrogateescape")
        for part in git_bytes(
            repo,
            "diff",
            "--no-renames",
            "--name-only",
            "-z",
            "HEAD",
            "--",
        ).split(b"\0")
        if part
    }
    untracked = {
        part.decode("utf-8", "surrogateescape")
        for part in git_bytes(repo, "ls-files", "--others", "--exclude-standard", "-z").split(b"\0")
        if part
    }
    return tracked | untracked


def workspace_fingerprint(repo: Path) -> str:
    digest = hashlib.sha256()
    digest.update(
        git_bytes(repo, "diff", "--cached", "--binary", "--no-ext-diff", "HEAD", "--")
    )
    digest.update(git_bytes(repo, "diff", "--binary", "--no-ext-diff", "--"))
    untracked = sorted(
        part.decode("utf-8", "surrogateescape")
        for part in git_bytes(
            repo,
            "ls-files",
            "--others",
            "--exclude-standard",
            "-z",
        ).split(b"\0")
        if part
    )
    for relative in untracked:
        digest.update(relative.encode("utf-8", "surrogateescape"))
        path = repo / relative
        if path.is_symlink():
            digest.update(os.readlink(path).encode("utf-8", "surrogateescape"))
        elif path.is_file():
            with path.open("rb") as handle:
                for chunk in iter(lambda: handle.read(65536), b""):
                    digest.update(chunk)
    return digest.hexdigest()


def run_static_checks(
    repo: Path, commands: list[str], timeout: int
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for command in commands:
        if text_contains_secret(command):
            raise ReleaseError(
                "sensitive_content",
                "A configured static-check command appears to contain secret material.",
            )
        try:
            completed = subprocess.run(
                command,
                cwd=repo,
                shell=True,
                executable="/bin/sh" if os.name != "nt" else None,
                capture_output=True,
                text=True,
                timeout=timeout,
                env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise ReleaseError(
                "static_check_failed",
                f"Static check timed out: {sanitize_output(command)}.",
            ) from exc
        if completed.returncode:
            raise ReleaseError(
                "static_check_failed",
                f"Static check failed ({completed.returncode}): {sanitize_output(command)}",
                {
                    "exit_code": completed.returncode,
                    "output_withheld": "Review the check output locally to avoid exposing credentials.",
                },
            )
        results.append({"command": sanitize_output(command), "exit_code": 0})
    return results


def push_current_branch(repo: Path, remote_plan: dict[str, Any]) -> tuple[bool, str]:
    if remote_plan["set_upstream"]:
        args = ["git", "push", "-u", remote_plan["remote"], "HEAD"]
    else:
        args = [
            "git",
            "push",
            remote_plan["remote"],
            f"HEAD:{remote_plan['upstream_ref']}",
        ]
    result = run_process(args, repo, check=False)
    return result.returncode == 0, sanitize_output((result.stdout + result.stderr)[-4000:])


def validate_commit_message(message: Optional[str]) -> str:
    if not message or not message.strip() or "\n" in message or "\r" in message:
        raise ReleaseError(
            "invalid_commit_message",
            "Provide --commit-message as one non-empty line summarizing this commit's changes without a version number.",
        )
    message = message.strip()
    if text_contains_secret(message):
        raise ReleaseError(
            "sensitive_content",
            "The commit message appears to contain secret material.",
        )
    return message


def configured_checks(
    config: dict[str, Any],
    inferred: list[str],
    requested: list[str],
) -> tuple[list[str], str]:
    checks_cfg = config.get("checks", {})
    if isinstance(checks_cfg, str) or (
        isinstance(checks_cfg, dict) and "commands" in checks_cfg
    ):
        return inferred, "project-config"
    if requested:
        if not all(isinstance(command, str) and command.strip() for command in requested):
            raise ReleaseError("invalid_check", "Requested checks must be non-empty strings.")
        if any(text_contains_secret(command) for command in requested):
            raise ReleaseError(
                "sensitive_content",
                "A requested static-check command appears to contain secret material.",
            )
        return requested, "invocation"
    return inferred, "auto-detected" if inferred else "none-found"


def staged_paths(repo: Path) -> set[str]:
    return {
        part.decode("utf-8", "surrogateescape")
        for part in git_bytes(
            repo,
            "diff",
            "--cached",
            "--no-renames",
            "--name-only",
            "-z",
            "HEAD",
            "--",
        ).split(b"\0")
        if part
    }


def unstaged_or_untracked_paths(repo: Path) -> set[str]:
    unstaged = {
        part.decode("utf-8", "surrogateescape")
        for part in git_bytes(repo, "diff", "--name-only", "-z", "--").split(b"\0")
        if part
    }
    untracked = {
        part.decode("utf-8", "surrogateescape")
        for part in git_bytes(
            repo,
            "ls-files",
            "--others",
            "--exclude-standard",
            "-z",
        ).split(b"\0")
        if part
    }
    return unstaged | untracked


def release_plan_path(repo: Path) -> Path:
    git_dir = Path(git(repo, "rev-parse", "--git-dir").strip())
    if not git_dir.is_absolute():
        git_dir = repo / git_dir
    return git_dir / "publish-version-readme-plan.json"


def plan_digest(payload: dict[str, Any], token: str) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256((token + canonical).encode("utf-8")).hexdigest()


def write_release_plan(repo: Path, payload: dict[str, Any]) -> str:
    token = "pvr_" + secrets.token_urlsafe(24)
    document = dict(payload)
    document["digest"] = plan_digest(payload, token)
    path = release_plan_path(repo)
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=path.parent,
        delete=False,
    ) as handle:
        json.dump(document, handle, sort_keys=True)
        handle.write("\n")
        temp_path = Path(handle.name)
    os.chmod(temp_path, 0o600)
    os.replace(temp_path, path)
    return token


def read_release_plan(repo: Path, token: str) -> dict[str, Any]:
    path = release_plan_path(repo)
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReleaseError(
            "release_plan_missing",
            "No valid staged release plan exists; run the stage command again.",
        ) from exc
    if not isinstance(document, dict):
        raise ReleaseError(
            "release_plan_invalid",
            "The staged release plan has an invalid structure; run stage again.",
        )
    stored_digest = document.pop("digest", None)
    if not token:
        raise ReleaseError("release_plan_invalid", "Release plan token is required.")
    expected_digest = plan_digest(document, token)
    if not stored_digest or not secrets.compare_digest(str(stored_digest), expected_digest):
        raise ReleaseError(
            "release_plan_invalid",
            "Release plan token does not match or the plan integrity check failed.",
        )
    return document


def clear_release_plan(repo: Path) -> None:
    path = release_plan_path(repo)
    if path.exists():
        path.unlink()


def stage_command(
    repo: Path,
    expected_version: str,
    requested_checks: list[str],
    commit_message: Optional[str] = None,
) -> dict[str, Any]:
    inspection = inspect_repo(repo, "publish")
    version = inspection["version"]
    if version["version"] != expected_version:
        raise ReleaseError(
            "version_changed",
            "Detected version changed after preview; rerun inspection.",
        )
    readme = repo / inspection["readme"]["file"]
    try:
        readme_text = readme.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ReleaseError(
            "readme_not_ready",
            "README must be readable UTF-8 before publishing.",
        ) from exc
    if not readme_has_version_heading(readme_text, expected_version):
        raise ReleaseError(
            "readme_not_ready",
            "README does not contain a heading for the detected version.",
        )
    initial_paths = workspace_paths(repo)
    message = validate_commit_message(commit_message) if initial_paths else None
    initial_fingerprint = workspace_fingerprint(repo)
    if any(is_sensitive_path(path) for path in initial_paths):
        raise ReleaseError(
            "sensitive_content",
            "Potentially sensitive files are present in the workspace changes.",
        )

    config = load_config(repo)
    selected_checks, check_source = configured_checks(
        config,
        inspection["static_checks"],
        requested_checks,
    )
    checks_cfg = config.get("checks", {})
    timeout = 600
    if isinstance(checks_cfg, dict) and "timeout_seconds" in checks_cfg:
        try:
            timeout = int(checks_cfg["timeout_seconds"])
        except (TypeError, ValueError) as exc:
            raise ReleaseError(
                "invalid_config",
                "checks.timeout_seconds must be an integer.",
            ) from exc
        if timeout < 1 or timeout > 7200:
            raise ReleaseError(
                "invalid_config",
                "checks.timeout_seconds must be between 1 and 7200.",
            )
    check_results: list[dict[str, Any]] = []
    if initial_paths:
        check_results = run_static_checks(repo, selected_checks, timeout)
        after_checks = workspace_paths(repo)
        after_check_fingerprint = workspace_fingerprint(repo)
        if after_checks != initial_paths or after_check_fingerprint != initial_fingerprint:
            raise ReleaseError(
                "workspace_changed_by_check",
                "Static checks changed the workspace; review those changes before publishing.",
                {
                    "added_paths": sorted(after_checks - initial_paths),
                    "removed_paths": sorted(initial_paths - after_checks),
                },
            )

        run_process(["git", "add", "-A"], repo)
        current_staged_paths = staged_paths(repo)
        remaining_paths = workspace_paths(repo)
        if current_staged_paths != initial_paths or remaining_paths != current_staged_paths:
            raise ReleaseError(
                "staged_path_mismatch",
                "Staged paths do not match the expected complete non-ignored workspace set.",
                {
                    "missing_from_stage": sorted(initial_paths - current_staged_paths),
                    "unexpected_in_stage": sorted(current_staged_paths - initial_paths),
                },
            )
        leftovers = unstaged_or_untracked_paths(repo)
        if leftovers:
            raise ReleaseError(
                "staged_path_mismatch",
                "Unstaged or untracked paths remain after git add -A.",
                {"paths": sorted(leftovers)},
            )
        final_status = parse_status(repo)
        check_sensitive_changes(repo, inspection["boundary"]["commit"], final_status)
        check_sensitive_push_range(repo, inspection["remote_plan"])
    current_staged_paths = staged_paths(repo)
    staged_summary = (
        git(repo, "diff", "--cached", "--stat", "HEAD", "--").rstrip()
        if current_staged_paths
        else ""
    )
    pending_push_commits = commits_to_push(repo, inspection["remote_plan"])
    if current_staged_paths:
        action = "commit-and-push"
    elif pending_push_commits:
        action = "push-only"
    else:
        action = "no-op"
    plan = {
        "action": action,
        "boundary": inspection["boundary"]["commit"],
        "branch": inspection["branch"],
        "checks": check_results,
        "check_source": check_source,
        "commit_message": message,
        "expected_version": expected_version,
        "head": git(repo, "rev-parse", "HEAD").strip(),
        "remote_plan": inspection["remote_plan"],
        "push_commits": pending_push_commits,
        "staged_paths": sorted(current_staged_paths),
        "staged_summary": staged_summary,
        "staged_tree": git(repo, "write-tree").strip(),
    }
    token = write_release_plan(repo, plan)
    return {
        "ok": True,
        "result": "staged",
        "action": action,
        "version": expected_version,
        "branch": inspection["branch"],
        "commit_message": message,
        "staged_paths": sorted(current_staged_paths),
        "staged_summary": staged_summary,
        "checks": check_results,
        "check_source": check_source,
        "plan_token": token,
    }


def publish_command(repo: Path, plan_token: str) -> dict[str, Any]:
    plan = read_release_plan(repo, plan_token)
    branch = validate_git_state(repo)
    if branch != plan["branch"]:
        raise ReleaseError("release_plan_stale", "Current branch changed after staging.")
    if git(repo, "rev-parse", "HEAD").strip() != plan["head"]:
        raise ReleaseError("release_plan_stale", "HEAD changed after staging.")
    config = load_config(repo)
    version = detect_version(repo, config)
    if version.version != plan["expected_version"]:
        raise ReleaseError("release_plan_stale", "Version changed after staging.")
    remote_plan = resolve_remote(repo, config)
    if remote_plan != plan["remote_plan"]:
        raise ReleaseError("release_plan_stale", "Remote or upstream changed after staging.")
    if commits_to_push(repo, remote_plan) != plan["push_commits"]:
        raise ReleaseError("release_plan_stale", "The set of commits to push changed after staging.")
    if sorted(staged_paths(repo)) != plan["staged_paths"]:
        raise ReleaseError("release_plan_stale", "Staged paths changed after staging.")
    if git(repo, "write-tree").strip() != plan["staged_tree"]:
        raise ReleaseError("release_plan_stale", "Staged content changed after staging.")
    leftovers = unstaged_or_untracked_paths(repo)
    if leftovers:
        raise ReleaseError(
            "release_plan_stale",
            "Workspace changed after staging.",
            {"paths": sorted(leftovers)},
        )
    check_sensitive_push_range(repo, remote_plan)

    if plan["action"] == "no-op":
        if plan["staged_paths"] or plan["push_commits"]:
            raise ReleaseError("release_plan_invalid", "No-op release plan contains pending changes.")
        clear_release_plan(repo)
        return {
            "ok": True,
            "result": "no_changes",
            "version": plan["expected_version"],
            "branch": branch,
            "commit": None,
            "staged_summary": "",
            "checks": plan["checks"],
            "push": "not_needed",
        }

    commit: Optional[str] = None
    if plan["action"] == "commit-and-push":
        commit_result = run_process(
            ["git", "commit", "-m", plan["commit_message"]],
            repo,
            check=False,
        )
        if commit_result.returncode:
            raise ReleaseError(
                "commit_failed",
                "Git commit failed; staged changes were preserved. Review hook output locally.",
            )
        commit = git(repo, "rev-parse", "HEAD").strip()
        committed_tree = git(repo, "rev-parse", "HEAD^{tree}").strip()
        committed_message = git(repo, "log", "-1", "--format=%B", "HEAD").rstrip("\n")
        post_commit_paths = parse_status(repo)
        if (
            committed_tree != plan["staged_tree"]
            or committed_message != plan["commit_message"]
            or post_commit_paths
        ):
            clear_release_plan(repo)
            raise ReleaseError(
                "post_commit_mismatch",
                "Git hooks changed the planned commit or workspace; push was not attempted.",
                {
                    "commit": commit,
                    "commit_tree_changed": committed_tree != plan["staged_tree"],
                    "commit_message_changed": committed_message != plan["commit_message"],
                    "workspace_dirty": bool(post_commit_paths),
                },
            )
        check_sensitive_push_range(repo, remote_plan)
    elif plan["action"] != "push-only":
        raise ReleaseError("release_plan_invalid", "Release plan contains an invalid action.")

    pushed, push_output = push_current_branch(repo, remote_plan)
    clear_release_plan(repo)
    if not pushed:
        message = (
            "Push failed; the new local commit was preserved."
            if commit
            else "Push failed; existing local commits were preserved."
        )
        details: dict[str, Any] = {"output": push_output}
        if commit:
            details["commit"] = commit
            details["staged_summary"] = plan["staged_summary"]
        raise ReleaseError("push_failed", message, details)
    result = "committed_and_pushed" if commit else "pushed_existing_commits"
    return {
        "ok": True,
        "result": result,
        "version": plan["expected_version"],
        "branch": branch,
        "commit": commit,
        "staged_summary": plan["staged_summary"],
        "checks": plan["checks"],
        "push": "succeeded",
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    inspect_parser = subparsers.add_parser("inspect")
    inspect_parser.add_argument("--repo", default=".")
    inspect_parser.add_argument(
        "--mode",
        choices=("preview", "publish"),
        default="preview",
    )
    render_parser = subparsers.add_parser("render")
    render_parser.add_argument("--repo", default=".")
    render_parser.add_argument("--notes-file", required=True)
    render_parser.add_argument("--preview", action="store_true")
    stage_parser = subparsers.add_parser("stage")
    stage_parser.add_argument("--repo", default=".")
    stage_parser.add_argument("--expected-version", required=True)
    stage_parser.add_argument(
        "--commit-message",
        help="One-line summary of this commit's changes, without a version number; required for new commits.",
    )
    stage_parser.add_argument("--check-command", action="append", default=[])
    publish_parser = subparsers.add_parser("publish")
    publish_parser.add_argument("--repo", default=".")
    publish_parser.add_argument("--plan-token", required=True)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    try:
        repo = resolve_repo(args.repo)
        if args.command == "inspect":
            emit(inspect_repo(repo, args.mode))
        if args.command == "render":
            emit(
                render_command(
                    repo,
                    Path(args.notes_file).expanduser().resolve(),
                    args.preview,
                )
            )
        if args.command == "stage":
            emit(
                stage_command(
                    repo,
                    args.expected_version,
                    args.check_command,
                    args.commit_message,
                )
            )
        if args.command == "publish":
            emit(publish_command(repo, args.plan_token))
        raise AssertionError("unreachable")
    except ReleaseError as error:
        fail(error)
    except KeyboardInterrupt:
        fail(
            ReleaseError(
                "interrupted",
                "Operation interrupted; no automatic recovery was attempted.",
            )
        )


if __name__ == "__main__":
    main()
