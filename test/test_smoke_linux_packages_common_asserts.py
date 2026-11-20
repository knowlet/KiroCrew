r"""``common_asserts`` must not execute host commands while building container script.

The docker wrapper expands ``$(common_asserts …)`` on the HOST. An unquoted
heredoc there treats backticks and ``$()`` as shell syntax on the host before
``docker run`` starts — not inside the container.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "smoke-linux-packages.sh"

_ALLOWED_LIST_CMDS = ('dpkg -L "$pkg"', 'rpm -ql "$pkg"')


def _bash_executable(posix_shell: str) -> str:
    """Bash that can parse this script, never Windows' WSL launcher.

    On a Windows runner ``shutil.which("bash")`` resolves to
    ``C:\\Windows\\System32\\bash.exe``. That launcher writes a UTF-16
    "Windows Subsystem for Linux has no installed distributions" line and
    exits 1, so a PATH lookup fails every assertion before the script runs.
    ``posix_test_shell`` is Git Bash on that host. A path whose file name is
    already bash is the interpreter to run; looking it up again finds the
    launcher.
    """
    if Path(posix_shell).stem.lower() == "bash":
        return posix_shell
    resolved = shutil.which("bash")
    assert resolved is not None, f"{posix_shell} is not bash and no bash is on PATH"
    return resolved


def test_a_bash_path_is_not_looked_up_again() -> None:
    """The Windows choice, checked without a Windows host.

    Git Bash's path is what the shared fixture returns there. Re-resolving
    ``bash`` from PATH selects the WSL launcher instead.
    """
    git_bash = "C:/Program Files/Git/bin/bash.exe"
    assert _bash_executable(git_bash) == git_bash


@pytest.fixture
def bash_exe(posix_test_shell: str) -> str:
    return _bash_executable(posix_test_shell)


def _common_asserts_definition() -> str:
    text = SCRIPT.read_text(encoding="utf-8")
    start = text.index("common_asserts() {")
    depth = 0
    for i in range(start, len(text)):
        ch = text[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    raise AssertionError("common_asserts() body not closed in smoke-linux-packages.sh")


def _bash(bash_exe: str, script: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [bash_exe, "-c", script],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


def test_common_asserts_rejects_unknown_list_command(bash_exe: str) -> None:
    proc = _bash(bash_exe, f"{_common_asserts_definition()}\ncommon_asserts 'id'")
    assert proc.returncode != 0
    assert "unsupported list command" in (proc.stderr or "")


def test_common_asserts_quoted_heredoc_has_no_host_expansion(bash_exe: str) -> None:
    for list_cmd in _ALLOWED_LIST_CMDS:
        proc = _bash(bash_exe, f"{_common_asserts_definition()}\ncommon_asserts {list_cmd!r}")
        assert proc.returncode == 0, proc.stderr
        assert "No such file or directory" not in (proc.stderr or ""), proc.stderr
        assert list_cmd in proc.stdout
        assert 'exe=$(basename "$entry" .desktop)' in proc.stdout


def test_unquoted_heredoc_with_sentinel_runs_on_host(bash_exe: str, tmp_path: Path) -> None:
    """Documents the failure mode the quoted heredoc removes."""
    marker = tmp_path / "host-expanded"
    # The path is inside the script body, so Git Bash does not convert
    # backslashes the way it converts an argv path. ``as_posix`` is a path
    # both Git Bash and a POSIX bash can ``touch``.
    marker_shell = marker.as_posix().replace("'", "'\\''")
    probe = f"""
common_asserts_old() {{
  cat <<ASSERTS
    host_probe=$(touch '{marker_shell}')
ASSERTS
}}
common_asserts_old
"""
    proc = _bash(bash_exe, probe)
    assert proc.returncode == 0, proc.stderr
    assert marker.is_file(), "unquoted heredoc command substitution must execute on the host"


def test_common_asserts_emits_nightly_path_in_comment_without_executing(bash_exe: str) -> None:
    proc = _bash(
        bash_exe,
        f"{_common_asserts_definition()}\n" f"common_asserts {_ALLOWED_LIST_CMDS[0]!r}",
    )
    assert "/opt/KiroCrew Nightly" in proc.stdout
    assert proc.returncode == 0
    assert not (proc.stderr or "").strip()


def test_common_asserts_uses_quoted_heredoc_delimiter() -> None:
    text = SCRIPT.read_text(encoding="utf-8")
    start = text.index("common_asserts() {")
    chunk = text[start:]
    assert "<<'ASSERTS'" in chunk, "common_asserts must expand body with a quoted heredoc delimiter"
    assert "cat <<ASSERTS" not in chunk.replace("<<'ASSERTS'", "")


def test_smoke_linux_packages_shell_syntax(bash_exe: str) -> None:
    proc = subprocess.run(
        [bash_exe, "-n", str(SCRIPT)],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert proc.returncode == 0, proc.stderr
