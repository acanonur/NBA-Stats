"""The installer gives the Mac app a key to write with, and is careful with it (BD-1).

``backend/scripts/macos/install.sh`` runs ``ensure_api_key.py``, which puts a random key into
``hardwood.env``. These tests are the rules the file's docstring states:

* a key is made when there is none, with ``secrets.token_urlsafe(32)``, and **never** replaces or
  alters one that exists (quotes, spaces around ``=`` and a duplicate line included: the first
  active assignment decides, as the server reads the file);
* an *empty* assignment is "no key" to the server, and a second line after it would be ignored
  (the first assignment wins), so the empty line itself is the one replaced;
* the settings file is mode ``0600`` at the end, whatever it was, with no temporary file left;
* the key is **never printed**, by the helper or by the installer;
* what the helper writes is what the server reads back, through the server's own parser.

Everything is on temporary files; the end-to-end group drives the real ``install.sh`` with
``--no-load --skip-pip`` against a temporary home, exactly like ``test_worker_jobs.py`` does.
"""

from __future__ import annotations

import importlib.util
import os
import re
import secrets
import shutil
import stat
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

from nbastats.accounts import config as auth_config

MACOS = Path(__file__).resolve().parents[1] / "scripts" / "macos"
KEY_LINE = re.compile(r"^HARDWOOD_API_KEY=([A-Za-z0-9_-]{43})$", re.MULTILINE)
needs_bash = pytest.mark.skipif(shutil.which("bash") is None, reason="bash is required")


