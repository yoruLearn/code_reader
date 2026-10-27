"""Agent tools: read-only inspection plus a narrow, confirmed run command.

The tool layer is the security boundary.  It never trusts paths or command
strings coming from the model.
"""

from __future__ import annotations

import fnmatch
import json
import os
import re
import shlex
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from code_reader.curriculum import find_chapter
from code_reader.repo import (
    RepoInfo,
    is_secret_path,
    read_text_file,
)
from code_reader.state import StateStore

if TYPE_CHECKING:
    from code_reader.ui import UI

MAX_TOOL_OUTPUT = 20_000
MAX_READ_LINES = 400
ALLOWED_PYTHON_MODULES = {"pytest", "ruff", "mypy"}
ALLOWED_MAKE_TARGETS = {"test", "lint", "type", "check"}
SHELL_META = re.compile(r"[;&|<>`]|\$\(|\r|\n")


class ToolError(RuntimeError):
    pass


@dataclass
class ToolResult:
    content: str
    ok: bool = True


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]

    def as_openai(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


def _schema(
    properties: dict[str, Any],
    required: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": required or [],
        "additionalProperties": False,
    }


TOOL_SPECS: tuple[ToolSpec, ...] = (
    ToolSpec(
        name="list_files",
        description="List files under a repository-relative directory. Useful for orientation before reading.",
        parameters=_schema(
            {
                "path": {"type": "string", "description": "Repository-relative directory, default '.'"},
                "depth": {"type": "integer", "description": "Max directory depth, 1-6", "default": 2},
                "limit": {"type": "integer", "description": "Max paths, 1-500", "default": 200},
            }
        ),
    ),
    ToolSpec(
        name="read_file",
        description=(
            "Read a UTF-8 text file with line numbers. Always use this before citing path:line. "
            "Never read .env or credential files."
        ),
        parameters=_schema(
            {
                "path": {"type": "string", "description": "Repository-relative file path"},
                "start_line": {"type": "integer", "description": "1-based first line", "default": 1},
                "end_line": {"type": "integer", "description": "Inclusive last line", "default": 200},
            },
            ["path"],
        ),
    ),
    ToolSpec(
        name="grep",
        description="Search text files with a Python regular expression; returns path:line matches.",
        parameters=_schema(
            {
                "pattern": {"type": "string", "description": "Python regular expression"},
                "path": {"type": "string", "description": "Repository-relative subtree, default '.'"},
                "glob": {"type": "string", "description": "Optional glob such as '*.py'"},
                "ignore_case": {"type": "boolean", "default": False},
                "max_results": {"type": "integer", "default": 50},
            },
            ["pattern"],
        ),
    ),
    ToolSpec(
        name="git_log",
        description="Show recent git commits, optionally limited to a path.",
        parameters=_schema(
            {
                "limit": {"type": "integer", "default": 10},
                "path": {"type": "string", "description": "Optional repository-relative path"},
            }
        ),
    ),
    ToolSpec(
        name="git_show",
        description="Show a commit summary and diff stat.",
        parameters=_schema(
            {
                "rev": {"type": "string", "default": "HEAD"},
            }
        ),
    ),
    ToolSpec(
        name="git_blame",
        description="Show git blame for a line range of a file.",
        parameters=_schema(
            {
                "path": {"type": "string"},
                "start_line": {"type": "integer", "default": 1},
                "end_line": {"type": "integer", "default": 80},
            },
            ["path"],
        ),
    ),
    ToolSpec(
        name="run_command",
        description=(
            "Run an allow-listed test or static-check command. The user must confirm every execution. "
            "Allowed: pytest, python -m pytest|ruff|mypy, ruff, mypy, make test|lint|type|check."
        ),
        parameters=_schema(
            {
                "command": {"type": "string", "description": "Exact command, no shell operators"},
            },
            ["command"],
        ),
    ),
    ToolSpec(
        name="update_progress",
        description="Record chapter status after a lesson. Status: todo, in_progress, done, skipped.",
        parameters=_schema(
            {
                "chapter": {"type": "string", "description": "Chapter id, e.g. '1'"},
                "status": {"type": "string", "enum": ["todo", "in_progress", "done", "skipped"]},
                "note": {"type": "string", "description": "Optional durable note", "default": ""},
            },
            ["chapter", "status"],
        ),
    ),
    ToolSpec(
        name="save_note",
        description="Append a durable learning note to .code-reader/notes.md.",
        parameters=_schema(
            {
                "title": {"type": "string"},
                "content": {"type": "string"},
            },
            ["title", "content"],
        ),
    ),
    ToolSpec(
        name="record_quiz",
        description="Record a quiz score after grading, for later /progress review.",
        parameters=_schema(
            {
                "score": {"type": "string", "description": "e.g. '2/3' or '6/10'"},
                "summary": {"type": "string", "description": "One sentence on what to review"},
            },
            ["score"],
        ),
    ),
    ToolSpec(
        name="get_progress",
        description="Read the current chapter and learning progress as JSON.",
        parameters=_schema({}),
    ),
)


class ToolRegistry:
    def __init__(
        self,
        repo: RepoInfo,
        state: StateStore,
        ui: UI,
        *,
        confirm: Callable[[str, str], bool] | None = None,
    ) -> None:
        self.repo = repo
        self.state = state
        self.ui = ui
        self.confirm = confirm
        self._specs = {spec.name: spec for spec in TOOL_SPECS}

    def specs(self) -> list[dict[str, Any]]:
        return [spec.as_openai() for spec in TOOL_SPECS]

    def execute(self, name: str, arguments: dict[str, Any] | None = None) -> ToolResult:
        spec = self._specs.get(name)
        if spec is None:
            return ToolResult(f"ERROR: 未知工具 `{name}`", ok=False)
        args = arguments or {}
        if not isinstance(args, dict):
            return ToolResult("ERROR: 工具参数必须是 object", ok=False)
        try:
            handler = getattr(self, f"_tool_{name}")
            result = handler(args)
        except ToolError as exc:
            return ToolResult(f"ERROR: {exc}", ok=False)
        except Exception as exc:  # defensive: never crash the agent loop on a tool
            return ToolResult(f"ERROR: 工具执行失败：{type(exc).__name__}: {exc}", ok=False)
        if isinstance(result, ToolResult):
            return result
        return ToolResult(str(result))

    # ------------------------------------------------------------------ paths

    def _resolve(self, raw: str, *, must_exist: bool = True) -> Path:
        text = (raw or ".").strip()
        candidate = Path(text)
        if candidate.is_absolute():
            candidate = candidate.resolve()
        else:
            candidate = (self.repo.root / candidate).resolve()
        root = self.repo.root.resolve()
        if not candidate.is_relative_to(root):
            raise ToolError(f"路径越界，拒绝访问：{raw}")
        parts = candidate.relative_to(root).parts
        if ".git" in parts:
            raise ToolError("拒绝读取 .git 内部文件；请使用 git_log/git_show/git_blame")
        if is_secret_path(candidate):
            raise ToolError("拒绝访问密钥/凭据文件")
        if must_exist and not candidate.exists():
            raise ToolError(f"路径不存在：{raw}")
        return candidate

    def _relative(self, path: Path) -> str:
        return path.resolve().relative_to(self.repo.root.resolve()).as_posix()

    def _read_text(self, path: Path) -> str:
        text = read_text_file(path)
        if text is None:
            raise ToolError(f"无法读取为文本文件（可能是二进制或过大）：{self._relative(path)}")
        return text

    # ------------------------------------------------------------------ read tools

    def _tool_list_files(self, args: dict[str, Any]) -> str:
        base = self._resolve(str(args.get("path", ".")))
        if base.is_file():
            return f"{self._relative(base)}  ({base.stat().st_size} B)"
        base_rel = self._relative(base) if base != self.repo.root else ""
        depth = _clamp_int(args.get("depth"), 2, 1, 6)
        limit = _clamp_int(args.get("limit"), 200, 1, 500)
        prefix = f"{base_rel}/" if base_rel else ""
        rows: list[str] = []
        for file in self.repo.files:
            if base_rel and not (file.path == base_rel or file.path.startswith(prefix)):
                continue
            relative = file.path[len(prefix) :] if prefix else file.path
            if relative.count("/") + 1 > depth:
                continue
            rows.append(f"{file.path}  ({file.size} B)")
            if len(rows) >= limit:
                break
        if not rows:
            return f"{base_rel or '.'} 下没有匹配文件（可能被忽略或未扫描）。"
        header = f"{base_rel or '.'} 下 {len(rows)} 个文件：\n"
        return header + "\n".join(rows)

    def _tool_read_file(self, args: dict[str, Any]) -> str:
        path = self._resolve(str(args.get("path", "")))
        if not path.is_file():
            raise ToolError(f"不是文件：{args.get('path')}")
        text = self._read_text(path)
        lines = text.replace("\r\n", "\n").replace("\r", "\n").splitlines()
        total = len(lines)
        if total == 0:
            return f"# {self._relative(path)}\n(空文件)"
        start = _clamp_int(args.get("start_line"), 1, 1, max(total, 1))
        requested_end = _clamp_int(args.get("end_line"), min(start + 199, total), start, total)
        end = min(requested_end, start + MAX_READ_LINES - 1, total)
        numbered = [f"{number:>5} | {lines[number - 1]}" for number in range(start, end + 1)]
        header = f"# {self._relative(path)} (lines {start}-{end} of {total})"
        if end < total:
            header += f"\n# 继续读取：start_line={end + 1}"
        return header + "\n" + "\n".join(numbered)

    def _tool_grep(self, args: dict[str, Any]) -> str:
        pattern = str(args.get("pattern", ""))
        if not pattern:
            raise ToolError("pattern 不能为空")
        if len(pattern) > 300:
            raise ToolError("pattern 过长")
        flags = re.IGNORECASE if bool(args.get("ignore_case", False)) else 0
        try:
            regex = re.compile(pattern, flags)
        except re.error as exc:
            raise ToolError(f"正则表达式无效：{exc}") from exc
        base = self._resolve(str(args.get("path", ".")))
        base_rel = self._relative(base) if base != self.repo.root else ""
        prefix = f"{base_rel}/" if base_rel else ""
        glob = str(args.get("glob", "") or "")
        max_results = _clamp_int(args.get("max_results"), 50, 1, 100)
        rows: list[str] = []
        scanned = 0
        for file in self.repo.files:
            if base_rel and not (file.path == base_rel or file.path.startswith(prefix)):
                continue
            if glob and not (
                fnmatch.fnmatch(file.path, glob) or fnmatch.fnmatch(Path(file.path).name, glob)
            ):
                continue
            if file.size > 500_000:
                continue
            scanned += 1
            if scanned > 3000:
                break
            path = self.repo.root / file.path
            if is_secret_path(path):
                continue
            text = read_text_file(path, max_bytes=500_000)
            if text is None:
                continue
            for number, line in enumerate(text.splitlines(), 1):
                if regex.search(line):
                    rows.append(f"{file.path}:{number}: {line[:300]}")
                    if len(rows) >= max_results:
                        break
            if len(rows) >= max_results:
                break
        if not rows:
            return "没有匹配。可以换 pattern、扩大 path 或去掉 glob。"
        suffix = f"\n... 已达到上限 {max_results}" if len(rows) >= max_results else ""
        return "\n".join(rows) + suffix

    # ------------------------------------------------------------------ git tools

    def _git(self, args: list[str], *, timeout: int = 20) -> str:
        if not self.repo.is_git:
            raise ToolError("当前目录不是 git 仓库")
        try:
            result = subprocess.run(
                ["git", "--no-pager", "-C", str(self.repo.root), *args],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise ToolError(f"git 执行失败：{exc}") from exc
        output = (result.stdout or "") + (result.stderr or "")
        output = output.strip() or "(无输出)"
        if len(output) > MAX_TOOL_OUTPUT:
            output = output[:MAX_TOOL_OUTPUT] + "\n... (truncated)"
        if result.returncode != 0:
            raise ToolError(output)
        return output

    def _tool_git_log(self, args: dict[str, Any]) -> str:
        limit = _clamp_int(args.get("limit"), 10, 1, 30)
        path = str(args.get("path", "") or "").strip()
        git_args = ["log", "--oneline", "--decorate", "-n", str(limit)]
        if path:
            self._resolve(path)
            git_args.extend(["--", path])
        return self._git(git_args)

    def _tool_git_show(self, args: dict[str, Any]) -> str:
        rev = str(args.get("rev", "HEAD") or "HEAD").strip()
        if not re.fullmatch(r"[A-Za-z0-9_./~^@{}:\-]{1,140}", rev) or rev.startswith("-"):
            raise ToolError("非法的 revision")
        return self._git(["show", "--stat", "--format=fuller", rev])

    def _tool_git_blame(self, args: dict[str, Any]) -> str:
        path = self._resolve(str(args.get("path", "")))
        if not path.is_file():
            raise ToolError("不是文件")
        start = _clamp_int(args.get("start_line"), 1, 1, 1_000_000)
        end = _clamp_int(args.get("end_line"), start + 79, start, 1_000_000)
        return self._git(
            ["blame", "-L", f"{start},{end}", "--", self._relative(path)],
        )

    # ------------------------------------------------------------------ run

    def _project_python(self) -> str:
        """Prefer the target repo's virtualenv over code-reader's interpreter."""

        candidates = (
            ".venv/Scripts/python.exe",
            ".venv/bin/python",
            "venv/Scripts/python.exe",
            "venv/bin/python",
        )
        for relative in candidates:
            path = self.repo.root / relative
            if path.is_file():
                return str(path)
        return sys.executable

    def _tool_run_command(self, args: dict[str, Any]) -> ToolResult:
        command = str(args.get("command", "")).strip()
        if not command:
            raise ToolError("command 不能为空")
        argv, reason = validate_command(command)
        if argv is None:
            return ToolResult(f"ERROR: {reason}", ok=False)
        if self.confirm is None:
            return ToolResult("ERROR: 当前会话不允许执行命令", ok=False)

        if argv and argv[0] == sys.executable:
            argv = [self._project_python(), *argv[1:]]
        preview = " ".join(argv)
        question = (
            f"允许执行以下命令吗？\n  {preview}\n"
            f"  工作目录: {self.repo.root}\n"
            f"  目的: {reason}\n"
            "  可能副作用: 测试可能连接数据库/Redis，或写入测试数据/缓存。"
        )
        if not self.confirm(question, "run"):
            return ToolResult("用户拒绝执行该命令。", ok=False)

        env = dict(os.environ)
        for secret_name in (
            "CODE_READER_API_KEY",
            "DEEPSEEK_API_KEY",
            "OPENAI_API_KEY",
        ):
            env.pop(secret_name, None)
        env["PYTHONUNBUFFERED"] = "1"
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        env["NO_COLOR"] = "1"
        try:
            result = subprocess.run(
                argv,
                cwd=self.repo.root,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=180,
                check=False,
                env=env,
            )
        except subprocess.TimeoutExpired:
            return ToolResult("ERROR: 命令超过 180 秒，已终止。", ok=False)
        except OSError as exc:
            return ToolResult(f"ERROR: 命令启动失败：{exc}", ok=False)

        stdout = (result.stdout or "").strip()
        stderr = (result.stderr or "").strip()
        combined = (
            f"$ {preview}\n"
            f"cwd: {self.repo.root}\n"
            f"exit: {result.returncode}\n"
            f"--- stdout ---\n{stdout or '(empty)'}\n"
            f"--- stderr ---\n{stderr or '(empty)'}"
        )
        if len(combined) > MAX_TOOL_OUTPUT:
            combined = combined[:MAX_TOOL_OUTPUT] + "\n... (truncated)"
        return ToolResult(combined, ok=True)

    # ------------------------------------------------------------------ progress

    def _tool_update_progress(self, args: dict[str, Any]) -> str:
        chapter_raw = str(args.get("chapter", "")).strip()
        status = str(args.get("status", "")).strip()
        chapter = find_chapter(chapter_raw)
        if chapter is None:
            valid = ", ".join(f"{item.id}={item.title}" for item in _chapters())
            raise ToolError(f"未知章节 `{chapter_raw}`。可用：{valid}")
        note = str(args.get("note", "") or "")
        self.state.mark_chapter(chapter.id, status, note)
        return f"已更新第 {chapter.id} 章《{chapter.title}》为 {status}。"

    def _tool_save_note(self, args: dict[str, Any]) -> str:
        title = str(args.get("title", "")).strip()
        content = str(args.get("content", "")).strip()
        if not title or not content:
            raise ToolError("title 和 content 都不能为空")
        if len(content) > 20_000:
            raise ToolError("content 过长（上限 20000 字符）")
        self.state.append_note(title, content)
        return f"已写入笔记：{title}"

    def _tool_record_quiz(self, args: dict[str, Any]) -> str:
        score = str(args.get("score", "")).strip()
        summary = str(args.get("summary", "") or "").strip()
        if not score:
            raise ToolError("score 不能为空")
        self.state.record_quiz(score)
        if summary:
            self.state.append_note("测验复盘", f"得分：{score}\n\n{summary}")
        return f"已记录测验：{score}"

    def _tool_get_progress(self, _args: dict[str, Any]) -> str:
        return json.dumps(self.state.progress, ensure_ascii=False, indent=2)


def _chapters() -> list[Any]:
    from code_reader.curriculum import CHAPTERS

    return list(CHAPTERS)


def _clamp_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    return max(minimum, min(number, maximum))


def validate_command(command: str) -> tuple[list[str] | None, str]:
    """Return ``(argv, reason)`` or ``(None, error)``.

    This is intentionally narrow: it is a reading tutor, not a general shell.
    """

    if SHELL_META.search(command):
        return None, "不允许 shell 组合、管道、重定向或换行"
    try:
        parts = shlex.split(command, posix=os.name != "nt")
    except ValueError as exc:
        return None, f"命令解析失败：{exc}"
    if not parts:
        return None, "命令为空"
    parts = [_strip_quotes(part) for part in parts]
    first = Path(parts[0]).name.lower()
    if first.endswith(".exe"):
        first = first[:-4]

    if first == "pytest":
        argv = [sys.executable, "-m", "pytest", *parts[1:]]
        return _with_pytest_defaults(argv), "运行测试"
    if first in {"ruff", "mypy"}:
        if first == "ruff" and any(arg in {"--fix", "--unsafe-fixes"} for arg in parts[1:]):
            return None, "不允许 ruff 的写文件修复选项（--fix / --unsafe-fixes）"
        return [sys.executable, "-m", first, *parts[1:]], f"运行 {first} 静态检查"
    if first in {"python", "py"}:
        if len(parts) < 3 or parts[1] != "-m":
            return None, "只允许 `python -m pytest|ruff|mypy`，不允许 -c 或任意脚本"
        module = parts[2].lower()
        if module not in ALLOWED_PYTHON_MODULES:
            return None, f"只允许 python -m {', '.join(sorted(ALLOWED_PYTHON_MODULES))}"
        if module == "ruff" and any(arg in {"--fix", "--unsafe-fixes"} for arg in parts[3:]):
            return None, "不允许 ruff 的写文件修复选项（--fix / --unsafe-fixes）"
        argv = [sys.executable, "-m", module, *parts[3:]]
        if module == "pytest":
            argv = _with_pytest_defaults(argv)
        return argv, f"运行 {module}"
    if first == "make":
        if len(parts) < 2 or parts[1] not in ALLOWED_MAKE_TARGETS:
            return None, f"只允许 make {', '.join(sorted(ALLOWED_MAKE_TARGETS))}"
        return ["make", *parts[1:]], "运行 Makefile 目标"
    return None, (
        "命令不在白名单内。允许：pytest、python -m pytest|ruff|mypy、"
        "ruff、mypy、make test|lint|type|check"
    )


def _with_pytest_defaults(argv: list[str]) -> list[str]:
    if "no:cacheprovider" in argv:
        return argv
    return [*argv, "-p", "no:cacheprovider"]


def _strip_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value
