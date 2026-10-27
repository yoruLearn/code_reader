from __future__ import annotations

from pathlib import Path

from code_reader.briefing import build_briefing, fallback_briefing
from code_reader.repo import scan_repo


def test_fallback_briefing_has_sections(tmp_repo: Path) -> None:
    repo = scan_repo(tmp_repo)
    text = fallback_briefing(repo)
    for heading in (
        "## 一句话",
        "## 技术栈",
        "## 入口与运行",
        "## 目录地图",
        "## 建议阅读顺序",
        "## 已实现 vs 规划中",
        "## 设计看点",
    ):
        assert heading in text
    assert "app/main.py" in text
    assert "do-not-read" not in text


def test_build_briefing_without_llm(tmp_repo: Path) -> None:
    repo = scan_repo(tmp_repo)
    text = build_briefing(repo, None)
    assert text.startswith("# ")
    assert "离线" in text
