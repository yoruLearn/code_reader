from __future__ import annotations

from pathlib import Path

from code_reader.repo import scan_repo
from code_reader.state import StateStore
from code_reader.tools import ToolRegistry, validate_command
from code_reader.ui import UI


def _registry(tmp_repo: Path, confirmed: bool = True) -> tuple[ToolRegistry, StateStore, UI]:
    repo = scan_repo(tmp_repo)
    state = StateStore(tmp_repo)
    ui = UI()
    registry = ToolRegistry(repo, state, ui, confirm=lambda _prompt, _reason: confirmed)
    return registry, state, ui


def test_read_file_has_line_numbers(tmp_repo: Path) -> None:
    registry, _, _ = _registry(tmp_repo)
    result = registry.execute("read_file", {"path": "app/main.py"})
    assert result.ok
    assert "1 |" in result.content
    assert "def main()" in result.content


def test_path_traversal_and_secrets_are_rejected(tmp_repo: Path) -> None:
    registry, _, _ = _registry(tmp_repo)
    outside = registry.execute("read_file", {"path": "../../Windows/win.ini"})
    secret = registry.execute("read_file", {"path": ".env"})
    assert not outside.ok
    assert "越界" in outside.content
    assert not secret.ok
    assert "密钥" in secret.content


def test_grep_and_list_files(tmp_repo: Path) -> None:
    registry, _, _ = _registry(tmp_repo)
    grep = registry.execute("grep", {"pattern": "TraceMiddleware", "glob": "*.py"})
    listing = registry.execute("list_files", {"path": "app", "depth": 2})
    assert "app/middleware.py:1" in grep.content
    assert "app/main.py" in listing.content


def test_update_progress_and_note(tmp_repo: Path) -> None:
    registry, state, _ = _registry(tmp_repo)
    result = registry.execute(
        "update_progress",
        {"chapter": "1", "status": "in_progress", "note": "开始读中间件"},
    )
    assert result.ok
    assert state.current_chapter == "1"
    note = registry.execute("save_note", {"title": "Trace", "content": "contextvars"})
    assert note.ok
    assert "contextvars" in state.read_notes()
    quiz = registry.execute("record_quiz", {"score": "2/3", "summary": "补 contextvars"})
    assert quiz.ok
    assert state.progress["quiz"][-1]["score"] == "2/3"


def test_command_allowlist() -> None:
    argv, _ = validate_command("pytest -q")
    assert argv is not None and "pytest" in argv

    argv, _ = validate_command("python -m pytest tests")
    assert argv is not None and "pytest" in argv

    argv, reason = validate_command("pip install requests")
    assert argv is None and "白名单" in reason

    argv, reason = validate_command("pytest && rm -rf .")
    assert argv is None and "shell" in reason

    argv, reason = validate_command("python -c 'print(1)'")
    assert argv is None and "只允许" in reason

    argv, reason = validate_command("ruff check --fix .")
    assert argv is None and "fix" in reason


def test_run_command_requires_confirmation(tmp_repo: Path) -> None:
    registry, _, _ = _registry(tmp_repo, confirmed=False)
    result = registry.execute("run_command", {"command": "pytest --version"})
    assert not result.ok
    assert "拒绝" in result.content
