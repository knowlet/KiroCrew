r"""``common_asserts`` must not execute host commands while building container script.

The docker wrapper expands ``$(common_asserts …)`` on the HOST. An unquoted
heredoc there treats backticks and ``$()`` as shell syntax on the host before
``docker run`` starts — not inside the container.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "smoke-linux-packages.sh"

_ALLOWED_LIST_CMDS = ('dpkg -L "$pkg"', 'rpm -ql "$pkg"')


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


def _bash(script: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", "-c", script],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )


def test_common_asserts_rejects_unknown_list_command() -> None:
    proc = _bash(f"{_common_asserts_definition()}\ncommon_asserts 'id'")
    assert proc.returncode != 0
    assert "unsupported list command" in (proc.stderr or "")


def test_common_asserts_quoted_heredoc_has_no_host_expansion() -> None:
    for list_cmd in _ALLOWED_LIST_CMDS:
        proc = _bash(f"{_common_asserts_definition()}\ncommon_asserts {list_cmd!r}")
        assert proc.returncode == 0, proc.stderr
        assert "No such file or directory" not in (proc.stderr or ""), proc.stderr
        assert list_cmd in proc.stdout
        assert 'exe=$(basename "$entry" .desktop)' in proc.stdout


def test_unquoted_heredoc_with_sentinel_runs_on_host(tmp_path: Path) -> None:
    """Documents the failure mode the quoted heredoc removes."""
    marker = tmp_path / "host-expanded"
    probe = f"""
common_asserts_old() {{
  cat <<ASSERTS
    host_probe=$(touch {marker})
ASSERTS
}}
common_asserts_old
"""
    proc = _bash(probe)
    assert proc.returncode == 0
    assert marker.is_file(), "unquoted heredoc backticks must execute on the host"


def test_common_asserts_emits_nightly_path_in_comment_without_executing() -> None:
    proc = _bash(f"{_common_asserts_definition()}\n" f"common_asserts {_ALLOWED_LIST_CMDS[0]!r}")
    assert "/opt/KiroCrew Nightly" in proc.stdout
    assert proc.returncode == 0
    assert not (proc.stderr or "").strip()


def test_common_asserts_uses_quoted_heredoc_delimiter() -> None:
    text = SCRIPT.read_text(encoding="utf-8")
    start = text.index("common_asserts() {")
    chunk = text[start:]
    assert "<<'ASSERTS'" in chunk, "common_asserts must expand body with a quoted heredoc delimiter"
    assert "cat <<ASSERTS" not in chunk.replace("<<'ASSERTS'", "")


def test_smoke_linux_packages_shell_syntax() -> None:
    subprocess.run(["bash", "-n", str(SCRIPT)], check=True)
