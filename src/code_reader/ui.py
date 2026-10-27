"""Rich terminal UI.  Kept deliberately thin so tests can inject a fake."""

from __future__ import annotations

import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from prompt_toolkit import PromptSession
from prompt_toolkit.completion import CompleteEvent, Completer, Completion
from prompt_toolkit.document import Document
from prompt_toolkit.filters import has_completions
from prompt_toolkit.formatted_text import FormattedText
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.shortcuts import CompleteStyle
from rich.console import Console
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.prompt import Confirm
from rich.table import Table
from rich.text import Text

from code_reader.commands import COMMANDS
from code_reader.curriculum import CHAPTERS, chapter_label


class SlashCommandCompleter(Completer):
    """Show the command palette as soon as the user types ``/``."""

    def get_completions(
        self, document: Document, _complete_event: CompleteEvent
    ) -> Iterator[Completion]:
        text = document.text_before_cursor
        if not text.startswith("/"):
            return

        if text.startswith("/think "):
            part = text[len("/think ") :]
            for value, description in (
                ("on", "开启思考模式（更慢、更贵）"),
                ("off", "关闭思考模式（默认）"),
                ("status", "查看当前会话的思考模式"),
            ):
                if value.startswith(part):
                    yield Completion(
                        value,
                        start_position=-len(part),
                        display=f"/think {value}",
                        display_meta=description,
                    )
            return

        if " " in text:
            return

        for spec in COMMANDS:
            if spec.command.startswith(text):
                yield Completion(
                    spec.insert,
                    start_position=-len(text),
                    display=spec.syntax,
                    display_meta=f"{spec.description}；例：{spec.example}",
                )


def _slash_bindings() -> KeyBindings:
    """Enter accepts the highlighted item instead of submitting immediately."""

    bindings = KeyBindings()
    immediate = {spec.insert for spec in COMMANDS if spec.immediate}

    @bindings.add("/")
    def _open_menu(event: Any) -> None:
        buffer = event.current_buffer
        if buffer.text:
            # A slash inside an argument (e.g. /run pytest tests/unit) should
            # be a normal character, not a command-menu trigger.
            buffer.insert_text("/")
            return
        buffer.insert_text("/")
        buffer.start_completion(select_first=True)

    @bindings.add("enter", filter=has_completions)
    def _accept(event: Any) -> None:
        buffer = event.current_buffer
        state = buffer.complete_state
        completion = state.current_completion if state is not None else None
        if completion is None and state is not None and state.completions:
            # complete_while_typing can open the menu without selecting a row;
            # Enter should pick the first match instead of submitting raw "/".
            completion = state.completions[0]
        if completion is None:
            buffer.validate_and_handle()
            return
        # No-argument commands execute in one Enter.  Commands with arguments
        # are inserted so the user can type the rest; /think subcommands are
        # complete once selected.
        if completion.text in immediate or (
            buffer.text.startswith("/think ") and completion.text in {"on", "off", "status"}
        ):
            buffer.apply_completion(completion)
            buffer.validate_and_handle()
        else:
            buffer.apply_completion(completion)

    return bindings


