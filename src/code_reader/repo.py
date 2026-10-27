"""Repository discovery, scanning, and context building.

The scanner is deliberately cheap: it does not parse ASTs or build an index.
It gathers enough facts for a one-page briefing and for the agent to decide
which files to read next.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import tomllib
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

MAX_SCAN_FILES = 5000
MAX_FILE_BYTES = 1_000_000

SKIP_DIRS = {
    ".git",
    ".hg",
    ".svn",
    ".code-reader",
    ".venv",
    "venv",
    ".tox",
    ".nox",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "__pycache__",
    "node_modules",
    "bower_components",
    "dist",
    "build",
    "target",
    "vendor",
    "bin",
    "obj",
    ".idea",
    ".vscode",
    ".next",
    ".nuxt",
    ".svelte-kit",
    "coverage",
    "htmlcov",
    "site-packages",
    ".eggs",
}

SKIP_FILES = {
    "uv.lock",
    "poetry.lock",
    "Pipfile.lock",
    "package-lock.json",
    "pnpm-lock.yaml",
    "yarn.lock",
    "Cargo.lock",
    "go.sum",
    "composer.lock",
    "Gemfile.lock",
    ".coverage",
    "coverage.xml",
    ".DS_Store",
    "Thumbs.db",
}

BINARY_SUFFIXES = {
    ".pyc",
    ".pyo",
    ".so",
    ".dll",
    ".exe",
    ".bin",
    ".zip",
    ".tar",
    ".gz",
    ".tgz",
    ".7z",
    ".rar",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".ico",
    ".pdf",
    ".woff",
    ".woff2",
    ".ttf",
    ".eot",
    ".mp4",
    ".mp3",
    ".wav",
    ".db",
    ".sqlite",
    ".sqlite3",
    ".class",
    ".jar",
    ".war",
    ".pkl",
    ".npy",
    ".npz",
    ".parquet",
}

SAFE_ENV_FILES = {".env.example", ".env.sample", ".env.template"}

MANIFEST_NAMES = {
    "pyproject.toml",
    "setup.py",
    "setup.cfg",
    "requirements.txt",
    "requirements-dev.txt",
    "package.json",
    "go.mod",
    "Cargo.toml",
    "pom.xml",
    "build.gradle",
    "build.gradle.kts",
    "Gemfile",
    "composer.json",
    "Dockerfile",
    "docker-compose.yml",
    "docker-compose.yaml",
    "compose.yml",
    "compose.yaml",
    "Makefile",
}

LANG_BY_SUFFIX = {
    ".py": "Python",
    ".pyi": "Python",
    ".js": "JavaScript",
    ".mjs": "JavaScript",
    ".cjs": "JavaScript",
    ".jsx": "JavaScript/React",
    ".ts": "TypeScript",
    ".tsx": "TypeScript/React",
    ".vue": "Vue",
    ".svelte": "Svelte",
    ".go": "Go",
    ".rs": "Rust",
    ".java": "Java",
    ".kt": "Kotlin",
    ".kts": "Kotlin",
    ".scala": "Scala",
    ".rb": "Ruby",
    ".php": "PHP",
    ".cs": "C#",
    ".fs": "F#",
    ".vb": "VB.NET",
    ".c": "C",
    ".h": "C/C++ header",
    ".cc": "C++",
    ".cpp": "C++",
    ".cxx": "C++",
    ".hpp": "C++",
    ".m": "Objective-C",
    ".mm": "Objective-C++",
    ".swift": "Swift",
    ".dart": "Dart",
    ".lua": "Lua",
    ".r": "R",
    ".pl": "Perl",
    ".sh": "Shell",
    ".ps1": "PowerShell",
    ".sql": "SQL",
    ".html": "HTML",
    ".css": "CSS",
    ".scss": "SCSS",
    ".md": "Markdown",
    ".toml": "TOML",
    ".yaml": "YAML",
    ".yml": "YAML",
    ".json": "JSON",
    ".ini": "Config",
    ".cfg": "Config",
}

FRAMEWORK_HINTS = {
    "fastapi": "FastAPI",
    "starlette": "Starlette/ASGI",
    "uvicorn": "Uvicorn",
    "langgraph": "LangGraph",
    "langchain": "LangChain",
    "sqlalchemy": "SQLAlchemy",
    "alembic": "Alembic",
    "aiomysql": "MySQL/aiomysql",
    "pymysql": "MySQL/PyMySQL",
    "redis": "Redis",
    "rocketmq": "RocketMQ",
    "kafka": "Kafka",
    "chroma": "Chroma",
    "faiss": "FAISS",
    "pgvector": "pgvector",
    "pytest": "pytest",
    "django": "Django",
    "flask": "Flask",
    "pydantic": "Pydantic",
    "prometheus": "Prometheus",
    "react": "React",
    "next": "Next.js",
    "vue": "Vue",
    "svelte": "Svelte",
}


@dataclass(frozen=True)
class RepoFile:
    path: str
    size: int
    language: str


@dataclass
class RepoInfo:
    root: Path
    name: str
    is_git: bool
    git_branch: str | None
    git_head: str | None
    git_status: str
    files: list[RepoFile]
    languages: dict[str, int]
    manifests: list[str]
    docs: list[str]
    entry_candidates: list[str]
    run_commands: list[str]
    test_paths: list[str]
    key_excerpts: dict[str, str]
    tree: str
    framework_hints: list[str]
    warnings: list[str] = field(default_factory=list)
    truncated: bool = False
    total_files: int = 0
    total_bytes: int = 0

    def context(self, max_chars: int = 36_000) -> str:
        languages = ", ".join(f"{name} {count}" for name, count in self.languages.items())
        lines = [
            f"REPO: {self.name}",
            f"ROOT: {self.root}",
            f"GIT: {'yes' if self.is_git else 'no'}"
            f"{f' | branch={self.git_branch}' if self.git_branch else ''}"
            f"{f' | head={self.git_head}' if self.git_head else ''}",
        ]
        if self.git_status:
            status_lines = self.git_status.splitlines()
            if len(status_lines) > 12:
                status_lines = [*status_lines[:12], "... (truncated)"]
            lines.append("GIT STATUS:\n" + "\n".join(status_lines))
        lines.extend(
            [
                f"FILES: {self.total_files} total, {len(self.files)} scanned"
                f"{' (truncated)' if self.truncated else ''}",
                f"BYTES: {self.total_bytes}",
                f"LANGUAGES: {languages or 'unknown'}",
                f"FRAMEWORKS: {', '.join(self.framework_hints) or 'not detected'}",
                f"MANIFESTS: {', '.join(self.manifests) or 'none'}",
                f"ENTRY CANDIDATES: {', '.join(self.entry_candidates) or 'unknown'}",
                f"RUN COMMANDS: {' | '.join(self.run_commands) or 'unknown'}",
                f"TESTS: {', '.join(self.test_paths[:12]) or 'not detected'}",
                "TREE:",
                self.tree,
            ]
        )
        if self.docs:
            lines.append("DOCS: " + ", ".join(self.docs[:20]))
        if self.warnings:
            lines.append("WARNINGS: " + " | ".join(self.warnings))
        lines.append("KEY EXCERPTS:")
        for path, excerpt in self.key_excerpts.items():
            lines.append(f"\n--- {path} ---\n{excerpt}")
        text = "\n".join(lines)
        if len(text) > max_chars:
            return text[:max_chars] + "\n... (context truncated)"
        return text


def find_repo_root(start: Path) -> tuple[Path, bool]:
    """Return ``(repo_root, is_git)`` for a path inside a repository."""

    path = start.expanduser()
    if path.is_file():
        path = path.parent
    path = path.resolve()
    if not path.exists():
        raise FileNotFoundError(f"路径不存在: {path}")
    try:
        result = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return path, False
    if result.returncode == 0 and result.stdout.strip():
        return Path(result.stdout.strip()).resolve(), True
    return path, False


def is_secret_path(path: str | Path) -> bool:
    name = Path(path).name.lower()
    if name in SAFE_ENV_FILES:
        return False
    if name.startswith(".env"):
        return True
    if name in {
        "id_rsa",
        "id_dsa",
        "id_ecdsa",
        "id_ed25519",
        ".netrc",
        ".npmrc",
        ".pypirc",
        "credentials.json",
        "credentials.yaml",
        "credentials.yml",
        "secrets.json",
        "secrets.yaml",
        "secrets.yml",
        "secret.yaml",
        "secret.yml",
    }:
        return True
    suffix = Path(path).suffix.lower()
    return suffix in {".pem", ".key", ".p12", ".pfx", ".jks", ".keystore"}


def should_skip_dir(name: str) -> bool:
    lowered = name.lower()
    return lowered in SKIP_DIRS or lowered.endswith(".egg-info")


def should_skip_file(path: Path) -> bool:
    name = path.name
    if name in SKIP_FILES:
        return True
    if is_secret_path(name):
        return True
    return path.suffix.lower() in BINARY_SUFFIXES


def is_binary_text(text: str) -> bool:
    return "\x00" in text[:2048]


def read_text_file(path: Path, *, max_bytes: int = MAX_FILE_BYTES) -> str | None:
    try:
        if not path.is_file() or path.stat().st_size > max_bytes:
            return None
        raw = path.read_bytes()
    except OSError:
        return None
    if b"\x00" in raw[:2048]:
        return None
    return raw.decode("utf-8", errors="replace")


def language_for(path: Path) -> str:
    if path.name in {"Dockerfile", "Makefile", "Gemfile"}:
        return path.name
    return LANG_BY_SUFFIX.get(path.suffix.lower(), "Other")


def _git_output(root: Path, args: list[str], *, timeout: int = 15) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    if result.returncode != 0:
        return ""
    return result.stdout.strip()


def _git_files(root: Path) -> list[str]:
    try:
        result = subprocess.run(
            [
                "git",
                "-C",
                str(root),
                "ls-files",
                "-z",
                "--cached",
                "--others",
                "--exclude-standard",
            ],
            capture_output=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if result.returncode != 0:
        return []
    paths: list[str] = []
    for raw in result.stdout.split(b"\0"):
        if not raw:
            continue
        paths.append(raw.decode("utf-8", errors="replace").replace("\\", "/"))
    return paths


def _walk_files(root: Path) -> list[str]:
    paths: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [name for name in dirnames if not should_skip_dir(name)]
        current = Path(dirpath)
        for filename in filenames:
            path = current / filename
            if should_skip_file(path):
                continue
            try:
                rel = path.relative_to(root).as_posix()
            except ValueError:
                continue
            paths.append(rel)
    return paths


def _detect_manifests(paths: list[str]) -> list[str]:
    manifests = [
        path
        for path in paths
        if Path(path).name in MANIFEST_NAMES and path.count("/") <= 2
    ]
    return sorted(manifests, key=lambda item: (item.count("/"), item))[:16]


def _detect_docs(paths: list[str]) -> list[str]:
    candidates: list[str] = []
    for path in paths:
        lower = path.lower()
        name = Path(path).name.lower()
        if (name.startswith("readme") or lower.endswith((".md", ".rst"))) and (
            name.startswith("readme")
            or lower.startswith("docs/")
            or lower.startswith("doc/")
            or name in {"architecture.md", "contributing.md", "changelog.md"}
            or "spec" in name
            or "adr" in lower
        ):
            candidates.append(path)
    def priority(path: str) -> tuple[int, int, str]:
        lower = path.lower()
        if Path(path).name.lower().startswith("readme"):
            return (0, path.count("/"), path)
        if "requirements" in lower:
            return (1, path.count("/"), path)
        if "verification" in lower:
            return (2, path.count("/"), path)
        if "spec" in lower:
            return (3, path.count("/"), path)
        if "adr" in lower:
            return (4, path.count("/"), path)
        return (5, path.count("/"), path)

    return sorted(set(candidates), key=priority)[:24]


def _detect_entries(paths: list[str], manifests: list[str], root: Path) -> list[str]:
    path_set = set(paths)
    priority = [
        "app/main.py",
        "main.py",
        "src/main.py",
        "manage.py",
        "asgi.py",
        "wsgi.py",
        "server.py",
        "app.py",
        "run.py",
        "cli.py",
        "src/cli.py",
    ]
    entries: list[str] = []
    for candidate in priority:
        if candidate in path_set:
            entries.append(candidate)
    for path in paths:
        name = Path(path).name
        if name == "__main__.py" or (path.startswith("src/") and path.count("/") <= 2 and name in {"main.py", "cli.py"}):
            entries.append(path)
    for manifest in manifests:
        if Path(manifest).name != "pyproject.toml":
            continue
        try:
            with (root / manifest).open("rb") as handle:
                data = tomllib.load(handle)
        except (OSError, tomllib.TOMLDecodeError):
            continue
        project = data.get("project", {})
        if not isinstance(project, dict):
            continue
        scripts = project.get("scripts", {})
        if isinstance(scripts, dict):
            for target in scripts.values():
                if not isinstance(target, str):
                    continue
                module = target.split(":", 1)[0].strip()
                candidate = module.replace(".", "/") + ".py"
                if candidate in path_set:
                    entries.append(candidate)
    deduped: list[str] = []
    for entry in entries:
        if entry not in deduped:
            deduped.append(entry)
    return deduped[:10]


def _extract_run_commands(root: Path, docs: list[str], manifests: list[str]) -> list[str]:
    texts: list[str] = []
    for path in docs[:6]:
        text = read_text_file(root / path, max_bytes=200_000)
        if text:
            texts.append(text)
    for manifest in manifests:
        if Path(manifest).name in {"Makefile", "package.json", "Dockerfile"}:
            text = read_text_file(root / manifest, max_bytes=100_000)
            if text:
                texts.append(text)
    prefixes = (
        "uvicorn ",
        "gunicorn ",
        "python -m ",
        "python3 -m ",
        "npm run ",
        "pnpm ",
        "go run ",
        "cargo run",
        "make ",
        "docker compose up",
        "docker-compose up",
    )
    commands: list[str] = []
    for raw_line in "\n".join(texts).splitlines():
        line = raw_line.strip().strip("`").strip()
        line = re.sub(r"^\$\s*", "", line)
        line = re.sub(r"^(?:uv|poetry|pdm)\s+run\s+", "", line, flags=re.IGNORECASE)
        line = line.split("`", 1)[0].strip()
        line = re.sub(r"\s+#.*$", "", line).strip()
        line = line.rstrip("。；,;")
        if not line.lower().startswith(prefixes):
            continue
        command = re.sub(r"\s+", " ", line).strip()
        if command and command not in commands:
            commands.append(command)
        if len(commands) >= 10:
            break
    return commands


def _detect_frameworks(root: Path, manifests: list[str], paths: list[str]) -> list[str]:
    text_parts: list[str] = []
    for manifest in manifests:
        text = read_text_file(root / manifest, max_bytes=200_000)
        if text:
            text_parts.append(text.lower())
    for path in paths:
        if Path(path).suffix.lower() in {".py", ".ts", ".js", ".go", ".java"}:
            if len(text_parts) > 20:
                break
            text = read_text_file(root / path, max_bytes=100_000)
            if text:
                text_parts.append(text.lower())
    blob = "\n".join(text_parts)
    hints = [label for needle, label in FRAMEWORK_HINTS.items() if needle in blob]
    deduped: list[str] = []
    for hint in hints:
        if hint not in deduped:
            deduped.append(hint)
    return deduped[:12]


def _detect_tests(paths: list[str]) -> list[str]:
    tests: list[str] = []
    for path in paths:
        name = Path(path).name.lower()
        lower = path.lower()
        if (
            lower.startswith("tests/")
            or lower.startswith("test/")
            or "/tests/" in lower
            or name.startswith("test_")
            or name.endswith("_test.py")
            or name.endswith(".test.ts")
            or name.endswith(".test.js")
            or name.endswith("_test.go")
        ):
            tests.append(path)
    return sorted(set(tests))[:30]


def _build_tree(paths: list[str], *, max_lines: int = 30) -> str:
    top: Counter[str] = Counter()
    second: Counter[str] = Counter()
    root_files: list[str] = []
    for path in paths:
        parts = path.split("/")
        if len(parts) == 1:
            root_files.append(path)
            continue
        top[parts[0]] += 1
        if len(parts) > 2:
            second[f"{parts[0]}/{parts[1]}"] += 1
    lines: list[str] = []
    for directory, count in top.most_common(max_lines):
        lines.append(f"{directory}/ ({count} files)")
        children = [
            (child, child_count)
            for child, child_count in second.items()
            if child.startswith(directory + "/")
        ]
        children.sort(key=lambda item: (-item[1], item[0]))
        for child, child_count in children[:4]:
            lines.append(f"  {child.split('/', 1)[1]}/ ({child_count})")
        if len(lines) >= max_lines:
            lines.append("  ...")
            break
    if root_files:
        lines.append("root files: " + ", ".join(sorted(root_files)[:10]))
    return "\n".join(lines) or "(empty)"


def _pick_excerpts(root: Path, paths: list[str], docs: list[str], entries: list[str]) -> dict[str, str]:
    path_set = set(paths)
    chosen: list[tuple[str, int]] = []
    for doc in docs:
        name = Path(doc).name.lower()
        if name.startswith("readme"):
            chosen.append((doc, 8000))
        elif "requirements" in name or "verification" in name:
            chosen.append((doc, 6000))
        elif "spec" in name:
            chosen.append((doc, 8000))
        elif "adr" in doc.lower():
            chosen.append((doc, 5000))
    for manifest in ("pyproject.toml", "package.json", "go.mod", "Cargo.toml", "Makefile"):
        if manifest in path_set:
            chosen.append((manifest, 5000))
    for entry in entries[:3]:
        chosen.append((entry, 4000))
    for doc in docs:
        if len(chosen) >= 12:
            break
        if (doc, 6000) not in chosen and all(doc != item[0] for item in chosen):
            chosen.append((doc, 4000))
    excerpts: dict[str, str] = {}
    for path, limit in chosen[:12]:
        text = read_text_file(root / path)
        if not text:
            continue
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        if len(text) > limit:
            text = text[:limit] + "\n... (truncated)"
        excerpts[path] = text
    return excerpts


def scan_repo(
    root: Path,
    *,
    max_files: int = MAX_SCAN_FILES,
    max_file_bytes: int = MAX_FILE_BYTES,
) -> RepoInfo:
    root = root.resolve()
    is_git = bool(_git_output(root, ["rev-parse", "--is-inside-work-tree"]))
    raw_paths = _git_files(root) if is_git else _walk_files(root)
    if not raw_paths:
        # Newly initialized git repos, or repos whose only files are ignored.
        raw_paths = _walk_files(root)

    paths: list[str] = []
    total_bytes = 0
    truncated = False
    for path in raw_paths:
        full = root / path
        if any(should_skip_dir(part) for part in Path(path).parts[:-1]):
            continue
        if not full.is_file() or should_skip_file(full):
            continue
        try:
            size = full.stat().st_size
        except OSError:
            continue
        if size > max_file_bytes:
            continue
        total_bytes += size
        if len(paths) >= max_files:
            truncated = True
            break
        paths.append(path)
    paths = sorted(set(paths))
    total_files = len(paths)

    files = [
        RepoFile(
            path=path,
            size=(root / path).stat().st_size,
            language=language_for(Path(path)),
        )
        for path in paths
        if (root / path).exists()
    ]
    languages_counter = Counter(file.language for file in files if file.language != "Other")
    languages = dict(languages_counter.most_common(8))
    manifests = _detect_manifests(paths)
    docs = _detect_docs(paths)
    entries = _detect_entries(paths, manifests, root)
    run_commands = _extract_run_commands(root, docs, manifests)
    tests = _detect_tests(paths)
    frameworks = _detect_frameworks(root, manifests, paths)
    excerpts = _pick_excerpts(root, paths, docs, entries)
    tree = _build_tree(paths)

    warnings: list[str] = []
    if truncated:
        warnings.append(f"文件数超过 {max_files}，只扫描了前 {max_files} 个")
    if not docs:
        warnings.append("未发现 README/文档")
    if not entries:
        warnings.append("未识别到明确入口，可能需要人工确认")
    if not tests:
        warnings.append("未发现测试目录")

    return RepoInfo(
        root=root,
        name=root.name,
        is_git=is_git,
        git_branch=_git_output(root, ["rev-parse", "--abbrev-ref", "HEAD"]) or None,
        git_head=_git_output(root, ["rev-parse", "--short", "HEAD"]) or None,
        git_status="\n".join(
            line
            for line in _git_output(root, ["status", "--short"]).splitlines()
            if ".code-reader/" not in line
        ),
        files=files,
        languages=languages,
        manifests=manifests,
        docs=docs,
        entry_candidates=entries,
        run_commands=run_commands,
        test_paths=tests,
        key_excerpts=excerpts,
        tree=tree,
        framework_hints=frameworks,
        warnings=warnings,
        truncated=truncated,
        total_files=total_files,
        total_bytes=total_bytes,
    )


def parse_pyproject(root: Path) -> dict[str, Any]:
    """Small helper used by tests and by callers that need project metadata."""

    path = root / "pyproject.toml"
    if not path.exists():
        return {}
    try:
        with path.open("rb") as handle:
            return tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError):
        return {}


def parse_package_json(root: Path) -> dict[str, Any]:
    path = root / "package.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}
