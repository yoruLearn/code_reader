from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, ClassVar

from code_reader.agent import Agent
from code_reader.cli import main
from code_reader.config import Settings
from code_reader.llm import LLMClient
from code_reader.repo import scan_repo
from code_reader.state import StateStore
from code_reader.tools import ToolRegistry


class _Handler(BaseHTTPRequestHandler):
    calls: ClassVar[int] = 0
    requests: ClassVar[list[dict[str, Any]]] = []

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(length).decode("utf-8"))
        type(self).requests.append(payload)
        type(self).calls += 1
        streaming = bool(payload.get("stream"))

        def chunk(delta: dict[str, Any], finish_reason: str | None) -> dict[str, Any]:
            return {
                "id": "chatcmpl-stream",
                "object": "chat.completion.chunk",
                "created": 0,
                "model": "mock",
                "choices": [
                    {"index": 0, "delta": delta, "finish_reason": finish_reason}
                ],
            }

        if type(self).calls == 1:
            tool_call = {
                "id": "call_1",
                "type": "function",
                "function": {
                    "name": "read_file",
                    "arguments": '{"path":"app/main.py"}',
                },
            }
            if streaming:
                events = [
                    chunk(
                        {
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [{"index": 0, **tool_call}],
                        },
                        None,
                    ),
                    chunk({}, "tool_calls"),
                ]
                body = None
            else:
                body = {
                    "id": "chatcmpl-1",
                    "object": "chat.completion",
                    "created": 0,
                    "model": "mock",
                    "choices": [
                        {
                            "index": 0,
                            "message": {
                                "role": "assistant",
                                "content": None,
                                "tool_calls": [tool_call],
                            },
                            "finish_reason": "tool_calls",
                        }
                    ],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
                }
        else:
            if streaming:
                events = [
                    chunk({"role": "assistant", "content": "入口在 "}, None),
                    chunk({"content": "`app/main.py:4`。"}, None),
                    chunk({}, "stop"),
                ]
                body = None
            else:
                body = {
                    "id": "chatcmpl-2",
                    "object": "chat.completion",
                    "created": 0,
                    "model": "mock",
                    "choices": [
                        {
                            "index": 0,
                            "message": {
                                "role": "assistant",
                                "content": "入口在 `app/main.py:4`。",
                            },
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
                }

        if streaming:
            raw = (
                "".join(f"data: {json.dumps(event, ensure_ascii=False)}\n\n" for event in events)
                + "data: [DONE]\n\n"
            ).encode("utf-8")
            content_type = "text/event-stream"
        else:
            raw = json.dumps(body, ensure_ascii=False).encode("utf-8")
            content_type = "application/json"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *_args: Any) -> None:
        return


@contextmanager
def _mock_server() -> Iterator[str]:
    _Handler.calls = 0
    _Handler.requests = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


class _UI:
    def __init__(self) -> None:
        self.markdowns: list[str] = []
        self.streamed: list[str] = []
        self.tools: list[tuple[str, dict[str, Any]]] = []

    def print_markdown(self, text: str) -> None:
        self.markdowns.append(text)

    def stream_text(self, text: str) -> None:
        self.streamed.append(text)

    def stream_end(self) -> None:
        return

    def tool_event(self, name: str, arguments: dict[str, Any]) -> None:
        self.tools.append((name, arguments))

    def error(self, message: str) -> None:
        raise AssertionError(message)

    def warning(self, message: str) -> None:
        return

    @contextmanager
    def thinking(self) -> Iterator[None]:
        yield


def test_openai_client_and_agent_loop(tmp_repo: Path) -> None:
    with _mock_server() as base_url:
        settings = Settings(
            api_key="test-key",
            base_url=base_url,
            model="deepseek-flash",
            timeout=20,
        )
        client = LLMClient(settings)
        repo = scan_repo(tmp_repo)
        state = StateStore(tmp_repo)
        ui = _UI()
        tools = ToolRegistry(repo, state, ui, confirm=lambda _p, _r: False)  # type: ignore[arg-type]
        agent = Agent(repo, state, "briefing", client, tools, ui)  # type: ignore[arg-type]

        answer = agent.ask("入口在哪？")

    assert answer == "入口在 `app/main.py:4`。"
    assert "".join(ui.streamed) == answer
    assert ui.markdowns == []
    assert ui.tools == [("read_file", {"path": "app/main.py"})]
    assert _Handler.calls == 2
    assert _Handler.requests[0]["tools"]
    assert _Handler.requests[0]["stream"] is True
    assert _Handler.requests[0]["thinking"] == {"type": "disabled"}
    tool_messages = [
        message for message in _Handler.requests[1]["messages"] if message.get("role") == "tool"
    ]
    assert tool_messages and "def main" in tool_messages[0]["content"]


def test_thinking_toggle_changes_extra_body(tmp_repo: Path) -> None:
    with _mock_server() as base_url:
        settings = Settings(
            api_key="test-key",
            base_url=base_url,
            model="deepseek-flash",
            thinking="enabled",
            timeout=20,
        )
        client = LLMClient(settings)
        assert client.thinking_enabled
        client.complete([{"role": "user", "content": "first"}])
        client.set_thinking(False)
        assert not client.thinking_enabled
        client.complete([{"role": "user", "content": "second"}])

    assert _Handler.requests[0]["thinking"] == {"type": "enabled"}
    assert _Handler.requests[1]["thinking"] == {"type": "disabled"}


def test_cli_ask_with_existing_briefing(
    tmp_repo: Path,
    monkeypatch: Any,
    capsys: Any,
) -> None:
    state_dir = tmp_repo / ".code-reader"
    state_dir.mkdir()
    (state_dir / "briefing.md").write_text("# Demo\n\n## 一句话\n\nA demo.\n", encoding="utf-8")

    with _mock_server() as base_url:
        monkeypatch.setenv("CODE_READER_API_KEY", "test-key")
        monkeypatch.setenv("CODE_READER_BASE_URL", base_url)
        monkeypatch.setenv("CODE_READER_MODEL", "mock")
        code = main(["--repo", str(tmp_repo), "--ask", "入口在哪？"])

    captured = capsys.readouterr()
    assert code == 0
    assert "入口在" in captured.out
    assert "app/main.py:4" in captured.out
    assert _Handler.calls == 2
