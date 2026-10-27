from __future__ import annotations

from io import StringIO

from rich.console import Console

from code_reader.ui import UI


def test_roadmap_shows_current_chapter_and_next_step() -> None:
    stream = StringIO()
    ui = UI(Console(file=stream, force_terminal=False, width=120))
    ui.show_roadmap(
        {
            "current_chapter": "1",
            "chapters": {"0": "done", "1": "in_progress"},
        }
    )
    text = stream.getvalue()
    assert "学习路线" in text
    assert "HTTP 中间件" in text
    assert "当前" in text
    assert "/learn 1" in text
