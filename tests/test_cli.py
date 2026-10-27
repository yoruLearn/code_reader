from __future__ import annotations

from io import StringIO
from pathlib import Path

from rich.console import Console

from code_reader.cli import Session, _handle_command, main
from code_reader.ui import UI


def test_briefing_only_without_key(tmp_repo: Path, monkeypatch, capsys, tmp_path: Path) -> None:
    for name in (
        "CODE_READER_API_KEY",
        "DEEPSEEK_API_KEY",
        "OPENAI_API_KEY",
        "CODE_READER_MODEL",
        "CODE_READER_BASE_URL",
        "CODE_READER_THINKING",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(
        "code_reader.config.user_config_path",
        lambda: tmp_path / "no-config.toml",
    )
    code = main(["--repo", str(tmp_repo), "--briefing-only", "--no-state"])
    captured = capsys.readouterr()
    assert code == 0
    assert tmp_repo.name in captured.out
    assert "仓库导览" in captured.out


class _FakeClient:
    thinking_enabled = False

    def set_thinking(self, enabled: bool) -> None:
        self.thinking_enabled = enabled


class _FakeAgent:
    def __init__(self) -> None:
        self.llm = _FakeClient()
        self.reset_called = 0

    def reset_history(self) -> None:
        self.reset_called += 1


def test_think_command_toggles_and_resets_history(tmp_repo: Path) -> None:
    stream = StringIO()
    ui = UI(Console(file=stream, force_terminal=False, width=100))
    agent = _FakeAgent()
    session = Session(
        repo=None,  # type: ignore[arg-type]
        state=None,  # type: ignore[arg-type]
        tools=None,  # type: ignore[arg-type]
        agent=agent,  # type: ignore[arg-type]
        briefing="",
    )

    assert _handle_command("/think on", session, None, ui, tmp_repo)
    assert agent.llm.thinking_enabled is True
    assert agent.reset_called == 1

    assert _handle_command("/think status", session, None, ui, tmp_repo)
    assert "开启" in stream.getvalue()

    assert _handle_command("/think off", session, None, ui, tmp_repo)
    assert agent.llm.thinking_enabled is False
    assert agent.reset_called == 2
