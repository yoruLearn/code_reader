"""The tool-calling learning loop."""

from __future__ import annotations

import json
from typing import Any

from code_reader.curriculum import (
    CHAPTER_BY_ID,
    Chapter,
    chapter_label,
    find_chapter,
    next_chapter_id,
)
from code_reader.llm import LLMClient, ToolCall, assistant_message
from code_reader.prompts import (
    build_system_prompt,
    grade_prompt,
    lesson_prompt,
    quiz_prompt,
    run_explain_prompt,
)
from code_reader.repo import RepoInfo
from code_reader.state import StateStore
from code_reader.tools import ToolRegistry
from code_reader.ui import UI

MAX_TOOL_ROUNDS = 8
MAX_HISTORY_CHARS = 90_000


class Agent:
    def __init__(
        self,
        repo: RepoInfo,
        state: StateStore,
        briefing: str,
        llm: LLMClient,
        tools: ToolRegistry,
        ui: UI,
    ) -> None:
        self.repo = repo
        self.state = state
        self.briefing = briefing
        self.llm = llm
        self.tools = tools
        self.ui = ui
        self.messages: list[dict[str, Any]] = []
        self._quiz_pending = False
        self.last_answer = ""

    def set_briefing(self, briefing: str) -> None:
        self.briefing = briefing

    def reset_history(self) -> None:
        """Used when thinking mode changes; mixed histories can break DeepSeek."""

        self.messages.clear()
        self._quiz_pending = False
        self.last_answer = ""

    def ask(self, question: str, *, grade_quiz: bool = True) -> str:
        question = question.strip()
        if not question:
            return ""
        self.state.record_question(question)
        if self._quiz_pending and grade_quiz:
            self._quiz_pending = False
            content = f"{grade_prompt()}\n\n用户的回答：\n{question}"
        else:
            content = question
        return self._run(content)

    def teach(self, topic: str | None = None) -> str:
        self._quiz_pending = False
        chapter = self._resolve_chapter(topic)
        status = self.state.progress["chapters"].get(chapter.id, "todo")
        if status == "todo":
            self.state.mark_chapter(chapter.id, "in_progress")
        else:
            self.state.set_current_chapter(chapter.id)
        return self._run(
            lesson_prompt(chapter.id, chapter.title, chapter.goal, chapter.focus)
        )

    def quiz(self) -> str:
        chapter = CHAPTER_BY_ID.get(self.state.current_chapter, CHAPTER_BY_ID["0"])
        self._quiz_pending = True
        try:
            result = self._run(quiz_prompt(chapter.id, chapter.title))
        except Exception:
            self._quiz_pending = False
            raise
        if not result:
            self._quiz_pending = False
        return result

    def explain_run(self, command: str, output: str) -> str:
        return self._run(run_explain_prompt(command, output))

    # ------------------------------------------------------------------ internals

    def _resolve_chapter(self, topic: str | None) -> Chapter:
        if topic:
            chapter = find_chapter(topic)
            if chapter is None:
                available = "、".join(f"{item.id}.{item.title}" for item in CHAPTER_BY_ID.values())
                raise ValueError(f"没有找到章节 `{topic}`。可用章节：{available}")
            return chapter
        current = CHAPTER_BY_ID.get(self.state.current_chapter, CHAPTER_BY_ID["0"])
        if self.state.progress["chapters"].get(current.id) == "done":
            return CHAPTER_BY_ID[next_chapter_id(current.id)]
        return current

    def _run(self, user_content: str) -> str:
        self.messages.append({"role": "user", "content": user_content})
        for _ in range(MAX_TOOL_ROUNDS):
            self._trim_history()
            messages = [
                {
                    "role": "system",
                    "content": build_system_prompt(self.repo, self.briefing, self.state),
                },
                *self.messages,
            ]

            streamed = False

            def on_text(delta: str) -> None:
                nonlocal streamed
                streamed = True
                self.ui.stream_text(delta)

            stream_method = getattr(self.llm, "stream_complete", None)
            try:
                if callable(stream_method):
                    response = stream_method(
                        messages,
                        tools=self.tools.specs(),
                        on_text=on_text,
                    )
                else:
                    with self.ui.thinking():
                        response = self.llm.complete(messages, tools=self.tools.specs())
            except BaseException:
                if streamed:
                    self.ui.stream_end()
                raise
            if streamed:
                self.ui.stream_end()

            if response.tool_calls:
                self.messages.append(assistant_message(response))
                for call in response.tool_calls:
                    self._handle_tool_call(call)
                continue

            content = (response.content or "").strip()
            if content:
                self.messages.append({"role": "assistant", "content": content})
                self.last_answer = content
                if not streamed:
                    self.ui.print_markdown(content)
                return content
            self.ui.error("模型返回了空内容。可以换个问法，或 /exit 后重试。")
            return ""

        self.ui.error("工具调用轮次过多，已停止本轮。请缩小问题范围后重试。")
        return ""

    def _handle_tool_call(self, call: ToolCall) -> None:
        self.ui.tool_event(call.name, call.arguments)
        if call.parse_error:
            result_text = f"ERROR: {call.parse_error}"
            ok = False
        else:
            result = self.tools.execute(call.name, call.arguments)
            result_text = result.content
            ok = result.ok
        if not ok:
            self.ui.warning(f"  {result_text[:300]}")
        self.messages.append(
            {
                "role": "tool",
                "tool_call_id": call.id,
                "content": result_text,
            }
        )

    def _trim_history(self) -> None:
        def total_chars() -> int:
            return sum(len(str(item.get("content") or "")) for item in self.messages)

        while len(self.messages) > 6 and total_chars() > MAX_HISTORY_CHARS:
            self.messages.pop(0)
            # Never leave an orphaned tool result or tool-call block at the front.
            while self.messages and (
                self.messages[0].get("role") == "tool"
                or (
                    self.messages[0].get("role") == "assistant"
                    and self.messages[0].get("tool_calls")
                )
            ):
                self.messages.pop(0)


def describe_chapter(chapter_id: str) -> str:
    chapter = CHAPTER_BY_ID.get(chapter_id)
    return chapter_label(chapter.id) if chapter else chapter_id


def dump_history(agent: Agent) -> str:
    """Useful for debugging; not exposed as a tool."""

    return json.dumps(agent.messages, ensure_ascii=False, indent=2)
