"""User-owned fork remote support for Carrot Web branch selection."""
from __future__ import annotations

import subprocess

from openpilot.common.repo_update import child_lock_kwargs


FORK_REMOTE = "fullmetalsonic"
FORK_URL = "https://github.com/fullmetalsonic/openpilot.git"
FORK_FETCH_REFSPEC = f"+refs/heads/*:refs/remotes/{FORK_REMOTE}/*"
FORK_LOCAL_BRANCHES = {
  "carrot-wip": "fms-carrot-wip",
  "carrot": "fms-carrot",
}


def local_branch_name(remote: str, branch: str) -> str:
  """Avoid colliding with same-named branches from the primary remote."""
  return FORK_LOCAL_BRANCHES.get(branch, branch) if remote == FORK_REMOTE else branch


def ensure_fork_remote(repo_dir: str) -> tuple[int, str]:
  """Idempotently configure only the dedicated fork remote.

  The current remote, checked-out branch, worktree, and runtime files are not
  modified. A differently configured remote with this reserved name is an
  explicit error rather than an implicit URL replacement.
  """
  messages: list[str] = []

  def git(*args: str, allowed: tuple[int, ...] = (0,)) -> str:
    proc = subprocess.run(
      ["git", *args], cwd=repo_dir, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30, **child_lock_kwargs(),
    )
    output = "\n".join(part.strip() for part in (proc.stdout, proc.stderr) if part.strip())
    if proc.returncode not in allowed:
      raise RuntimeError(output or f"git {args[0]} failed ({proc.returncode})")
    return output

  try:
    remotes = git("remote").split()
    if FORK_REMOTE not in remotes:
      git("remote", "add", FORK_REMOTE, FORK_URL)
      messages.append(f"Added {FORK_REMOTE} remote.")
    else:
      current_url = git("remote", "get-url", FORK_REMOTE)
      if current_url.rstrip("/") != FORK_URL.rstrip("/"):
        raise RuntimeError(f"Reserved remote {FORK_REMOTE} has an unexpected URL: {current_url}")

    git("config", "--local", "--replace-all", f"remote.{FORK_REMOTE}.fetch", FORK_FETCH_REFSPEC)
    messages.append(f"Verified {FORK_REMOTE} remote branch tracking.")
    return 0, "\n".join(messages)
  except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
    return 1, f"Fork remote setup failed: {exc}"
