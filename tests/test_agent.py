from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from code_reader.agent import Agent
from code_reader.llm import LLMResponse, ToolCall
from code_reader.repo import scan_repo
from code_reader.state import StateStore
from code_reader.tools import ToolRegistry


class FakeUI:
    def __init__(self) -> None:
        self.markdowns: list[str] = []
        self.tools: list[tuple[str, dict[str, Any]]] = []
        self.errors: list[str] = []
        self.warnings: list[str] = []

    def print_markdown(self, text: str) -> None:
        self.markdowns.append(text)

    def tool_event(self, name: str, arguments: dict[str, Any]) -> None:
        self.tools.append((name, arguments))

    def error(self, message: str) -> None:
        self.errors.append(message)

    def warning(self, message: str) -> None:
        self.warnings.append(message)

    @contextmanager
    def thinking(self) -> Iterator[None]:
        yield


class FakeLLM:
    def __init__(self, responses: list[LLMResponse]) -> None:
        self.responses = responses
        self.calls: list[list[dict[str, Any]]] = []

    def complete(self, messages: list[dict[str, Any]], **_kwargs: Any) -> LLMResponse:
        self.calls.append(messages)
        return self.responses.pop(0)


def test_agent_calls_tool_then_answers(tmp_repo: Path) -> None:
    repo = scan_repo(tmp_repo)
    state = StateStore(tmp_repo)
    ui = FakeUI()
    tools = ToolRegistry(repo, state, ui, confirm=lambda _p, _r: False)
    llm = FakeLLM(
        [
            LLMResponse(
                content="",
                tool_calls=[
                    ToolCall(
                        id="call_1",
                        name="read_file",
                        arguments={"path": "app/main.py"},
                        raw_arguments='{"path":"app/main.py"}',
                    )
                ],
            ),
            LLMResponse(content="入口在 `app/main.py:4`。"),
        ]
    )
    agent = Agent(repo, state, "briefing", llm, tools, ui)  # type: ignore[arg-type]

    answer = agent.ask("入口在哪？")

    assert "app/main.py:4" in answer
    assert ui.markdowns == ["入口在 `app/main.py:4`。"]
    assert ui.tools[0][0] == "read_file"
    assert any(message.get("role") == "tool" for message in agent.messages)
    assert state.progress["questions"][0]["q"] == "入口在哪？"
    assert len(llm.calls) == 2
