"""Command line entry point and REPL."""

from __future__ import annotations

import argparse
import contextlib
import sys
from dataclasses import dataclass
from pathlib import Path

from code_reader import __version__
from code_reader.agent import Agent
from code_reader.briefing import build_briefing, fallback_briefing
from code_reader.config import load_settings, setup_hint
from code_reader.llm import LLMClient, LLMError
from code_reader.repo import RepoInfo, find_repo_root, scan_repo
from code_reader.state import StateStore
from code_reader.tools import ToolRegistry
from code_reader.ui import UI


@dataclass
class Session:
    repo: RepoInfo
    state: StateStore
    tools: ToolRegistry
    agent: Agent | None
    briefing: str


def _make_tools(repo: RepoInfo, state: StateStore, ui: UI) -> ToolRegistry:
    return ToolRegistry(
        repo,
        state,
        ui,
        confirm=lambda prompt, _reason: ui.confirm(prompt, default=False),
    )


def _make_agent(
    repo: RepoInfo,
    state: StateStore,
    briefing: str,
    llm: LLMClient | None,
    tools: ToolRegistry,
    ui: UI,
) -> Agent | None:
    if llm is None:
        return None
    return Agent(repo, state, briefing, llm, tools, ui)


def _configure_stdio() -> None:
    """Keep Chinese output stable on Windows terminals with legacy code pages."""

    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        with contextlib.suppress(OSError, ValueError):
            reconfigure(encoding="utf-8", errors="replace")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="code-reader",
        description="Console-first code reading tutor: repo briefing, evidence-based Q&A, middleware deep dives.",
    )
    parser.add_argument(
        "--repo",
        type=Path,
        default=None,
        help="目标仓库路径（默认当前目录；会自动向上寻找 git root）",
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="重新扫描并生成仓库导览",
    )
    parser.add_argument(
        "--no-state",
        action="store_true",
        help="不写入 .code-reader/（临时探索）",
    )
    parser.add_argument(
        "--briefing-only",
        action="store_true",
        help="只生成/打印导览，然后退出",
    )
    parser.add_argument(
        "--ask",
        metavar="QUESTION",
        default=None,
        help="一次性提问，不进入 REPL",
    )
    parser.add_argument("--model", default=None, help="覆盖模型名")
    parser.add_argument("--base-url", default=None, help="覆盖 OpenAI-compatible base URL")
    parser.add_argument("--debug", action="store_true", help="显示完整异常栈")
    parser.add_argument("--version", action="version", version=f"code-reader {__version__}")
    return parser


def main(argv: list[str] | None = None) -> int:
    _configure_stdio()
    args = _build_parser().parse_args(argv)
    try:
        root, _is_git = find_repo_root(args.repo or Path.cwd())
    except FileNotFoundError as exc:
        print(f"code-reader: {exc}", file=sys.stderr)
        return 2

    settings = load_settings(model=args.model, base_url=args.base_url)
    ui = UI()
    state = StateStore(root, enabled=not args.no_state)
    if state.load_warning:
        ui.warning(state.load_warning)

    had_briefing = state.read_briefing() is not None
    try:
        with ui.status("扫描仓库…"):
            repo = scan_repo(root)
    except Exception as exc:
        ui.error(f"仓库扫描失败：{type(exc).__name__}: {exc}")
        if args.debug:
            ui.console.print_exception()
        return 2

    try:
        llm = LLMClient(settings) if settings.has_key else None
    except Exception as exc:
        ui.error(f"模型客户端初始化失败：{type(exc).__name__}: {exc}")
        if args.debug:
            ui.console.print_exception()
        return 2
    briefing = state.read_briefing()

    if args.refresh or not briefing:
        if args.refresh and not settings.has_key and briefing:
            ui.warning("未配置模型，保留现有导览；本次不重新生成。")
        else:
            briefing_errors: list[str] = []
            with ui.status("生成仓库导览…"):
                briefing = build_briefing(repo, llm, errors=briefing_errors)
            state.write_briefing(briefing)
            if briefing_errors:
                ui.warning("模型导览生成失败，已使用离线导览：" + briefing_errors[0])
    if not briefing:
        briefing = fallback_briefing(repo)
        state.write_briefing(briefing)

    ui.banner(
        repo_name=repo.name,
        repo_root=str(repo.root),
        model=settings.model,
        has_key=settings.has_key,
        state_enabled=state.enabled,
        is_git=repo.is_git,
    )
    hint = state.gitignore_hint()
    if not had_briefing and hint:
        ui.warning(hint)

    if not args.ask:
        ui.show_briefing(briefing)
    if args.briefing_only:
        return 0

    if not settings.has_key:
        ui.warning(setup_hint())
        if args.ask:
            ui.error("一次性提问需要配置模型 Key。")
            return 2

    tools = _make_tools(repo, state, ui)
    agent = _make_agent(repo, state, briefing, llm, tools, ui)
    session = Session(repo=repo, state=state, tools=tools, agent=agent, briefing=briefing)

    if args.ask:
        return 0 if _ask(session, args.ask, ui, debug=args.debug) else 1

    return repl(session, llm, ui, root, debug=args.debug)


