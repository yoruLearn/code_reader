from __future__ import annotations

from pathlib import Path

from code_reader.config import DEFAULT_MODEL, load_settings


def _isolate_config(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        "code_reader.config.user_config_path",
        lambda: tmp_path / "no-config.toml",
    )
    for name in (
        "CODE_READER_API_KEY",
        "DEEPSEEK_API_KEY",
        "OPENAI_API_KEY",
        "CODE_READER_MODEL",
        "CODE_READER_BASE_URL",
        "CODE_READER_THINKING",
    ):
        monkeypatch.delenv(name, raising=False)


def test_default_model_is_deepseek_flash(monkeypatch, tmp_path: Path) -> None:
    _isolate_config(monkeypatch, tmp_path)
    settings = load_settings()
    assert DEFAULT_MODEL == "deepseek-flash"
    assert settings.model == DEFAULT_MODEL
    assert "deepseek.com" in settings.base_url
    assert settings.thinking == "disabled"


def test_explicit_model_override(monkeypatch, tmp_path: Path) -> None:
    _isolate_config(monkeypatch, tmp_path)
    settings = load_settings(model="custom-model")
    assert settings.model == "custom-model"


def test_config_tolerates_utf8_bom(tmp_path: Path, monkeypatch) -> None:
    cfg = tmp_path / "config.toml"
    cfg.write_bytes(
        b"\xef\xbb\xbf"
        b'api_key = "test-key"\n'
        b'model = "deepseek-flash"\n'
        b'thinking = "disabled"\n'
    )
    monkeypatch.setattr("code_reader.config.user_config_path", lambda: cfg)
    for name in (
        "CODE_READER_API_KEY",
        "DEEPSEEK_API_KEY",
        "OPENAI_API_KEY",
        "CODE_READER_MODEL",
        "CODE_READER_BASE_URL",
    ):
        monkeypatch.delenv(name, raising=False)

    settings = load_settings()
    assert settings.api_key == "test-key"
    assert settings.model == "deepseek-flash"
    assert settings.thinking == "disabled"