@pytest.fixture(scope="module")
def helper() -> ModuleType:
    spec = importlib.util.spec_from_file_location("ensure_api_key", MACOS / "ensure_api_key.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def keys_in(text: str) -> list[str]:
    return KEY_LINE.findall(text)


# --------------------------------------------------------------------------- the helper


def test_a_missing_file_gets_a_key_and_is_private(helper: ModuleType, tmp_path: Path) -> None:
    env_file = tmp_path / "hardwood.env"
    assert helper.ensure_api_key(env_file) == "created"
    assert mode(env_file) == 0o600
    [key] = keys_in(env_file.read_text())
    assert len(key) == 43  # 32 random bytes, base64url without padding


def test_a_file_without_a_key_keeps_every_line_and_gets_one_appended(
    helper: ModuleType, tmp_path: Path
) -> None:
    env_file = tmp_path / "hardwood.env"
    original = "# my settings\nHARDWOOD_NEWS=off\n\nINGEST_POLL_SECONDS=120\n"
    env_file.write_text(original)
    env_file.chmod(0o644)  # a settings file the person made themselves, readable by others
    assert helper.ensure_api_key(env_file) == "created"
    text = env_file.read_text()
    assert text.startswith(original)
    assert len(keys_in(text)) == 1
    assert mode(env_file) == 0o600  # it holds a secret now


def test_a_file_that_does_not_end_in_a_newline_is_not_glued_to_the_key(
    helper: ModuleType, tmp_path: Path
) -> None:
    env_file = tmp_path / "hardwood.env"
    env_file.write_text("HARDWOOD_NEWS=off")
    helper.ensure_api_key(env_file)
    lines = env_file.read_text().splitlines()
    assert lines[0] == "HARDWOOD_NEWS=off"
    assert len(keys_in(env_file.read_text())) == 1


@pytest.mark.parametrize(
    "line",
    [
        "HARDWOOD_API_KEY=my-own-key",
        "HARDWOOD_API_KEY = my-own-key",
        '  HARDWOOD_API_KEY="my-own-key"  ',
        "HARDWOOD_API_KEY='my-own-key'",
    ],
)
def test_an_existing_key_is_never_touched(helper: ModuleType, tmp_path: Path, line: str) -> None:
    env_file = tmp_path / "hardwood.env"
    original = f"HARDWOOD_NEWS=on\n{line}\nLOG_LEVEL=INFO\n"
    env_file.write_text(original)
    env_file.chmod(0o640)
    assert helper.ensure_api_key(env_file) == "kept"
    assert env_file.read_text() == original  # byte for byte
    assert mode(env_file) == 0o600  # only the mode is tightened


def test_the_first_assignment_wins_as_the_server_reads_it(
    helper: ModuleType, tmp_path: Path
) -> None:
    env_file = tmp_path / "hardwood.env"
    original = "HARDWOOD_API_KEY=first\nHARDWOOD_API_KEY=second\n"
    env_file.write_text(original)
    assert helper.ensure_api_key(env_file) == "kept"
    assert env_file.read_text() == original


@pytest.mark.parametrize(
    "line",
    [
        "# HARDWOOD_API_KEY=commented-out",
        "   # HARDWOOD_API_KEY=also-commented",
        "XHARDWOOD_API_KEY=x",
        "export HARDWOOD_API_KEY=not-read-by-the-server",
    ],
)
def test_text_the_server_does_not_read_as_a_key_is_not_a_key(
    helper: ModuleType, tmp_path: Path, line: str
) -> None:
    """A commented-out line and a line the server's parser would not match give no key, so one
    is made (and the person's text is still there, untouched)."""
    env_file = tmp_path / "hardwood.env"
    env_file.write_text(f"{line}\n")
    assert helper.ensure_api_key(env_file) == "created"
    text = env_file.read_text()
    assert text.startswith(f"{line}\n")
    assert len(keys_in(text)) == 1


@pytest.mark.parametrize(
    "empty", ["HARDWOOD_API_KEY=", "HARDWOOD_API_KEY=   ", 'HARDWOOD_API_KEY=""']
)
def test_an_empty_assignment_is_filled_in_place_not_followed_by_a_second_line(
    helper: ModuleType, tmp_path: Path, empty: str
) -> None:
    """The server reads the first assignment and ignores later ones, so a key appended after an
    empty line would do nothing. The empty line is the one replaced."""
    env_file = tmp_path / "hardwood.env"
    env_file.write_text(f"HARDWOOD_NEWS=on\n{empty}\nLOG_LEVEL=INFO\n")
    assert helper.ensure_api_key(env_file) == "filled"
    lines = env_file.read_text().splitlines()
    assert lines[0] == "HARDWOOD_NEWS=on" and lines[2] == "LOG_LEVEL=INFO"
    assert KEY_LINE.match(lines[1])
    assert sum(1 for line in lines if line.startswith("HARDWOOD_API_KEY")) == 1
    assert mode(env_file) == 0o600


def test_a_second_run_keeps_the_key_the_first_one_made(helper: ModuleType, tmp_path: Path) -> None:
    env_file = tmp_path / "hardwood.env"
    helper.ensure_api_key(env_file)
    first = env_file.read_text()
    assert helper.ensure_api_key(env_file) == "kept"
    assert env_file.read_text() == first


def test_every_key_is_fresh_and_comes_from_token_urlsafe_32(
    helper: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sizes: list[int] = []
    real = secrets.token_urlsafe

    def spy(nbytes: int | None = None) -> str:
        sizes.append(nbytes)  # type: ignore[arg-type]
        return real(nbytes)

    monkeypatch.setattr(helper.secrets, "token_urlsafe", spy)
    made = set()
    for n in range(5):
        env_file = tmp_path / f"env{n}"
        helper.ensure_api_key(env_file)
        made.add(keys_in(env_file.read_text())[0])
    assert sizes == [32] * 5
    assert len(made) == 5


def test_the_server_reads_back_exactly_what_the_helper_wrote(
    helper: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Through the server's own parser (``load_dotenv``): the key is in its environment and the
    settings say a key is required."""
    from nbastats import config as stats_config

    env_file = tmp_path / "hardwood.env"
    env_file.write_text("HARDWOOD_NEWS=on\nHARDWOOD_API_KEY=\n")  # the awkward case: empty first
    helper.ensure_api_key(env_file)
    [written] = keys_in(env_file.read_text())
    monkeypatch.delenv("HARDWOOD_API_KEY", raising=False)
    monkeypatch.delenv("HARDWOOD_NEWS", raising=False)
    auth_config.load_dotenv(env_file)
    try:
        stats_config.reset_settings_cache()
        settings = stats_config.get_settings()
        assert settings.requires_api_key and settings.api_key == written
    finally:
        os.environ.pop("HARDWOOD_API_KEY", None)
        os.environ.pop("HARDWOOD_NEWS", None)
        stats_config.reset_settings_cache()


def test_a_symlinked_settings_file_is_written_through(helper: ModuleType, tmp_path: Path) -> None:
    real = tmp_path / "dotfiles" / "hardwood.env"
    real.parent.mkdir()
    real.write_text("HARDWOOD_NEWS=off\n")
    link = tmp_path / "hardwood.env"
    link.symlink_to(real)
    helper.ensure_api_key(link)
    assert link.is_symlink()
    assert len(keys_in(real.read_text())) == 1 and real.read_text().startswith("HARDWOOD_NEWS=off")


def test_no_temporary_file_is_left_behind(helper: ModuleType, tmp_path: Path) -> None:
    env_file = tmp_path / "hardwood.env"
    helper.ensure_api_key(env_file)
    helper.ensure_api_key(env_file)
    assert sorted(path.name for path in tmp_path.iterdir()) == ["hardwood.env"]


def test_the_helper_prints_one_word_and_never_the_key(
    helper: ModuleType, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    env_file = tmp_path / "hardwood.env"
    assert helper.main(["ensure_api_key.py", str(env_file)]) == 0
    [key] = keys_in(env_file.read_text())
    captured = capsys.readouterr()
    assert captured.out == "created\n" and captured.err == ""
    assert key not in captured.out + captured.err
    assert helper.main(["ensure_api_key.py"]) == 2  # wrong usage


@pytest.mark.skipif(os.geteuid() == 0, reason="root can write anywhere")
def test_an_unwritable_folder_is_an_error_not_a_silent_skip(
    helper: ModuleType, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    folder = tmp_path / "locked"
    folder.mkdir()
    env_file = folder / "hardwood.env"
    env_file.write_text("HARDWOOD_NEWS=off\n")
    folder.chmod(0o500)
    try:
        assert helper.main(["ensure_api_key.py", str(env_file)]) == 1
    finally:
        folder.chmod(0o700)
    assert "could not write" in capsys.readouterr().err


def test_the_helper_needs_nothing_outside_the_standard_library() -> None:
    body = (MACOS / "ensure_api_key.py").read_text(encoding="utf-8")
    imported = set(re.findall(r"^(?:from|import) (\w+)", body, re.MULTILINE))
    assert imported <= {"__future__", "os", "secrets", "sys", "tempfile", "pathlib"}


# --------------------------------------------------------------------------- install.sh, end to end


def _install(tmp_path: Path, *extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(MACOS / "install.sh"), "--no-load", "--skip-pip", "--python", sys.executable,
         *extra],
        capture_output=True,
        text=True,
        env={
            "PATH": os.environ["PATH"],
            "HOME": os.environ.get("HOME", "/root"),
            "HARDWOOD_INSTALL_HOME": str(tmp_path),
            "HARDWOOD_INSTALL_ALLOW_NON_MAC": "1",
        },
        timeout=180,
    )


def _env_file(tmp_path: Path) -> Path:
    return tmp_path / "Library" / "Application Support" / "Hardwood" / "hardwood.env"


@needs_bash
def test_a_fresh_install_makes_a_private_key_and_never_prints_it(tmp_path: Path) -> None:
    pytest.importorskip("ensurepip")
    result = _install(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    env_file = _env_file(tmp_path)
    assert mode(env_file) == 0o600
    [key] = keys_in(env_file.read_text())
    assert key not in result.stdout + result.stderr
    assert "Created a private API key" in result.stdout


@needs_bash
def test_installing_again_never_replaces_the_key(tmp_path: Path) -> None:
    pytest.importorskip("ensurepip")
    assert _install(tmp_path).returncode == 0
    env_file = _env_file(tmp_path)
    first = env_file.read_text()
    again = _install(tmp_path)
    assert again.returncode == 0, again.stderr
    assert env_file.read_text() == first
    assert "already has an API key" in again.stdout


@needs_bash
def test_a_settings_file_without_a_key_gets_one_and_keeps_the_persons_lines(
    tmp_path: Path,
) -> None:
    pytest.importorskip("ensurepip")
    assert _install(tmp_path).returncode == 0
    env_file = _env_file(tmp_path)
    env_file.write_text("HARDWOOD_NEWS=off\n")
    env_file.chmod(0o644)
    again = _install(tmp_path)
    assert again.returncode == 0, again.stderr
    text = env_file.read_text()
    assert text.startswith("HARDWOOD_NEWS=off\n") and len(keys_in(text)) == 1
    assert mode(env_file) == 0o600


@needs_bash
def test_a_key_the_person_chose_survives_an_install(tmp_path: Path) -> None:
    pytest.importorskip("ensurepip")
    assert _install(tmp_path).returncode == 0
    env_file = _env_file(tmp_path)
    mine = "HARDWOOD_NEWS=off\nHARDWOOD_API_KEY=a-key-i-chose-myself\n"
    env_file.write_text(mine)
    assert _install(tmp_path).returncode == 0
    assert env_file.read_text() == mine


@needs_bash
def test_no_api_key_leaves_the_file_without_one_and_says_so(tmp_path: Path) -> None:
    pytest.importorskip("ensurepip")
    result = _install(tmp_path, "--no-api-key")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "HARDWOOD_API_KEY=" not in "".join(
        line for line in _env_file(tmp_path).read_text().splitlines(keepends=True)
        if not line.lstrip().startswith("#")
    )
    assert "Not creating an API key" in result.stdout


@needs_bash
def test_a_dry_run_mentions_the_key_and_writes_nothing(tmp_path: Path) -> None:
    result = subprocess.run(
        ["bash", str(MACOS / "install.sh"), "--dry-run", "--python", sys.executable],
        capture_output=True,
        text=True,
        env={
            "PATH": os.environ["PATH"],
            "HOME": os.environ.get("HOME", "/root"),
            "HARDWOOD_INSTALL_HOME": str(tmp_path),
            "HARDWOOD_INSTALL_ALLOW_NON_MAC": "1",
        },
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    assert "an API key" in result.stdout and "never printed" in result.stdout
    assert list(tmp_path.iterdir()) == []


def test_the_example_settings_file_has_no_active_key_line() -> None:
    """The example is copied for new installs; the key is appended by the helper, never shipped."""
    text = (MACOS / "hardwood.env.example").read_text(encoding="utf-8")
    active = [
        line for line in text.splitlines() if line.strip() and not line.lstrip().startswith("#")
    ]
    assert not [line for line in active if line.split("=", 1)[0].strip() == "HARDWOOD_API_KEY"]
