"""The fixed learning spine.

The agent may detour, but it always knows where the main line is.  Storing the
spine as data keeps the CLI, state file, and prompt in sync.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Chapter:
    id: str
    title: str
    goal: str
    focus: tuple[str, ...]


CHAPTERS: tuple[Chapter, ...] = (
    Chapter(
        id="0",
        title="仓库导览",
        goal="建立地图：这个仓库是什么、入口在哪、先读哪三个文件。",
        focus=("README", "Spec", "ADR", "入口文件", "目录职责", "已实现 vs 规划中"),
    ),
    Chapter(
        id="1",
        title="HTTP 中间件",
        goal="讲清一个请求从进来到回去发生了什么。",
        focus=(
            "TraceMiddleware",
            "MetricsMiddleware",
            "CORSMiddleware",
            "ASGI vs BaseHTTPMiddleware",
            "add_middleware 顺序",
            "contextvars",
            "SSE",
            "X-Trace-Id",
            "RED 指标",
        ),
    ),
    Chapter(
        id="2",
        title="基础设施中间件",
        goal="讲清 MySQL / Redis / RocketMQ / Chroma 各自承担什么，以及为什么是这套组合。",
        focus=(
            "MySQL",
            "SQLAlchemy",
            "Alembic",
            "Redis",
            "缓存/会话/锁/幂等",
            "RocketMQ",
            "outbox",
            "Chroma",
            "向量检索",
            "故障模式",
        ),
    ),
    Chapter(
        id="3",
        title="链路串讲",
        goal="把中间件放回真实链路：一条问答链路、一条办事链路。",
        focus=(
            "chat/stream",
            "SSE",
            "RAG",
            "aftersales/apply",
            "状态机",
            "HITL 审批",
            "outbox",
            "幂等",
        ),
    ),
    Chapter(
        id="4",
        title="组件专题",
        goal="按需拆 RAG、工具层、状态机、审批、可观测等横切能力。",
        focus=("RAG", "工具注册表", "状态机", "审批", "评测", "可观测"),
    ),
    Chapter(
        id="5",
        title="设计复盘",
        goal="从“复述代码”走到“判断设计”：替代方案、代价、失效条件、如果重做。",
        focus=("替代方案", "权衡", "失效条件", "如果重做"),
    ),
)

CHAPTER_BY_ID = {chapter.id: chapter for chapter in CHAPTERS}
FIRST_CHAPTER_ID = CHAPTERS[0].id


def chapter_label(chapter_id: str) -> str:
    chapter = CHAPTER_BY_ID.get(chapter_id)
    if chapter is None:
        return chapter_id
    return f"{chapter.id}. {chapter.title}"


def chapter_titles() -> list[str]:
    return [chapter_label(chapter.id) for chapter in CHAPTERS]


def next_chapter_id(current_id: str) -> str:
    for index, chapter in enumerate(CHAPTERS):
        if chapter.id == current_id:
            if index + 1 < len(CHAPTERS):
                return CHAPTERS[index + 1].id
            return current_id
    return FIRST_CHAPTER_ID


def find_chapter(topic: str) -> Chapter | None:
    """Best-effort topic resolution for ``/learn <topic>``."""

    needle = topic.strip().lower()
    if not needle:
        return None
    if needle in CHAPTER_BY_ID:
        return CHAPTER_BY_ID[needle]
    number = re.search(r"\d+", needle)
    if number and number.group(0) in CHAPTER_BY_ID:
        return CHAPTER_BY_ID[number.group(0)]
    for chapter in CHAPTERS:
        haystack = f"{chapter.id} {chapter.title} {' '.join(chapter.focus)}".lower()
        if needle in haystack or chapter.title.lower() in needle:
            return chapter
    # Common aliases
    aliases = {
        "middleware": "1",
        "中间件": "1",
        "http": "1",
        "redis": "2",
        "mysql": "2",
        "mq": "2",
        "rocketmq": "2",
        "chroma": "2",
        "rag": "4",
        "链路": "3",
        "审批": "4",
        "状态机": "4",
    }
    for alias, chapter_id in aliases.items():
        if alias in needle:
            return CHAPTER_BY_ID.get(chapter_id)
    return None
