from __future__ import annotations

import subprocess
from pathlib import Path

from config.settings import settings


class GitSyncError(Exception):
    pass


class GitPullError(GitSyncError):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


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

    def pull(self) -> None:
        self._ensure_repo()
        # Попробовать аккуратно подтянуть изменения по умолчанию
        result = self._run("git", "pull", "--rebase")
        if result.returncode != 0:
            stderr = (result.stderr or "").strip()
            # Если remote не настроен/нет tracking-ветки — просто игнорируем pull
            benign_markers = (
                "There is no tracking information",
                "No remote repository specified",
                "could not read Username",
            )
            if any(marker in stderr for marker in benign_markers):
                return
            raise GitPullError(stderr or "git pull failed")

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

