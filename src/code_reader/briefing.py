"""Repo briefing generation with a deterministic offline fallback."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path

from code_reader.llm import LLMClient, LLMError
from code_reader.prompts import BRIEFING_SYSTEM, BRIEFING_USER
from code_reader.repo import RepoInfo


def _first_paragraph(text: str) -> str:
    lines = [line.strip() for line in text.replace("\r\n", "\n").splitlines()]
    paragraph: list[str] = []
    in_fence = False
    for line in lines:
        if line.startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        if not line:
            if paragraph:
                break
            continue
        if line.startswith(("#", ">", "|", "![")):
            if paragraph:
                break
            continue
        if "badge.svg" in line or "actions/workflows" in line:
            continue
        # Drop image-only lines and naked URLs; a learning repo often has badges
        # before the first real paragraph.
        cleaned = re.sub(r"!\[[^\]]*\]\([^)]+\)", "", line)
        cleaned = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", cleaned)
        cleaned = re.sub(r"<https?://[^>]+>", "", cleaned)
        cleaned = re.sub(r"https?://\S+", "", cleaned)
        cleaned = re.sub(r"[*_`]", "", cleaned).strip()
        if not cleaned or not re.search(r"[A-Za-z\u4e00-\u9fff]", cleaned):
            continue
        if len(cleaned) < 8:
            continue
        paragraph.append(cleaned)
    joined = " ".join(paragraph)
    return joined[:300]


def _readme_excerpt(repo: RepoInfo) -> str:
    for path, text in repo.key_excerpts.items():
        if Path(path).name.lower().startswith("readme"):
            return text
    return ""


def _language_line(repo: RepoInfo) -> str:
    if not repo.languages:
        return "未识别"
    return "、".join(f"{name}（{count} 个文件）" for name, count in repo.languages.items())


def _stack_line(repo: RepoInfo) -> str:
    parts = []
    if repo.framework_hints:
        parts.append("、".join(repo.framework_hints))
    parts.append(_language_line(repo))
    return "；".join(part for part in parts if part)


def _must_reads(repo: RepoInfo) -> list[tuple[str, str]]:
    picks: list[tuple[str, str]] = []
    root_readme = next(
        (path for path in repo.docs if Path(path).name.lower().startswith("readme")),
        None,
    )
    if root_readme:
        picks.append((root_readme, "先建立“这个仓库是什么”的整体印象"))
    if repo.entry_candidates:
        picks.append((repo.entry_candidates[0], "从真实入口看请求/程序如何启动"))

    # Prefer the actual design/verification docs over a second README buried in
    # a subdirectory (e.g. loadtest/README.md).
    preferred = ("requirements", "verification", "spec", "adr", "architecture")
    doc_candidates = [
        path
        for path in repo.docs
        if path not in {item[0] for item in picks}
        and not Path(path).name.lower().startswith("readme")
    ]
    doc_candidates.sort(
        key=lambda path: (
            0 if any(word in path.lower() for word in preferred) else 1,
            path.count("/"),
            path,
        )
    )
    if doc_candidates:
        picks.append((doc_candidates[0], "补充设计背景、约束或验收标准"))

    if len(picks) < 3:
        for manifest in repo.manifests:
            if manifest not in {item[0] for item in picks}:
                picks.append((manifest, "确认依赖、脚本和运行方式"))
                break
    return picks[:3]


def _implemented_section(repo: RepoInfo) -> str:
    blob = "\n".join(repo.key_excerpts.values()).lower()
    markers = []
    if "m0" in blob or "m1" in blob or "里程碑" in blob:
        markers.append("文档出现里程碑/M0 等字样")
    if "骨架" in blob or "scaffold" in blob:
        markers.append("文档出现“骨架/scaffold”")
    if "未实现" in blob or "not implemented" in blob or "501" in blob:
        markers.append("文档提到未实现/501")
    if "spec" in blob:
        markers.append("存在 Spec 类文档")
    if markers:
        evidence = "；".join(markers)
        return (
            f"扫描判断：**这个仓库很可能处于早期里程碑或骨架阶段**（{evidence}）。\n\n"
            "因此不能把 Spec 里的目标架构当成已落地实现。读代码时请逐项核对：\n"
            "- 端点是否返回占位错误或 501；\n"
            "- 服务方法是否只有契约、没有真实调用；\n"
            "- 配置/部署是否已经接线；\n"
            "- `docs/**/verification` 或同类验证记录是否标明未覆盖项。\n\n"
            "本导览只能做静态扫描，具体“哪一项已实现”需要在问答中逐文件确认。"
        )
    return (
        "扫描无法可靠判定“已实现 vs 规划中”。请以代码入口、测试和验证记录为准；"
        "如果文档中出现目标架构，先确认对应代码是否存在，再把它标成 `[Spec 规划]`。"
    )


def _design_points(repo: RepoInfo) -> list[str]:
    hints = set(repo.framework_hints)
    points: list[str] = []
    if {"FastAPI", "Starlette/ASGI", "Uvicorn"} & hints:
        points.append("请求链路：中间件顺序、trace 传播、SSE 长连接如何共存。")
    if "LangGraph" in hints:
        points.append("Agent 编排：状态图节点如何划分，checkpointer 持久化边界在哪。")
    if {"SQLAlchemy", "Alembic"} & hints:
        points.append("数据层：事务边界、迁移与 outbox 如何保证最终一致。")
    if "Redis" in hints:
        points.append("Redis：缓存、会话、分布式锁、幂等键是否共用一套 key 规范。")
    if {"RocketMQ", "Kafka"} & hints:
        points.append("消息队列：投递语义、消费幂等、失败重试与死信。")
    if {"Chroma", "FAISS", "pgvector"} & hints:
        points.append("RAG：分块、混合召回、rerank、低置信兜底如何串起来。")
    if not points:
        points.append("从入口和目录分层入手：哪些是横切关注点，哪些是业务用例。")
    points.append("找出一个“看起来可以更简单”的地方，追问作者为什么没有那样做。")
    return points[:5]


def fallback_briefing(repo: RepoInfo) -> str:
    readme = _readme_excerpt(repo)
    one_liner = _first_paragraph(readme) or f"{repo.name}，一个以 {_language_line(repo)} 为主的项目。"
    docs = "\n".join(f"- `{path}`" for path in repo.docs[:10]) or "- 未发现文档"
    entries = "\n".join(f"- `{path}`" for path in repo.entry_candidates) or "- 未识别到明确入口"
    commands = "\n".join(f"- `{command}`" for command in repo.run_commands) or "- 未从文档中提取到运行命令"
    must_reads = _must_reads(repo)
    must_read_text = "\n".join(
        f"{index}. `{path}` —— {reason}" for index, (path, reason) in enumerate(must_reads, 1)
    )
    design_points = "\n".join(f"- {point}" for point in _design_points(repo))
    warnings = "\n".join(f"- {warning}" for warning in repo.warnings) or "- 扫描未发现明显盲区"
    git_line = "git 仓库" if repo.is_git else "非 git 目录"
    if repo.git_branch:
        git_line += f"，分支 `{repo.git_branch}`"
    if repo.git_head:
        git_line += f"，HEAD `{repo.git_head}`"

    return f"""# {repo.name} · 仓库导览