def repl(
    session: Session,
    llm: LLMClient | None,
    ui: UI,
    root: Path,
    *,
    debug: bool = False,
) -> int:
    ui.info("输入 /help 查看命令；直接输入问题即可。")
    while True:
        try:
            line = ui.prompt()
        except EOFError:
            ui.console.print()
            break
        except KeyboardInterrupt:
            ui.console.print()
            continue

        if not line:
            continue
        if line.startswith("/"):
            try:
                keep_going = _handle_command(line, session, llm, ui, root)
            except KeyboardInterrupt:
                ui.console.print()
                ui.warning("命令已中断。")
                continue
            except Exception as exc:
                if debug:
                    ui.console.print_exception()
                ui.error(f"命令失败：{type(exc).__name__}: {exc}")
                continue
            if not keep_going:
                break
        else:
            _ask(session, line, ui, debug=False)

    session.state.save()
    if session.state.enabled:
        ui.success(f"已退出。进度保存在 {session.state.dir}")
    else:
        ui.info("已退出（--no-state，未写入进度）。")
    return 0


def _ask(
    session: Session,
    question: str,
    ui: UI,
    *,
    debug: bool,
    grade_quiz: bool = True,
) -> bool:
    if session.agent is None:
        ui.warning("当前没有配置模型，只能使用 /map、/progress、/run 等本地命令。")
        return False
    try:
        return bool(session.agent.ask(question, grade_quiz=grade_quiz))
    except LLMError as exc:
        ui.error(str(exc))
        return False
    except KeyboardInterrupt:
        ui.warning("已中断本轮回答。")
        return False
    except Exception as exc:
        if debug:
            ui.console.print_exception()
        ui.error(f"本轮失败：{type(exc).__name__}: {exc}")
        return False


