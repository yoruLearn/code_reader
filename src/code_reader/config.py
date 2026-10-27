"""Configuration loading for code-reader.

Secrets are never written into the target repository.  The lookup order is:

1. explicit CLI arguments (model / base URL only)
2. environment variables
3. the per-user ``config.toml``
4. sensible defaults (DeepSeek Flash, the provider's supported fast model)
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-flash"
OPENAI_BASE_URL = "https://api.openai.com/v1"
OPENAI_MODEL = "gpt-4o-mini"


def user_config_path() -> Path:
    """Return the per-user configuration path (never inside the target repo)."""

    if os.name == "nt":
        appdata = os.environ.get("APPDATA")
        base = Path(appdata) if appdata else Path.home() / "AppData" / "Roaming"
        return base / "code-reader" / "config.toml"
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg) if xdg else Path.home() / ".config"
    return base / "code-reader" / "config.toml"


def _read_toml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        raw = path.read_bytes()
        # Windows editors frequently save UTF-8 with a BOM; tomllib rejects it.
        if raw.startswith(b"\xef\xbb\xbf"):
            raw = raw[3:]
        data = tomllib.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _first_env(*names: str) -> tuple[str | None, str | None]:
    for name in names:
        value = os.environ.get(name)
        if value and value.strip():
            return value.strip(), name
    return None, None


def _as_text(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _as_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class Settings:
    api_key: str | None = field(default=None, repr=False)
    base_url: str = DEFAULT_BASE_URL
    model: str = DEFAULT_MODEL
    max_tokens: int = 4096
    temperature: float = 0.2
    timeout: float = 90.0
    thinking: str = "disabled"
    config_path: Path = field(default_factory=user_config_path)

    @property
    def has_key(self) -> bool:
        return bool(self.api_key)

    @property
    def provider_label(self) -> str:
        host = self.base_url.split("://", 1)[-1].split("/", 1)[0]
        return host or "openai-compatible"

    @property
    def masked_key(self) -> str:
        if not self.api_key:
            return "<未配置>"
        if len(self.api_key) <= 8:
            return "***"
        return f"{self.api_key[:3]}...{self.api_key[-2:]}"


def load_settings(
    *,
    api_key: str | None = None,
    base_url: str | None = None,
    model: str | None = None,
) -> Settings:
    """Load settings without ever printing or persisting the secret."""

    config_path = user_config_path()
    config = _read_toml(config_path)

    env_key, key_source = _first_env(
        "CODE_READER_API_KEY",
        "DEEPSEEK_API_KEY",
        "OPENAI_API_KEY",
    )
    config_key = _as_text(config.get("api_key"))
    effective_key = api_key or env_key or config_key

    env_base, _ = _first_env("CODE_READER_BASE_URL")
    config_base = _as_text(config.get("base_url"))
    effective_base = base_url or env_base or config_base

    if not effective_base:
        # DeepSeek is the intended provider for this build.  A config file key
        # without an explicit base URL is also treated as DeepSeek.
        effective_base = OPENAI_BASE_URL if key_source == "OPENAI_API_KEY" else DEFAULT_BASE_URL

    env_model, _ = _first_env("CODE_READER_MODEL")
    default_model = OPENAI_MODEL if "openai.com" in effective_base.lower() else DEFAULT_MODEL
    effective_model = model or env_model or _as_text(config.get("model")) or default_model

    max_tokens = _as_int(
        os.environ.get("CODE_READER_MAX_TOKENS", config.get("max_tokens")),
        4096,
    )
    temperature = _as_float(
        os.environ.get("CODE_READER_TEMPERATURE", config.get("temperature")),
        0.2,
    )
    timeout = _as_float(
        os.environ.get("CODE_READER_TIMEOUT", config.get("timeout")),
        90.0,
    )
    thinking = str(
        os.environ.get("CODE_READER_THINKING", config.get("thinking", "disabled"))
    ).strip().lower()
    if thinking not in {"enabled", "disabled"}:
        thinking = "disabled"

    return Settings(
        api_key=effective_key or None,
        base_url=effective_base.rstrip("/"),
        model=str(effective_model),
        max_tokens=max(256, min(max_tokens, 32000)),
        temperature=max(0.0, min(temperature, 1.5)),
        timeout=max(10.0, min(timeout, 600.0)),
        thinking=thinking,
        config_path=config_path,
    )


def setup_hint() -> str:
    """Human readable setup hint used by the CLI and the offline mode."""

    return (
        "未配置模型 Key。可设置环境变量后重试：\n"
        '  $env:CODE_READER_API_KEY = "sk-..."\n'
        '  $env:CODE_READER_BASE_URL = "https://api.deepseek.com"\n'
        '  $env:CODE_READER_MODEL = "deepseek-flash"\n'
        f"或写入用户配置：{user_config_path()}"
    )
