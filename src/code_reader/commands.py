"""Single source of truth for slash commands.

``ui.show_help`` and the prompt_toolkit completion menu both read this list, so
adding a command cannot silently drift between the menu and the help table.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CommandSpec:
    command: str
    syntax: str
    description: str
    example: str
    insert: str
    immediate: bool = False


COMMANDS: tuple[CommandSpec, ...] = (
    CommandSpec(
        command="/help",
        syntax="/help",
        description="查看全部命令、作用和示例",
        example="/help",
        insert="/help",
        immediate=True,
    ),
    CommandSpec(
        command="/roadmap",
        syntax="/roadmap",
        description="查看 0–5 章学习路线、目标和下一步",
        example="/roadmap",
        insert="/roadmap",
        immediate=True,
    ),
    CommandSpec(
        command="/learn",
        syntax="/learn [章节]",
        description="按路线学习；不带参数继续当前章",
        example="/learn 1",
        insert="/learn ",
    ),
    CommandSpec(
        command="/ask",
        syntax="/ask <问题>",
        description="自由提问；也可直接输入自然语言",
        example="/ask 为什么 TraceMiddleware 最外层？",
        insert="/ask ",
    ),
    CommandSpec(
        command="/quiz",
        syntax="/quiz",
        description="针对当前章节出 3 道检查题",
        example="/quiz",
        insert="/quiz",
        immediate=True,
    ),
    CommandSpec(
        command="/run",
        syntax="/run <命令>",
        description="执行允许的测试/静态检查，需确认",
        example="/run pytest tests/unit -q",
        insert="/run ",
    ),
    CommandSpec(
        command="/think",
        syntax="/think on|off|status",
        description="切换当前会话思考模式；切换会清空对话",
        example="/think on",
        insert="/think ",
    ),
    CommandSpec(
        command="/progress",
        syntax="/progress",
        description="查看章节状态、跳过的坑和最近测验",
        example="/progress",
        insert="/progress",
        immediate=True,
    ),
    CommandSpec(
        command="/map",
        syntax="/map",
        description="重新打印仓库导览和阅读顺序",
        example="/map",
        insert="/map",
        immediate=True,
    ),
    CommandSpec(
        command="/refresh",
        syntax="/refresh",
        description="重新扫描仓库并生成导览",
        example="/refresh",
        insert="/refresh",
        immediate=True,
    ),
    CommandSpec(
        command="/notes",
        syntax="/notes",
        description="查看 .code-reader/notes.md 学习笔记",
        example="/notes",
        insert="/notes",
        immediate=True,
    ),
    CommandSpec(
        command="/done",
        syntax="/done [备注]",
        description="标记当前章节完成，可附一句备注",
        example="/done 本章重点：ASGI + contextvars",
        insert="/done ",
    ),
    CommandSpec(
        command="/skip",
        syntax="/skip <主题>",
        description="记录一个暂时跳过的主题",
        example="/skip contextvars",
        insert="/skip ",
    ),
    CommandSpec(
        command="/clear",
        syntax="/clear",
        description="清屏",
        example="/clear",
        insert="/clear",
        immediate=True,
    ),
    CommandSpec(
        command="/exit",
        syntax="/exit",
        description="退出并保存学习进度",
        example="/exit",
        insert="/exit",
        immediate=True,
    ),
)

COMMAND_BY_NAME = {spec.command: spec for spec in COMMANDS}
