from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture()
def tmp_repo(tmp_path: Path) -> Path:
    (tmp_path / "README.md").write_text(
        "# Demo Service\n\nA tiny FastAPI service for testing the reader.\n",
        encoding="utf-8",
    )
    (tmp_path / "pyproject.toml").write_text(
        """
[project]
name = "demo-service"
version = "0.1.0"
dependencies = ["fastapi", "pytest", "redis"]

[project.scripts]
demo = "app.main:main"
""".strip()
        + "\n",
        encoding="utf-8",
    )
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "app" / "main.py").write_text(
        '"""Demo entry."""\n\n\ndef main() -> int:\n    return 1\n',
        encoding="utf-8",
    )
    (tmp_path / "app" / "middleware.py").write_text(
        "class TraceMiddleware:\n"
        "    async def __call__(self, scope, receive, send):\n"
        "        await self.app(scope, receive, send)\n",
        encoding="utf-8",
    )
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_main.py").write_text(
        "from app.main import main\n\n\ndef test_main() -> None:\n    assert main() == 1\n",
        encoding="utf-8",
    )
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "adr").mkdir()
    (tmp_path / "docs" / "adr" / "0001-demo.md").write_text(
        "# ADR 0001\n\nUse ASGI middleware for tracing.\n",
        encoding="utf-8",
    )
    (tmp_path / ".env").write_text("SECRET_KEY=do-not-read\n", encoding="utf-8")
    (tmp_path / ".gitignore").write_text(".code-reader/\n.env\n.venv/\n", encoding="utf-8")
    (tmp_path / ".venv").mkdir()
    (tmp_path / ".venv" / "ignored.py").write_text("raise SystemExit\n", encoding="utf-8")
    return tmp_path
