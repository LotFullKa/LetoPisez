from __future__ import annotations

import subprocess
from pathlib import Path

from config.settings import settings


class GitSync:
    def __init__(self, root: Path) -> None:
        self.root = root

    def _run(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            args,
            cwd=self.root,
            check=False,
            capture_output=True,
            text=True,
        )

    def _ensure_repo(self) -> None:
        git_dir = self.root / ".git"
        if not git_dir.exists():
            self._run("git", "init")

    def sync(self, summary: str | None = None) -> None:
        if not settings.git.auto_commit:
            return

        self._ensure_repo()
        self._run("git", "add", "-A")

        message = settings.git.commit_message_template
        if summary:
            message = f"{message} - {summary}"

        commit_result = self._run("git", "commit", "-m", message)
        if commit_result.returncode != 0 and "nothing to commit" in (
            commit_result.stderr or ""
        ).lower():
            return

        self._run("git", "push")


git_sync = GitSync(settings.vault_path)