class UI:
    def __init__(self, console: Console | None = None, *, interactive: bool = True) -> None:
        self.console = console or Console()
        self.interactive = interactive
        self._prompt_session: Any = None
        self._streaming = False
        self._stream_buffer = ""
        self._stream_live: Live | None = None
        self._last_stream_update = 0.0

    # ------------------------------------------------------------------ primitives

    def print(self, *args: Any, **kwargs: Any) -> None:
        self.console.print(*args, **kwargs)

    def info(self, message: str) -> None:
        self.console.print(f"[cyan]{message}[/cyan]")

    def success(self, message: str) -> None:
        self.console.print(f"[green]{message}[/green]")

    def warning(self, message: str) -> None:
        self.console.print(f"[yellow]{message}[/yellow]")

    def error(self, message: str) -> None:
        self.console.print(f"[bold red]{message}[/bold red]")

    def rule(self, title: str = "") -> None:
        self.console.rule(f"[dim]{title}[/dim]" if title else "")

    def print_markdown(self, text: str) -> None:
        self.stream_end()
        self.console.print(Markdown(text or "（模型没有返回内容）"))

    def stream_text(self, text: str) -> None:
        """Stream a model delta.

        In a real terminal the accumulated Markdown is re-rendered in-place, so
        bold/code blocks remain readable while text appears progressively.  When
        output is redirected (tests/logs), fall back to plain deltas.
        """

        if not text:
            return
        self._streaming = True
        self._stream_buffer += text
        if self.console.is_terminal and self.interactive:
            if self._stream_live is None:
                self._stream_live = Live(
                    Markdown(self._stream_buffer),
                    console=self.console,
                    refresh_per_second=8,
                )
                self._stream_live.start()
                self._last_stream_update = time.monotonic()
            elif time.monotonic() - self._last_stream_update >= 0.08:
                self._stream_live.update(Markdown(self._stream_buffer))
                self._last_stream_update = time.monotonic()
            return
        self.console.print(
            text,
            end="",
            markup=False,
            highlight=False,
            soft_wrap=True,
        )

    def stream_end(self) -> None:
        if self._stream_live is not None:
            self._stream_live.update(Markdown(self._stream_buffer))
            self._stream_live.stop()
            self._stream_live = None
            self._stream_buffer = ""
            self._streaming = False
            return
        if self._streaming:
            self.console.print()
            self._stream_buffer = ""
            self._streaming = False

    def status(self, message: str) -> Any:
        return self.console.status(f"[cyan]{message}[/cyan]", spinner="dots")

    # ------------------------------------------------------------------ input

    def prompt(self, label: str = "你") -> str:
        if not self.interactive or not (sys.stdin.isatty() and sys.stdout.isatty()):
            return self.console.input(f"[bold cyan]{label} › [/]").strip()
        try:
            if self._prompt_session is None:
                self._prompt_session = PromptSession(
                    completer=SlashCommandCompleter(),
                    complete_while_typing=True,
                    complete_style=CompleteStyle.COLUMN,
                    reserve_space_for_menu=18,
                    key_bindings=_slash_bindings(),
                    enable_history_search=False,
                )
            return str(
                self._prompt_session.prompt(
                    FormattedText([("bold ansicyan", f"{label} › ")])
                )
            ).strip()
        except (EOFError, KeyboardInterrupt):
            raise
        except Exception:
            # A redirected/odd terminal should still be usable; fall back to
            # the plain Rich prompt instead of crashing the REPL.
            self._prompt_session = None
            return self.console.input(f"[bold cyan]{label} › [/]").strip()

    def confirm(self, prompt: str, *, default: bool = False) -> bool:
        if not self.interactive:
            return False
        try:
            return Confirm.ask(prompt, default=default, console=self.console)
        except (EOFError, KeyboardInterrupt):
            return False

    # ------------------------------------------------------------------ screens

    def banner(
        self,
        *,
        repo_name: str,
        repo_root: str,
        model: str,
        has_key: bool,
        state_enabled: bool,
        is_git: bool,
    ) -> None:
        key_state = "已配置 Key" if has_key else "未配置 Key，离线导览"
        lines = [
            f"[bold]仓库[/bold]  {repo_name}  [dim]{repo_root}[/dim]",
            f"[bold]模型[/bold]  {model}  [dim]({key_state})[/dim]",
            f"[bold]状态[/bold]  {'.code-reader/' if state_enabled else '不落盘 (--no-state)'}"
            f"  {'git' if is_git else 'plain dir'}",
        ]
        self.console.print(
            Panel(
                "\n".join(lines),
                title="[bold cyan]code-reader[/bold cyan]",
                subtitle="[dim]/help 查看命令 · 直接输入问题即可[/dim]",
                border_style="cyan",
            )
        )

    def show_briefing(self, text: str) -> None:
        self.print_markdown(text)

    def show_help(self) -> None:
        table = Table(
            title="命令",
            show_header=True,
            header_style="bold cyan",
            expand=True,
        )
        table.add_column("命令", style="cyan", no_wrap=True)
        table.add_column("作用", overflow="fold")
        table.add_column("示例", overflow="fold")
        for spec in COMMANDS:
            table.add_row(spec.syntax, spec.description, spec.example)
        self.console.print(table)
        self.console.print("[dim]提示：输入 / 会弹出命令菜单，↑/↓ 选择，Enter 确认。[/dim]")

    def show_roadmap(self, progress: dict[str, Any]) -> None:
        current = str(progress.get("current_chapter", "0"))
        chapters = progress.get("chapters", {})
        status_label = {
            "todo": "[dim]未开始[/dim]",
            "in_progress": "[yellow]进行中[/yellow]",
            "done": "[green]已完成[/green]",
            "skipped": "[magenta]已跳过[/magenta]",
        }
        table = Table(
            title="学习路线 · 0–5 章",
            show_header=True,
            header_style="bold cyan",
            expand=True,
        )
        table.add_column("章节", no_wrap=True, style="cyan")
        table.add_column("本章目标", overflow="fold")
        table.add_column("状态", no_wrap=True)
        for chapter in CHAPTERS:
            status = str(chapters.get(chapter.id, "todo"))
            marker = "\n[dim]← 当前[/dim]" if chapter.id == current else ""
            table.add_row(
                f"{chapter.id}. {chapter.title}",
                chapter.goal,
                status_label.get(status, status) + marker,
            )
        self.console.print(table)

        next_chapter = CHAPTERS[0]
        for chapter in CHAPTERS:
            if chapter.id == current:
                next_chapter = chapter
                break
        if chapters.get(next_chapter.id) == "done":
            following = next(
                (item for item in CHAPTERS if item.id > next_chapter.id),
                next_chapter,
            )
            next_chapter = following
        self.console.print(
            f"[dim]下一步:[/dim] [bold cyan]/learn {next_chapter.id}[/bold cyan]"
            f"  [dim]（{next_chapter.title}）[/dim]"
        )
        self.console.print(
            "[dim]提示: /map 看仓库阅读顺序，/progress 看完成情况，/learn 继续当前章。[/dim]"
        )

    def show_progress(self, progress: dict[str, Any]) -> None:
        current = str(progress.get("current_chapter", "0"))
        table = Table(title="学习进度", show_header=True, header_style="bold cyan")
        table.add_column("章节", no_wrap=True)
        table.add_column("状态")
        table.add_column("", no_wrap=True)
        status_label = {
            "todo": "[dim]未开始[/dim]",
            "in_progress": "[yellow]进行中[/yellow]",
            "done": "[green]已完成[/green]",
            "skipped": "[magenta]已跳过[/magenta]",
        }
        chapters = progress.get("chapters", {})
        for chapter in CHAPTERS:
            status = str(chapters.get(chapter.id, "todo"))
            marker = "← 当前" if chapter.id == current else ""
            table.add_row(chapter_label(chapter.id), status_label.get(status, status), marker)
        self.console.print(table)
        skipped = progress.get("skipped", [])
        if skipped:
            self.console.print("[dim]跳过的坑：[/dim]")
            for item in skipped[-5:]:
                self.console.print(
                    f"  [magenta]·[/magenta] {item.get('topic', '')} "
                    f"[dim]{item.get('reason', '')}[/dim]"
                )
        quiz = progress.get("quiz", [])
        if quiz:
            last = quiz[-1]
            self.console.print(f"[dim]最近测验：{last.get('score', '')}[/dim]")

    def show_notes(self, notes: str) -> None:
        if not notes.strip():
            self.warning("还没有笔记。学完一章后可以用 /done 或让 agent 记录。")
            return
        self.print_markdown(notes)

    def tool_event(self, name: str, arguments: dict[str, Any]) -> None:
        detail = ""
        if name in {"read_file", "git_blame"}:
            detail = str(arguments.get("path", ""))
        elif name == "grep":
            detail = str(arguments.get("pattern", ""))
        elif name == "list_files":
            detail = str(arguments.get("path", "."))
        elif name == "run_command":
            detail = str(arguments.get("command", ""))
        elif name in {"update_progress", "save_note"}:
            detail = str(arguments.get("chapter") or arguments.get("title") or "")
        self.console.print(f"[dim]  ↳ {name} {detail}[/dim]")

    def command_echo(self, command: str) -> None:
        self.console.print(f"[bold]›[/bold] {command}")

    def clear(self) -> None:
        self.console.clear()

    @contextmanager
    def thinking(self, message: str = "思考中…") -> Iterator[None]:
        with self.console.status(f"[cyan]{message}[/cyan]", spinner="dots"):
            yield

    def print_text(self, text: str) -> None:
        self.console.print(Text(text))