> 离线导览：未调用模型，以下内容来自确定性扫描。配置模型后可 `/refresh` 生成更深入的版本。
> 生成时间：{datetime.now(UTC).isoformat(timespec="seconds")}

## 一句话

{one_liner}

## 技术栈

{_stack_line(repo)}

## 入口与运行

{entries}

运行/测试命令（从 README、Makefile、package.json 等提取）：

{commands}

## 目录地图

```text
{repo.tree}
```

## 关键文档

{docs}

## 建议阅读顺序

1. 先读 README，确认“这个仓库解决什么问题、当前处于哪个阶段”。
2. 再读依赖清单（`pyproject.toml` / `package.json` 等），确认技术栈与运行方式。
3. 从入口文件往下读，画出“请求/程序启动”的第一条链路。
4. 读测试，把测试当成可执行规格，反推设计意图。
5. 最后读 Spec/ADR/验证记录，对照“目标设计”和“已落地实现”。

## 3 个必读文件

{must_read_text or "- 未识别到足够的候选文件"}

## 已实现 vs 规划中

{_implemented_section(repo)}

## 设计看点

{design_points}

## 风险与限制

{warnings}
- 本导览是静态扫描，不包含运行时行为。
- 文件路径和行号会在代码变化后失效；回答具体问题时应重新读取。
- 仓库状态：{git_line}。
"""


def build_briefing(
    repo: RepoInfo,
    llm: LLMClient | None = None,
    *,
    errors: list[str] | None = None,
) -> str:
    if llm is not None:
        try:
            response = llm.complete(
                [
                    {"role": "system", "content": BRIEFING_SYSTEM},
                    {
                        "role": "user",
                        "content": BRIEFING_USER.format(repo_context=repo.context(max_chars=34_000)),
                    },
                ],
                tools=None,
                max_tokens=8192,
                temperature=0.2,
            )
        except LLMError as exc:
            if errors is not None:
                errors.append(str(exc))
            response = None
        if response is not None and response.content:
            content = response.content.strip()
            if len(content) >= 500 and "##" in content:
                if not content.startswith("#"):
                    content = f"# {repo.name} · 仓库导览\n\n{content}"
                if response.finish_reason == "length":
                    content += (
                        "\n\n> 注意：本次导览达到输出上限，末尾可能被截断；"
                        "可调大 `max_tokens` 后 `/refresh`。\n"
                    )
                return content + "\n\n---\n\n> 本导览由 code-reader 生成；结论请以代码与文档为准，`/refresh` 可重新生成。\n"

    text = fallback_briefing(repo)
    return text + "\n\n---\n\n> 本导览由 code-reader 离线生成；配置模型后 `/refresh` 可获得更深入的版本。\n"
