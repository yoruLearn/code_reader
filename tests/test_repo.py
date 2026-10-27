from __future__ import annotations

from pathlib import Path

from code_reader.repo import find_repo_root, scan_repo


def test_find_repo_root_plain_dir(tmp_repo: Path) -> None:
    root, is_git = find_repo_root(tmp_repo)
    assert root == tmp_repo
    assert is_git is False


def test_scan_repo_collects_expected_facts(tmp_repo: Path) -> None:
    repo = scan_repo(tmp_repo)

    assert repo.name == tmp_repo.name
    assert "Python" in repo.languages
    assert repo.languages["Python"] >= 2
    assert "README.md" in repo.docs
    assert "app/main.py" in repo.entry_candidates
    assert "app/main.py" in repo.key_excerpts
    assert "FastAPI" in repo.framework_hints
    assert ".env" not in {file.path for file in repo.files}
    assert ".venv/ignored.py" not in {file.path for file in repo.files}
    assert "main" in repo.context().lower()
    assert "tests" in repo.tree


def test_scan_repo_excerpt_does_not_include_secret(tmp_repo: Path) -> None:
    repo = scan_repo(tmp_repo)
    blob = "\n".join(repo.key_excerpts.values())
    assert "do-not-read" not in blob
