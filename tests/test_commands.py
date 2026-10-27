from __future__ import annotations

from prompt_toolkit.completion import CompleteEvent
from prompt_toolkit.document import Document

from code_reader.commands import COMMANDS
from code_reader.ui import SlashCommandCompleter


def _complete(text: str):
    return list(
        SlashCommandCompleter().get_completions(Document(text), CompleteEvent())
    )


def test_slash_lists_all_commands_with_chinese_meta() -> None:
    completions = _complete("/")
    assert len(completions) == len(COMMANDS)
    assert any(item.text == "/learn " for item in completions)
    assert any("按路线" in item.display_meta_text for item in completions)
    assert all(item.display_meta_text for item in completions)


def test_slash_filters_and_offers_think_subcommands() -> None:
    assert [item.text for item in _complete("/ro")] == ["/roadmap"]
    assert [item.text for item in _complete("/th")] == ["/think "]
    assert {item.text for item in _complete("/think ")} == {"on", "off", "status"}
    assert _complete("/ask ") == []
