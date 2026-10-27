# code-reader

一个在任意仓库根目录启动的 console 代码阅读导师。它先给一页仓库导览与阅读路线，再通过对话带你读代码、讲设计取舍、拆 HTTP 与基础设施中间件。所有关键结论要求带 `path:line`，并区分“代码事实 / Spec 规划 / 推断”。

## 安装

要求 Python 3.11+，推荐使用 `uv`：

```powershell
cd D:\AI\code-reader
uv tool install --editable .
```

或：

```powershell
pipx install --editable D:\AI\code-reader
```

## 配置模型

支持任意 OpenAI-compatible API。设置环境变量：

```powershell
$env:CODE_READER_API_KEY = "sk-..."
$env:CODE_READER_BASE_URL = "https://api.deepseek.com"
$env:CODE_READER_MODEL = "deepseek-flash"
```

默认值已经是 `deepseek-flash` + `https://api.deepseek.com`；只有需要换模型/网关时才需要设置后两项。

> DeepSeek 当前可用的 API 模型名是 `deepseek-flash` 和 `deepseek-v4-pro`。
> `deepseek-v4.1-Flash` 是展示名，传给 API 会返回 400。
>
> 默认 `thinking = "disabled"`：代码阅读场景更快、更稳，也避免思维链耗尽输出预算。
> 需要更强的推理时，可在配置文件加 `thinking = "enabled"`；工具调用链会自动回传
> `reasoning_content`。进入 REPL 后也可以随时 `/think on`、`/think off`，
> 只影响当前会话；切换时会清空对话历史，避免思考/非思考消息混用。

也支持 `DEEPSEEK_API_KEY` / `OPENAI_API_KEY` 作为 Key 的回退，以及用户配置文件：

- Windows: `%APPDATA%\code-reader\config.toml`
- 其他平台: `~/.config/code-reader/config.toml`

```toml
api_key = "sk-..."
base_url = "https://api.deepseek.com"
model = "deepseek-flash"
max_tokens = 4096
temperature = 0.2
timeout = 90
thinking = "disabled"   # 默认关闭思考模式；改成 "enabled" 可开启
```

## 使用

```powershell
cd D:\AI\e-co
code-reader
```

首次运行会扫描当前仓库，生成 `.code-reader/briefing.md`，打印导览并进入 REPL。

```text
/learn [章节]     按路线学习；不带参数则继续当前章节
/ask <问题>       自由提问
/quiz             针对当前章节出题
/run <命令>       提议执行测试/静态检查，确认后运行
/think on|off|status  切换当前会话的思考模式（切换会清空对话历史）
/roadmap          打印 0–5 章学习路线、目标、状态和下一步
/progress         查看进度
/map              重新打印导览
/notes            查看笔记
/done [备注]      标记当前章节完成
/skip <主题>      记录一个暂时跳过的坑
/refresh          重新扫描并生成导览
/help             命令帮助
/exit             退出
```

直接输入自然语言等同于 `/ask`。

**输入 `/` 会弹出命令菜单**：↑/↓ 选择，Enter 确认。菜单里同时显示命令语法、中文作用和示例。无参数命令会直接执行；需要参数的命令会先补全命令并等待你输入参数（例如 `/learn` 选中后继续输入 `1`）。

问答、`/learn`、`/quiz` 和 `/run` 的解释默认**流式输出**：真实终端里会边生成边刷新 Markdown；输出被重定向到文件/管道时退化为纯文本增量，避免 ANSI 控制符污染日志。仓库导览仍一次性渲染 Markdown。

非交互用法：

```powershell
code-reader --briefing-only                 # 只生成/打印导览
code-reader --ask "请求进来后 trace_id 怎么传？"  # 一次性提问
code-reader --no-state                      # 临时探索，不写 .code-reader/
code-reader --refresh                       # 重新扫描并生成导览
```

## 权限边界

- 默认只读：读文件、grep、git log/show/blame。
- 只写 `.code-reader/`（briefing、progress、notes）。
- `/run` 只允许 `pytest`、`python -m pytest|ruff|mypy`、`ruff`、`mypy`、`make test`，且每次都要确认。
- `/run` 会优先使用目标仓库自己的 `.venv`（如果存在），避免拿 code-reader 的 Python 去跑目标仓库的测试。
- 禁止安装依赖、改源码、提交、任意网络请求。
- 不读取 `.env` 等密钥文件；不会把 Key 写入仓库。

## 无 Key 模式

没有配置模型时仍可：

- 生成确定性的离线导览；
- 查看 `/map`、`/progress`、`/notes`；
- 用 `/run` 执行允许的测试命令；
- 不能进行 `/ask`、`/learn`、`/quiz` 的模型对话。

## 开发

```powershell
uv sync --extra dev
uv run pytest -q
uv run ruff check .
uv run code-reader --repo D:\AI\e-co --briefing-only --no-state
```

## 目录

```text
src/code_reader/
├── cli.py        # 参数解析、REPL、命令分发
├── repo.py       # 仓库识别、扫描、上下文构建
├── briefing.py   # 导览生成与离线回退
├── state.py      # .code-reader 状态
├── tools.py      # 只读工具、run 白名单、进度工具
├── agent.py      # 工具调用循环
├── llm.py        # OpenAI-compatible 客户端
├── prompts.py    # 系统提示、课程与导览提示
├── commands.py   # 斜杠命令单一来源（菜单 + 帮助）
├── config.py     # 环境变量 / 用户配置
└── ui.py         # Rich 终端界面
```

## 作者

yoruLearn

