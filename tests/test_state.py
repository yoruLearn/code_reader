from __future__ import annotations

from pathlib import Path

from code_reader.state import StateStore


def test_state_roundtrip(tmp_path: Path) -> None:
    store = StateStore(tmp_path)
    store.mark_chapter("1", "in_progress", "started middleware")
    store.record_question("为什么不用 BaseHTTPMiddleware?")
    store.record_skipped("contextvars", "先跳过")
    store.append_note("Trace", "trace_id 用 contextvars 传播。")

    assert (tmp_path / ".code-reader" / "progress.json").exists()
    assert (tmp_path / ".code-reader" / "notes.md").exists()

    reloaded = StateStore(tmp_path)
    assert reloaded.current_chapter == "1"
    assert reloaded.progress["chapters"]["1"] == "in_progress"
    assert reloaded.progress["questions"][0]["q"].startswith("为什么")
    assert "contextvars" in reloaded.read_notes()


def test_state_can_be_disabled(tmp_path: Path) -> None:
    store = StateStore(tmp_path, enabled=False)
    store.mark_chapter("1", "done")
    assert store.current_chapter == "1"
    assert not (tmp_path / ".code-reader").exists()
