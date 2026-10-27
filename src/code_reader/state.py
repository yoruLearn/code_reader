"""Repository-local learning state.

Everything lives in ``<repo>/.code-reader``.  Nothing outside that directory is
written.  ``--no-state`` keeps the same API but only in memory.
"""

from __future__ import annotations

import contextlib
import copy
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from code_reader.curriculum import CHAPTERS, FIRST_CHAPTER_ID, chapter_label


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _empty_progress(repo_root: Path) -> dict[str, Any]:
    now = _now()
    return {
        "version": 1,
        "repo_root": str(repo_root),
        "created_at": now,
        "updated_at": now,
        "current_chapter": FIRST_CHAPTER_ID,
        "chapters": {chapter.id: "todo" for chapter in CHAPTERS},
        "skipped": [],
        "questions": [],
        "quiz": [],
    }


class StateStore:
    def __init__(self, repo_root: Path, *, enabled: bool = True) -> None:
        self.repo_root = repo_root
        self.enabled = enabled
        self.dir = repo_root / ".code-reader"
        self.progress_path = self.dir / "progress.json"
        self.briefing_path = self.dir / "briefing.md"
        self.notes_path = self.dir / "notes.md"
        self._briefing_cache: str | None = None
        self._progress = _empty_progress(repo_root)
        self.load_warning: str | None = None

        if self.enabled:
            self.dir.mkdir(parents=True, exist_ok=True)
            self._load()

    # ------------------------------------------------------------------ load/save

    def _load(self) -> None:
        if not self.progress_path.exists():
            return
        try:
            raw = json.loads(self.progress_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            backup = self.progress_path.with_suffix(".json.corrupt")
            with contextlib.suppress(OSError):
                self.progress_path.replace(backup)
            self.load_warning = f"进度文件损坏，已重置；旧文件备份在 {backup.name}"
            return
        if not isinstance(raw, dict):
            self.load_warning = "进度文件格式异常，已重置"
            return
        progress = _empty_progress(self.repo_root)
        progress.update(raw)
        progress["repo_root"] = str(self.repo_root)
        chapters = progress.get("chapters")
        if not isinstance(chapters, dict):
            chapters = {}
        for chapter in CHAPTERS:
            chapters.setdefault(chapter.id, "todo")
        progress["chapters"] = chapters
        if progress.get("current_chapter") not in {chapter.id for chapter in CHAPTERS}:
            progress["current_chapter"] = FIRST_CHAPTER_ID
        for key in ("skipped", "questions", "quiz"):
            if not isinstance(progress.get(key), list):
                progress[key] = []
        self._progress = progress

    def save(self) -> None:
        if not self.enabled:
            return
        self._progress["updated_at"] = _now()
        self.dir.mkdir(parents=True, exist_ok=True)
        tmp = self.progress_path.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(self._progress, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(tmp, self.progress_path)

    # ------------------------------------------------------------------ briefing

    def read_briefing(self) -> str | None:
        if self._briefing_cache is not None:
            return self._briefing_cache
        if not self.enabled or not self.briefing_path.exists():
            return None
        try:
            self._briefing_cache = self.briefing_path.read_text(encoding="utf-8")
        except OSError:
            return None
        return self._briefing_cache

    def write_briefing(self, text: str) -> None:
        self._briefing_cache = text
        if not self.enabled:
            return
        self.dir.mkdir(parents=True, exist_ok=True)
        self.briefing_path.write_text(text.rstrip() + "\n", encoding="utf-8")

    # ------------------------------------------------------------------ progress

    @property
    def progress(self) -> dict[str, Any]:
        return copy.deepcopy(self._progress)

    @property
    def current_chapter(self) -> str:
        return str(self._progress.get("current_chapter", FIRST_CHAPTER_ID))

    @property
    def current_title(self) -> str:
        return chapter_label(self.current_chapter)

    def set_current_chapter(self, chapter_id: str) -> None:
        if chapter_id not in {chapter.id for chapter in CHAPTERS}:
            return
        self._progress["current_chapter"] = chapter_id
        self.save()

    def mark_chapter(self, chapter_id: str, status: str, note: str = "") -> None:
        if chapter_id not in {chapter.id for chapter in CHAPTERS}:
            raise ValueError(f"未知章节: {chapter_id}")
        if status not in {"todo", "in_progress", "done", "skipped"}:
            raise ValueError(f"未知状态: {status}")
        self._progress["current_chapter"] = chapter_id
        self._progress["chapters"][chapter_id] = status
        if note:
            self.append_note(f"章节 {chapter_label(chapter_id)}", note)
        self.save()

    def record_question(self, question: str) -> None:
        question = question.strip()
        if not question:
            return
        questions = self._progress.setdefault("questions", [])
        questions.append({"q": question[:500], "at": _now()})
        if len(questions) > 50:
            del questions[:-50]
        self.save()

    def record_skipped(self, topic: str, reason: str = "") -> None:
        topic = topic.strip()
        if not topic:
            return
        skipped = self._progress.setdefault("skipped", [])
        skipped.append(
            {
                "chapter": self.current_chapter,
                "topic": topic[:300],
                "reason": reason[:500],
                "at": _now(),
            }
        )
        if len(skipped) > 50:
            del skipped[:-50]
        self.save()

    def record_quiz(self, score: str) -> None:
        quiz = self._progress.setdefault("quiz", [])
        quiz.append(
            {
                "chapter": self.current_chapter,
                "score": score[:200],
                "at": _now(),
            }
        )
        if len(quiz) > 30:
            del quiz[:-30]
        self.save()

    def snapshot_text(self) -> str:
        progress = self._progress
        current = chapter_label(self.current_chapter)
        lines = [f"当前章节: {current}", "章节状态:"]
        for chapter in CHAPTERS:
            status = progress["chapters"].get(chapter.id, "todo")
            marker = " ← 当前" if chapter.id == self.current_chapter else ""
            lines.append(f"- {chapter.id}. {chapter.title}: {status}{marker}")
        skipped = progress.get("skipped", [])
        if skipped:
            lines.append("跳过的坑:")
            for item in skipped[-5:]:
                lines.append(f"- {item.get('topic', '')}: {item.get('reason', '')}")
        questions = progress.get("questions", [])
        if questions:
            lines.append("最近的问题:")
            for item in questions[-5:]:
                lines.append(f"- {item.get('q', '')}")
        return "\n".join(lines)

    # ------------------------------------------------------------------ notes

    def append_note(self, title: str, content: str) -> None:
        if not self.enabled:
            return
        self.dir.mkdir(parents=True, exist_ok=True)
        is_new = not self.notes_path.exists()
        with self.notes_path.open("a", encoding="utf-8") as handle:
            if is_new:
                handle.write("# 学习笔记\n\n")
            handle.write(f"## {title.strip() or '未命名'}\n\n")
            handle.write(content.strip() + "\n\n")

    def read_notes(self) -> str:
        if not self.enabled or not self.notes_path.exists():
            return ""
        try:
            return self.notes_path.read_text(encoding="utf-8")
        except OSError:
            return ""

    # ------------------------------------------------------------------ hints

    def gitignore_hint(self) -> str | None:
        """Return a hint instead of modifying the target repository."""

        if not self.enabled:
            return None
        gitignore = self.repo_root / ".gitignore"
        if not gitignore.exists():
            return "建议把 `.code-reader/` 加入 .gitignore（本工具不会自动修改项目文件）。"
        try:
            text = gitignore.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            return None
        if ".code-reader" not in text:
            return "建议把 `.code-reader/` 加入 .gitignore（本工具不会自动修改项目文件）。"
        return None
