r"""Host-side expansion of ``common_asserts`` must stay inert.

``common_asserts`` is expanded on the HOST through an unquoted heredoc
(``cat <<ASSERTS``) so the generated container script can embed ``\$`` escapes.
In that shape, an unescaped backtick in the heredoc body runs as command
substitution on the host before ``docker run`` — which once tried to execute
``/opt/KiroCrew`` and corrupted the emitted script.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "smoke-linux-packages.sh"


def _common_asserts_definition() -> str:
    text = SCRIPT.read_text(encoding="utf-8")
    start = text.index("common_asserts() {")
    end = text.index("\n}\n", start) + 2
    return text[start:end]


def test_common_asserts_emits_without_host_command_substitution() -> None:
    proc = subprocess.run(
        [
            "bash",
            "-c",
            (
                f"{_common_asserts_definition()}\n"
                "common_asserts 'dpkg -L \"$pkg\"'"
            ),
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    assert not (proc.stderr or "").strip(), proc.stderr
    assert 'exe=$(basename "$entry" .desktop)' in proc.stdout


def test_common_asserts_heredoc_carries_no_unescaped_backticks() -> None:
    text = SCRIPT.read_text(encoding="utf-8")
    start = text.index("common_asserts() {")
    chunk = text[start:]
    open_marker = chunk.index("cat <<ASSERTS")
    close_marker = chunk.index("\nASSERTS\n", open_marker)
    body = chunk[open_marker:close_marker]
    stripped = body.replace("\\`", "")
    assert "`" not in stripped, (
        "an unquoted ASSERTS heredoc treats backticks as host command "
        "substitution; escape them or drop the backticks"
    )