def _handle_command(
    line: str,
    session: Session,
    llm: LLMClient | None,
    ui: UI,
    root: Path,
) -> bool:
    command, _, rest = line.partition(" ")
    command = command.lower().strip()
    rest = rest.strip()

    if command in {"/exit", "/quit"}:
        return False
    if command == "/help":
        ui.show_help()
        return True
    if command == "/clear":
        ui.clear()
        return True
    if command == "/map":
        ui.show_briefing(session.briefing)
        return True
    if command == "/roadmap":
        ui.show_roadmap(session.state.progress)
        return True
    if command == "/progress":
        ui.show_progress(session.state.progress)
        return True
    if command == "/notes":
        ui.show_notes(session.state.read_notes())
        return True
    if command == "/refresh":
        _refresh(session, llm, ui, root)
        return True
    if command == "/learn":
        if session.agent is None:
            ui.warning("需要配置模型后才能进入讲解模式。")
            return True
        try:
            session.agent.teach(rest or None)
        except ValueError as exc:
            ui.warning(str(exc))
        except LLMError as exc:
            ui.error(str(exc))
        return True
    if command == "/ask":
        if not rest:
            ui.warning("用法：/ask <你的问题>")
            return True
        _ask(session, rest, ui, debug=False, grade_quiz=False)
        return True
    if command == "/quiz":
        if session.agent is None:
            ui.warning("需要配置模型后才能出题。")
            return True
        try:
            session.agent.quiz()
        except LLMError as exc:
            ui.error(str(exc))
        return True
    if command == "/think":
        client = session.agent.llm if session.agent is not None else llm
        if client is None:
            ui.warning("需要配置模型后才能切换思考模式。")
            return True
        target = rest.lower().strip()
        if target in {"", "status"}:
            state = "开启" if client.thinking_enabled else "关闭"
            ui.info(f"思考模式：{state}（当前会话）")
            ui.info("用法：/think on | /think off | /think status")
            return True
        if target in {"on", "enable", "enabled", "true", "1"}:
            desired = True
        elif target in {"off", "disable", "disabled", "false", "0"}:
            desired = False
        else:
            ui.warning("用法：/think on | /think off | /think status")
            return True
        if client.thinking_enabled == desired:
            ui.info(f"思考模式已经是{'开启' if desired else '关闭'}状态。")
            return True
        client.set_thinking(desired)
        if session.agent is not None:
            session.agent.reset_history()
        if desired:
            ui.warning("已开启思考模式：更慢、更贵，思维链计入输出；对话历史已清空。")
        else:
            ui.success("已关闭思考模式；对话历史已清空。")
        return True
    if command == "/run":
        if not rest:
            ui.warning("用法：/run pytest tests/test_x.py")
            return True
        result = session.tools.execute("run_command", {"command": rest})
        if not result.ok:
            ui.warning(result.content)
            return True
        ui.console.print(result.content, markup=False)
        if session.agent is not None:
            try:
                session.agent.explain_run(rest, result.content)
            except LLMError as exc:
                ui.error(str(exc))
        return True
    if command == "/done":
        chapter = session.state.current_chapter
        session.state.mark_chapter(chapter, "done", rest)
        ui.success(f"已标记第 {chapter} 章完成。")
        return True
    if command == "/skip":
        if not rest:
            ui.warning("用法：/skip <暂时不想深入的主题>")
            return True
        session.state.record_skipped(rest)
        ui.success(f"已记录跳过：{rest}")
        return True

    ui.warning(f"未知命令：{command}。输入 /help 查看命令。")
    return True


def _refresh(session: Session, llm: LLMClient | None, ui: UI, root: Path) -> None:
    with ui.status("重新扫描仓库…"):
        new_repo = scan_repo(root)
    if llm is not None:
        briefing_errors: list[str] = []
        with ui.status("重新生成导览…"):
            new_briefing = build_briefing(new_repo, llm, errors=briefing_errors)
        if briefing_errors:
            ui.warning("模型导览生成失败，已使用离线导览：" + briefing_errors[0])
    elif session.briefing:
        new_briefing = session.briefing
        ui.warning("未配置模型，保留现有导览；仓库扫描已更新。")
    else:
        new_briefing = fallback_briefing(new_repo)
        ui.warning("未配置模型，导览使用离线生成。")
    session.state.write_briefing(new_briefing)
    new_tools = _make_tools(new_repo, session.state, ui)
    new_agent = _make_agent(new_repo, session.state, new_briefing, llm, new_tools, ui)
    session.repo = new_repo
    session.briefing = new_briefing
    session.tools = new_tools
    session.agent = new_agent
    ui.success("导览已刷新。对话历史已重置，避免旧路径/旧行号污染。")
    ui.show_briefing(new_briefing)


if __name__ == "__main__":
    raise SystemExit(main())
