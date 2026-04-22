from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path


class CodexRunner:
    def __init__(self, workdir: str | None = None) -> None:
        self._workdir = Path(workdir or os.getenv("CODER_WORKDIR", "/home/hyc/projects/codex-workbench")).resolve()

    def run(self, prompt: str) -> str:
        with tempfile.NamedTemporaryFile(mode="r", encoding="utf-8", delete=False) as output_file:
            output_path = output_file.name

        try:
            completed = subprocess.run(
                [
                    "codex",
                    "exec",
                    "--skip-git-repo-check",
                    "--ephemeral",
                    "-C",
                    str(self._workdir),
                    "-s",
                    "read-only",
                    "-o",
                    output_path,
                    prompt,
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            if completed.returncode != 0:
                stderr = (completed.stderr or completed.stdout or "Codex exec failed").strip()
                raise RuntimeError(stderr)

            output = Path(output_path).read_text(encoding="utf-8").strip()
            if not output:
                raise RuntimeError("Codex returned no final message")
            return output
        finally:
            try:
                Path(output_path).unlink(missing_ok=True)
            except OSError:
                pass
