"""
Tool action dispatchers.

- `run_tool_job(job)` — async streaming dispatcher (used by /api/tools/start).
- `dispatch_sync(request, body)` — synchronous dispatcher (used by /api/tools).

Both share helpers from `jobs.py` (job state, exec wrappers, branch utils) and
action/command policy from `actions.py`. Some action implementations still have
async/sync variants, so keep behavior changes small and deliberate.
"""
from __future__ import annotations

import asyncio
import glob
import json
import os
import re
import shlex
import shutil
import subprocess
import traceback
from typing import Any, Dict, List, Optional, Tuple

from aiohttp import web

from openpilot.common.async_process import prepare_repo, run_locked_thread
from openpilot.common.repo_update import RepoBusyError, child_lock_kwargs, repo_lock
from openpilot.system.hardware import HARDWARE

from ...config import PARAMS_BACKUP_PATH
from ...services.auto_update import clear_recovered_git_ref_error
from ...services.branch_catalog import list_branches, checkout_branch, sync_branches
from ...services.git_config import prepare_git_pull, repair_git_config
from ...services.git_state import did_git_pull_update, write_git_pull_time
from ...services.git_status import clear_git_status_cache
from ...services.params import HAS_PARAMS, Params, ParamKeyType, get_all_param_values_for_backup
from . import jobs
from .actions import normalize_action, validate_action, validate_shell_argv


TMUX_LOG_PATH = "/data/media/tmux.log"
GIT_UPDATE_COMMIT_LIMIT = 20
GIT_UPDATE_DISPLAY_LIMIT = 3


def capture_tmux_log_sync() -> Tuple[int, str]:
  try:
    os.remove(TMUX_LOG_PATH)
  except FileNotFoundError:
    pass
  except OSError as e:
    return 1, str(e)

  proc = subprocess.run(
    ["tmux", "capture-pane", "-pq", "-S-1000"],
    capture_output=True,
    text=True,
  )
  if proc.returncode != 0:
    return proc.returncode, (proc.stderr or proc.stdout or "").strip()

  os.makedirs(os.path.dirname(TMUX_LOG_PATH), exist_ok=True)
  with open(TMUX_LOG_PATH, "w", encoding="utf-8") as f:
    f.write(proc.stdout or "")
  return 0, ""


def _safe_int(value: Any, default: int = 0) -> int:
  try:
    return int(str(value).strip())
  except Exception:
    return default


def _parse_git_shortstat(text: str) -> Dict[str, int]:
  body = str(text or "")

  def match_count(pattern: str) -> int:
    match = re.search(pattern, body)
    return _safe_int(match.group(1)) if match else 0

  return {
    "files": match_count(r"(\d+)\s+files?\s+changed"),
    "insertions": match_count(r"(\d+)\s+insertions?\(\+\)"),
    "deletions": match_count(r"(\d+)\s+deletions?\(-\)"),
  }


def _parse_git_commit_lines(text: str) -> List[Dict[str, str]]:
  commits: List[Dict[str, str]] = []
  for line in str(text or "").splitlines():
    line = line.strip()
    if not line:
      continue
    parts = line.split("\t", 1)
    if len(parts) == 2:
      commit_hash, message = parts
    else:
      commit_hash, message = "", parts[0]
    commits.append({"hash": commit_hash.strip(), "message": message.strip()})
  return commits


def _format_commit_count(count: int) -> str:
  return f"{count} commit" + ("" if count == 1 else "s")


def _format_git_update_summary(
  *,
  before: str,
  after: str,
  commit_count: int,
  commits: List[Dict[str, str]],
  shortstat: Dict[str, int],
  raw_out: str,
) -> Dict[str, Any]:
  updated = bool(before and after and before != after and commit_count > 0)
  if not updated:
    display = "Already up to date"
    return {
      "updated": False,
      "before": before,
      "after": after,
      "commit_count": 0,
      "commits": [],
      "files_changed": 0,
      "insertions": 0,
      "deletions": 0,
      "display": display,
      "card_summary": display,
      "detail": (display + "\n\nGit output\n" + str(raw_out or "").strip()).strip(),
    }

  shown = commits[:GIT_UPDATE_DISPLAY_LIMIT]
  remaining = max(0, commit_count - len(shown))
  lines = ["New Updates", ""]
  for commit in shown:
    message = commit.get("message") or commit.get("hash") or "Update"
    lines.append(message)
  if remaining > 0:
    lines.append(f"and {remaining} more commits")

  stat_bits = [_format_commit_count(commit_count)]
  files = int(shortstat.get("files") or 0)
  insertions = int(shortstat.get("insertions") or 0)
  deletions = int(shortstat.get("deletions") or 0)
  if files:
    stat_bits.append(f"{files} file" + ("" if files == 1 else "s"))
  stat_bits.append(f"+{insertions} -{deletions}")
  lines.extend(["", " | ".join(stat_bits)])
  display = "\n".join(lines).strip()

  commit_log = "\n".join(
    f"{commit.get('hash', '').strip()} {commit.get('message', '').strip()}".strip()
    for commit in commits
  ).strip()
  detail_parts = [display]
  if commit_log:
    detail_parts.extend(["", "Commit log", commit_log])
  raw = str(raw_out or "").strip()
  if raw:
    detail_parts.extend(["", "Git output", raw])

  return {
    "updated": True,
    "before": before,
    "after": after,
    "commit_count": commit_count,
    "commits": commits,
    "files_changed": files,
    "insertions": insertions,
    "deletions": deletions,
    "display": display,
    "card_summary": display,
    "detail": "\n".join(detail_parts).strip(),
  }


async def _build_git_update_summary_async(repo_dir: str, before: str, after: str, raw_out: str) -> Dict[str, Any]:
  if not before or not after or before == after:
    return _format_git_update_summary(
      before=before,
      after=after,
      commit_count=0,
      commits=[],
      shortstat={},
      raw_out=raw_out,
    )

  range_spec = f"{before}..{after}"
  rc_count, count_out = await jobs.capture_exec(["git", "rev-list", "--count", range_spec], cwd=repo_dir, timeout=15)
  commit_count = _safe_int(count_out) if rc_count == 0 else 0
  rc_log, log_out = await jobs.capture_exec(
    ["git", "log", "--format=%h%x09%s", f"-{GIT_UPDATE_COMMIT_LIMIT}", range_spec],
    cwd=repo_dir,
    timeout=15,
  )
  commits = _parse_git_commit_lines(log_out if rc_log == 0 else "")
  rc_stat, stat_out = await jobs.capture_exec(["git", "diff", "--shortstat", before, after], cwd=repo_dir, timeout=20)
  shortstat = _parse_git_shortstat(stat_out if rc_stat == 0 else "")
  return _format_git_update_summary(
    before=before,
    after=after,
    commit_count=commit_count or len(commits),
    commits=commits,
    shortstat=shortstat,
    raw_out=raw_out,
  )


def _build_git_update_summary_sync(repo_dir: str, before: str, after: str, raw_out: str) -> Dict[str, Any]:
  if not before or not after or before == after:
    return _format_git_update_summary(
      before=before,
      after=after,
      commit_count=0,
      commits=[],
      shortstat={},
      raw_out=raw_out,
    )

  range_spec = f"{before}..{after}"

  def run_git(args: List[str], timeout: float = 20) -> Tuple[int, str]:
    try:
      proc = subprocess.run(
        ["git", *args],
        cwd=repo_dir,
        capture_output=True,
        text=True,
        timeout=timeout,
      )
      out = (proc.stdout or "") + (("\n" + proc.stderr) if proc.stderr else "")
      return proc.returncode, out.strip()
    except Exception as exc:
      return 1, str(exc)

  rc_count, count_out = run_git(["rev-list", "--count", range_spec], timeout=15)
  commit_count = _safe_int(count_out) if rc_count == 0 else 0
  rc_log, log_out = run_git(["log", "--format=%h%x09%s", f"-{GIT_UPDATE_COMMIT_LIMIT}", range_spec], timeout=15)
  commits = _parse_git_commit_lines(log_out if rc_log == 0 else "")
  rc_stat, stat_out = run_git(["diff", "--shortstat", before, after], timeout=20)
  shortstat = _parse_git_shortstat(stat_out if rc_stat == 0 else "")
  return _format_git_update_summary(
    before=before,
    after=after,
    commit_count=commit_count or len(commits),
    commits=commits,
    shortstat=shortstat,
    raw_out=raw_out,
  )


async def _repair_git_job(job: dict[str, Any], repo_dir: str, **kwargs: Any) -> bool:
  jobs.progress(job, message="checking Git configuration", current=0, total=2)
  rc, out = await run_locked_thread(repair_git_config, repo_dir, **kwargs)
  clear_git_status_cache()
  jobs.append(job, out + "\n")
  if rc != 0:
    jobs.finish(job, ok=False, result=jobs.result_from_log(job, rc))
  return rc == 0


def _needs_repo_lock(action: str, body: dict) -> bool:
  return (action.startswith("git_") and action not in ("git_log", "git_branch_list")) or action == "rebuild_all" or (
    action == "shell_cmd" and str(body.get("cmd") or "").strip().startswith("git ")
  )


async def run_tool_job(job: Dict[str, Any]) -> None:
  if not _needs_repo_lock(normalize_action(job.get("action")), job.get("payload") or {}):
    return await _run_tool_job(job)
  try:
    with repo_lock():
      await prepare_repo("/data/openpilot")
      return await _run_tool_job(job)
  except (RepoBusyError, OSError, RuntimeError) as exc:
    busy = isinstance(exc, RepoBusyError)
    jobs.finish(job, ok=False, result={"ok": False, "error": str(exc), "error_code": "GIT_BUSY" if busy else "GIT_PRECHECK_FAILED"},
                error=str(exc), error_code="GIT_BUSY" if busy else "GIT_PRECHECK_FAILED")


async def _run_tool_job(job: Dict[str, Any]) -> None:
  action = normalize_action(job.get("action"))
  body = job.get("payload") or {}
  repo_dir = "/data/openpilot"

  try:
    action_error = validate_action(action)
    if action_error:
      error, error_code = action_error
      jobs.finish(job, ok=False, result={"ok": False, "error": error, "error_code": error_code}, error=error, error_code=error_code)
      return

    if action == "git_pull":
      rc_config, out_config, target_head = await run_locked_thread(prepare_git_pull, repo_dir)
      jobs.append(job, out_config + "\n")
      if rc_config:
        jobs.finish(job, ok=False, result=jobs.result_from_log(job, rc_config))
        return
      jobs.progress(job, message="git reset --hard", current=1, total=2)
      jobs.append(job, "$ git reset --hard\n")
      rc_reset = await jobs.stream_exec(job, ["git", "reset", "--hard"], cwd=repo_dir, timeout=120)
      if rc_reset != 0:
        jobs.finish(job, ok=False, result=jobs.result_from_log(job, rc_reset))
        return

      rc_before, before_out = await jobs.capture_exec(["git", "rev-parse", "HEAD"], cwd=repo_dir, timeout=10)
      before_head = before_out.strip() if rc_before == 0 else ""
      jobs.append(job, "\n$ git pull\n")
      jobs.progress(job, message="git pull", current=2, total=2)
      rc = await jobs.stream_exec(job, ["git", "merge", "--ff-only", target_head], cwd=repo_dir, timeout=180)
      rc_after, after_out = await jobs.capture_exec(["git", "rev-parse", "HEAD"], cwd=repo_dir, timeout=10)
      after_head = after_out.strip() if rc_after == 0 else ""
      clear_git_status_cache()
      if rc == 0:
        clear_recovered_git_ref_error()
      if rc == 0 and did_git_pull_update(job.get("log") or ""):
        write_git_pull_time()
      update_summary = await _build_git_update_summary_async(repo_dir, before_head, after_head, job.get("log") or "") if rc == 0 else None
      result = jobs.result_from_log(job, rc, update_summary=update_summary, summary_key="git_result_pull_done") if update_summary else jobs.result_from_log(job, rc)
      jobs.finish(job, ok=rc == 0, result=result)
      return

    if action == "git_sync":
      result = await run_locked_thread(sync_branches, repo_dir)
      if result.get("out"):
        jobs.append(job, result["out"] + "\n")
      jobs.finish(job, ok=bool(result.get("ok")), result=result)
      return

    if action == "git_reset":
      mode = (body.get("mode") or "hard").strip()
      target = (body.get("target") or "HEAD").strip()
      if mode not in ("hard", "soft", "mixed"):
        jobs.finish(
          job,
          ok=False,
          result={"ok": False, "error": "bad mode", "error_code": "INVALID_RESET_MODE"},
          error="bad mode",
          error_code="INVALID_RESET_MODE",
        )
        return

      if not await _repair_git_job(job, repo_dir):
        return
      jobs.progress(job, message=f"git reset --{mode} {target}", current=1, total=1)
      rc = await jobs.stream_exec(job, ["git", "reset", f"--{mode}", target], cwd=repo_dir, timeout=120)
      clear_git_status_cache()
      jobs.finish(job, ok=rc == 0, result=jobs.result_from_log(job, rc, summary_key="git_result_reset_done", summary_vars={"mode": mode, "target": target}))
      return

    if action == "git_checkout":
      result = await run_locked_thread(checkout_branch, repo_dir, body)
      if result.get("out"):
        jobs.append(job, result["out"] + "\n")
      jobs.finish(job, ok=bool(result.get("ok")), result=result)
      return

    if action == "git_remote_set":
      url = str(job.get("payload", {}).get("url") or "").strip()
      if not url:
        jobs.finish(job, ok=False, result={"ok": False, "error": "missing url"}, error="missing url")
        return

      jobs.progress(job, message=f"set-url origin {url}", current=1, total=2)
      rc_set = await jobs.stream_exec(job, ["git", "remote", "set-url", "origin", url], cwd=repo_dir, timeout=30)
      if rc_set != 0:
        jobs.finish(job, ok=False, result=jobs.result_from_log(job, rc_set))
        return

      if not await _repair_git_job(job, repo_dir, remote="origin", repair_upstream=False):
        return
      jobs.finish(job, ok=True, result=jobs.result_from_log(job, 0, summary_key="git_result_remote_set_done"))
      return

    if action == "git_branch_list":
      result = await asyncio.to_thread(list_branches, repo_dir)
      result.update(device_type=HARDWARE.get_device_type(), branch_prefix=jobs.get_branch_prefix())
      if result.get("out"):
        jobs.append(job, result["out"] + "\n")
      jobs.finish(job, ok=bool(result.get("ok")), result=result)
      return

    if action == "git_remote_add":
      name = str(body.get("name") or "").strip()
      url = str(body.get("url") or "").strip()
      if not name or not url:
        jobs.finish(job, ok=False, result={"ok": False, "error": "missing name or url"}, error="missing name or url")
        return

      rc_remotes, remotes_out = await jobs.capture_exec(["git", "remote"], cwd=repo_dir, timeout=15)
      remotes = remotes_out.split() if rc_remotes == 0 else []
      remote_exists = name in remotes

      setup_cmd = ["git", "remote", "set-url", name, url] if remote_exists else ["git", "remote", "add", name, url]
      setup_label = "set-url" if remote_exists else "add"
      jobs.progress(job, message=f"git remote {setup_label} {name}", current=1, total=2)
      rc_setup = await jobs.stream_exec(job, setup_cmd, cwd=repo_dir, timeout=30)
      if rc_setup != 0:
        jobs.finish(job, ok=False, result=jobs.result_from_log(job, rc_setup))
        return

      if not await _repair_git_job(job, repo_dir, remote=name, repair_upstream=False):
        return

      rc_remote_urls, remote_urls_out = await jobs.capture_exec(["git", "remote", "-v"], cwd=repo_dir, timeout=15)
      if rc_remote_urls == 0 and remote_urls_out:
        jobs.append(job, "\n$ git remote -v\n")
        jobs.append(job, remote_urls_out + "\n")
      jobs.finish(job, ok=True, result=jobs.result_from_log(job, 0, summary_key="git_result_remote_add_done", summary_vars={"name": name}))
      return

    if action == "git_log":
      count = min(int(body.get("count") or 20), 50)
      jobs.progress(job, message="git log", current=1, total=1)
      rc, out = await jobs.capture_exec(
        ["git", "log", f"--oneline", f"-{count}"],
        cwd=repo_dir,
        timeout=30,
      )
      rc_head, out_head = await jobs.capture_exec(
        ["git", "rev-parse", "--short", "HEAD"],
        cwd=repo_dir,
        timeout=10,
      )
      current_commit = out_head.strip() if rc_head == 0 else ""
      if out:
        jobs.append(job, out)
      commits = []
      for line in (out or "").splitlines():
        line = line.strip()
        if not line:
          continue
        parts = line.split(" ", 1)
        commits.append({"hash": parts[0], "message": parts[1] if len(parts) > 1 else ""})
      result = {"ok": rc == 0, "commits": commits, "current_commit": current_commit, "out": out, "summary_key": "git_result_log_done", "summary_vars": {"count": len(commits)}}
      jobs.finish(job, ok=rc == 0, result=result)
      return

    if action == "git_reset_repo_fetch":
      url = "https://github.com/ajouatom/openpilot.git"
      # Phase 0: clear stale git locks so the remote/config/fetch steps below
      # aren't blocked by a leftover *.lock from a crashed git process.
      await jobs.capture_exec(["find", ".git", "-type", "f", "-name", "*.lock", "-delete"], cwd=repo_dir, timeout=10)
      # Phase 1: ensure origin points to the correct URL and tracks every
      # branch. Installations migrated from comma may still have a narrow
      # release-tizi-staging fetch refspec, which does not exist on this
      # remote and makes even a plain `git fetch origin` fail.
      jobs.progress(job, message="configuring origin remote", current=1, total=4)

      rc_set, _ = await jobs.capture_exec(
        ["git", "remote", "set-url", "origin", url], cwd=repo_dir, timeout=15
      )
      if rc_set != 0:
        jobs.append(job, "origin not found, adding new remote\n")
        await jobs.capture_exec(["git", "remote", "remove", "origin"], cwd=repo_dir, timeout=10)
        rc_add, out_add = await jobs.capture_exec(
          ["git", "remote", "add", "origin", url], cwd=repo_dir, timeout=15
        )
        if rc_add != 0:
          jobs.append(job, f"failed to add origin: {out_add}\n")
          jobs.finish(job, ok=False, result={"ok": False, "error": f"failed to configure remote: {out_add}"})
          return
      jobs.append(job, f"origin → {url}\n")

      rc_branches, out_branches = await jobs.capture_exec(
        ["git", "remote", "set-branches", "origin", "*"], cwd=repo_dir, timeout=15
      )
      if rc_branches != 0:
        jobs.append(job, f"failed to configure origin branches: {out_branches}\n")
        jobs.finish(job, ok=False, result={"ok": False, "error": f"failed to configure origin branches: {out_branches}"})
        return
      jobs.append(job, "origin branches: *\n")

      # Phase 2: remove ALL other remotes (so only origin remains)
      jobs.progress(job, message="cleaning other remotes", current=2, total=4)
      rc_remotes, out_remotes = await jobs.capture_exec(
        ["git", "remote"], cwd=repo_dir, timeout=10
      )
      for remote_name in (out_remotes or "").splitlines():
        remote_name = remote_name.strip()
        if remote_name and remote_name != "origin":
          jobs.append(job, f"removing remote: {remote_name}\n")
          await jobs.capture_exec(
            ["git", "remote", "remove", remote_name], cwd=repo_dir, timeout=10
          )

      # Phase 3: fetch from origin only.
      # First clear remote-tracking refs + repack so a corrupt/broken ref
      # ("unable to update local ref", "cannot lock ref ... reference broken"
      # from a power-loss mid-fetch) can't block the fetch — git rebuilds
      # origin/* fresh. --force allows non-fast-forward ref updates.
      jobs.progress(job, message="git fetch origin --prune --force", current=3, total=4)
      await jobs.capture_exec(["git", "pack-refs", "--all"], cwd=repo_dir, timeout=20)
      await jobs.capture_exec(["rm", "-rf", ".git/refs/remotes/origin"], cwd=repo_dir, timeout=10)
      rc_fetch = await jobs.stream_exec(
        job, ["git", "fetch", "origin", "--prune", "--force"], cwd=repo_dir, timeout=300
      )
      if rc_fetch != 0:
        jobs.finish(job, ok=False, result=jobs.result_from_log(job, rc_fetch))
        return

      # Phase 4: list remote branches (origin/* only)
      jobs.progress(job, message="listing branches", current=4, total=4)
      rc_br, out_br = await jobs.capture_exec(
        ["git", "branch", "-r"], cwd=repo_dir, timeout=15
      )
      branches = []
      for line in (out_br or "").splitlines():
        line = line.strip()
        if not line or "->" in line:
          continue
        if not line.startswith("origin/"):
          continue
        branches.append(line.split("/", 1)[1])
      branches = sorted(set(branches))
      jobs.append(job, f"found {len(branches)} branches\n")

      result = {"ok": True, "branches": branches, "out": (job.get("log") or "").strip(), "summary_key": "git_result_reset_repo_fetch_done", "summary_vars": {"count": len(branches)}}
      jobs.finish(job, ok=True, result=result)
      return

    if action == "git_reset_repo_checkout":
      branch = str(body.get("branch") or "").strip()
      if not branch:
        jobs.finish(job, ok=False, result={"ok": False, "error": "missing branch"}, error="missing branch")
        return

      # The guarded preflight handles abandoned index.lock. Abort any
      # half-finished operation (merge/rebase/cherry-pick/am) so the checkout
      # isn't blocked, then FORCE the branch to the remote (-f discards local
      # changes that would otherwise abort the checkout). The abort/cleanup
      # steps are allowed to fail (they no-op when not applicable).
      steps = [
        ("git merge --abort", ["git", "merge", "--abort"], True),
        ("git rebase --abort", ["git", "rebase", "--abort"], True),
        ("git cherry-pick --abort", ["git", "cherry-pick", "--abort"], True),
        ("git revert --abort", ["git", "revert", "--abort"], True),
        ("git am --abort", ["git", "am", "--abort"], True),
        ("git bisect reset", ["git", "bisect", "reset"], True),
        (f"git checkout -f -B {branch} origin/{branch}", ["git", "checkout", "-f", "-B", branch, f"origin/{branch}"], False),
        (f"git reset --hard origin/{branch}", ["git", "reset", "--hard", f"origin/{branch}"], False),
        ("git clean -xfd", ["git", "clean", "-xfd"], False),
      ]
      for i, (msg, cmd, allow_fail) in enumerate(steps):
        jobs.progress(job, message=msg, current=i + 1, total=len(steps))
        rc = await jobs.stream_exec(job, cmd, cwd=repo_dir, timeout=120)
        if rc != 0 and not allow_fail:
          jobs.finish(job, ok=False, result=jobs.result_from_log(job, rc))
          return

      jobs.finish(job, ok=True, result=jobs.result_from_log(job, 0, summary_key="git_result_reset_repo_checkout_done", summary_vars={"branch": branch}))
      return

    if action == "delete_all_videos":
      jobs.progress(job, message="delete videos", current=1, total=1)
      deleted = 0
      for path in ["/data/media/0/videos"]:
        if not os.path.isdir(path):
          continue
        for fn in glob.glob(os.path.join(path, "*")):
          try:
            os.remove(fn)
            deleted += 1
            jobs.append(job, f"deleted: {os.path.basename(fn)}")
          except Exception as e:
            jobs.append(job, f"delete error: {e}")
      result = {"ok": True, "out": f"deleted files: {deleted}"}
      jobs.finish(job, ok=True, result=result)
      return

    if action == "delete_all_logs":
      jobs.progress(job, message="delete logs", current=1, total=1)
      deleted = 0
      for path in ["/data/media/0/realdata"]:
        if not os.path.isdir(path):
          continue
        for name in os.listdir(path):
          full_path = os.path.join(path, name)
          try:
            if os.path.isfile(full_path) or os.path.islink(full_path):
              os.remove(full_path)
            elif os.path.isdir(full_path):
              shutil.rmtree(full_path)
            else:
              continue
            deleted += 1
            jobs.append(job, f"deleted: {name}")
          except Exception as e:
            jobs.append(job, f"delete error: {e}")
      result = {"ok": True, "out": f"deleted entries: {deleted}"}
      jobs.finish(job, ok=True, result=result)
      return

    if action == "send_tmux_log":
      jobs.progress(job, message="capture tmux", current=1, total=1)
      rc, out = await asyncio.to_thread(capture_tmux_log_sync)
      if rc != 0:
        jobs.finish(
          job,
          ok=False,
          result={"ok": False, "error": "tmux capture failed", "error_code": "TMUX_CAPTURE_FAIL", "out": out},
          error="tmux capture failed",
          error_code="TMUX_CAPTURE_FAIL",
        )
        return
      result = {"ok": True, "out": "tmux log captured", "file": "/download/tmux.log"}
      jobs.finish(job, ok=True, result=result)
      return

    if action == "server_tmux_log":
      jobs.progress(job, message="send tmux", current=1, total=1)
      params = Params()
      params.put_nonblocking("CarrotException", "tmux_send")
      jobs.finish(job, ok=True, result={"ok": True, "out": "tmux send triggered"})
      return

    if action == "backup_settings":
      if not HAS_PARAMS or ParamKeyType is None:
        jobs.finish(
          job,
          ok=False,
          result={"ok": False, "error": "Params/ParamKeyType not available"},
          error="Params/ParamKeyType not available",
        )
        return

      jobs.progress(job, message="backup settings", current=1, total=1)
      values = get_all_param_values_for_backup()
      os.makedirs(os.path.dirname(PARAMS_BACKUP_PATH), exist_ok=True)
      with open(PARAMS_BACKUP_PATH, "w", encoding="utf-8") as f:
        json.dump(values, f, ensure_ascii=False, indent=2)
      result = {"ok": True, "out": f"backup saved ({len(values)} keys)", "file": "/download/params_backup.json"}
      jobs.finish(job, ok=True, result=result)
      return

    if action == "reset_calib":
      jobs.progress(job, message="reset calibration", current=1, total=1)
      for f in ["/data/params/d_tmp/CalibrationParams", "/data/params/d/CalibrationParams"]:
        try:
          os.remove(f)
          jobs.append(job, f"removed {f}")
        except FileNotFoundError:
          pass
        except Exception as e:
          jobs.append(job, f"error removing {f}: {e}")
      jobs.finish(job, ok=True, result={"ok": True, "out": "calibration reset"})
      await asyncio.sleep(1)
      subprocess.Popen(["sudo", "reboot"])
      return

    if action == "reboot":
      jobs.progress(job, message="request reboot", current=1, total=1)
      subprocess.Popen(["sudo", "reboot"])
      jobs.finish(job, ok=True, result={"ok": True, "out": "reboot requested"})
      return

    if action == "rebuild_all":
      jobs.progress(job, message="rebuild all", current=1, total=1)
      cmd = "cd /data/openpilot && scons -c && rm -rf prebuilt && sudo reboot"
      subprocess.Popen(["bash", "-lc", cmd])
      jobs.finish(job, ok=True, result={"ok": True, "out": "rebuild_all requested (clean + remove prebuilt + reboot)"})
      return

    if action == "shell_cmd":
      cmd_str = (body.get("cmd") or "").strip()
      if not cmd_str:
        jobs.finish(job, ok=False, result={"ok": False, "error": "missing cmd"}, error="missing cmd")
        return
      try:
        argv = shlex.split(cmd_str)
      except Exception:
        jobs.finish(job, ok=False, result={"ok": False, "error": "bad cmd format"}, error="bad cmd format")
        return
      if not argv:
        jobs.finish(job, ok=False, result={"ok": False, "error": "empty cmd"}, error="empty cmd")
        return

      alias_map = {
        "pull": ["git", "pull"],
        "status": ["git", "status"],
        "branch": ["git", "branch"],
        "log": ["git", "log"],
      }
      if argv[0] in alias_map:
        argv = alias_map[argv[0]] + argv[1:]

      validation_error = validate_shell_argv(argv)
      if validation_error:
        error, error_code, detail = validation_error
        jobs.finish(
          job,
          ok=False,
          result={
            "ok": False,
            "error": error,
            "error_code": error_code,
            "error_detail": detail,
          },
          error=error,
          error_code=error_code,
          error_detail=detail,
        )
        return

      jobs.progress(job, message=cmd_str, current=1, total=1)
      jobs.append(job, f"$ {cmd_str}")
      try:
        rc = await jobs.stream_exec(job, argv, cwd="/data/openpilot", timeout=10)
      except asyncio.TimeoutError:
        jobs.finish(
          job,
          ok=False,
          result={"ok": False, "error": "timeout", "error_code": "CMD_TIMEOUT"},
          error="timeout",
          error_code="CMD_TIMEOUT",
        )
        return
      result = {
        "ok": rc == 0,
        "out": (job.get("log") or "").strip() or "(no output)",
        "returncode": rc,
      }
      jobs.finish(job, ok=rc == 0, result=result)
      return

    jobs.finish(job, ok=False, result={"ok": False, "error": f"unknown action: {action}"}, error=f"unknown action: {action}")
  except asyncio.TimeoutError:
    jobs.finish(
      job,
      ok=False,
      result={"ok": False, "error": "timeout", "error_code": "CMD_TIMEOUT"},
      error="timeout",
      error_code="CMD_TIMEOUT",
    )
  except Exception as e:
    jobs.append(job, f"\n{traceback.format_exc()}")
    jobs.finish(job, ok=False, result={"ok": False, "error": str(e)}, error=str(e))


async def dispatch_sync(request: web.Request, body: Dict[str, Any]) -> web.Response:
  if not _needs_repo_lock(normalize_action(body.get("action")), body):
    return await _dispatch_sync(request, body)
  try:
    with repo_lock():
      await prepare_repo("/data/openpilot")
      return await _dispatch_sync(request, body)
  except (RepoBusyError, OSError, RuntimeError) as exc:
    return web.json_response({"ok": False, "error": str(exc), "error_code": "GIT_BUSY" if isinstance(exc, RepoBusyError) else "GIT_PRECHECK_FAILED"}, status=409)


async def _dispatch_sync(request: web.Request, body: Dict[str, Any]) -> web.Response:
  action = normalize_action(body.get("action"))
  action_error = validate_action(action)
  if action_error:
    error, error_code = action_error
    return web.json_response({"ok": False, "error": error, "error_code": error_code}, status=400)

  def run(cmd: List[str], cwd: Optional[str] = None) -> Tuple[int, str]:
    p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, **child_lock_kwargs())
    out = (p.stdout or "") + (("\n" + p.stderr) if p.stderr else "")
    return p.returncode, out.strip()

  try:
    REPO_DIR = "/data/openpilot"

    if action == "git_pull":
      rc_config, out_config, target_head = await run_locked_thread(prepare_git_pull, REPO_DIR)
      clear_git_status_cache()
      if rc_config != 0:
        return web.json_response({"ok": False, "rc": rc_config, "out": out_config})
      rc_before, before_out = run(["git", "rev-parse", "HEAD"], cwd=REPO_DIR)
      before_head = before_out.strip() if rc_before == 0 else ""
      rc, out = run(["git", "merge", "--ff-only", target_head], cwd=REPO_DIR)
      out = (out_config + "\n" + out).strip()
      rc_after, after_out = run(["git", "rev-parse", "HEAD"], cwd=REPO_DIR)
      after_head = after_out.strip() if rc_after == 0 else ""
      clear_git_status_cache()
      if rc == 0:
        clear_recovered_git_ref_error()
      if rc == 0 and did_git_pull_update(out):
        write_git_pull_time()
      update_summary = _build_git_update_summary_sync(REPO_DIR, before_head, after_head, out) if rc == 0 else None
      payload = {"ok": rc == 0, "rc": rc, "out": out, "summary_key": "git_result_pull_done"}
      if update_summary:
        payload["update_summary"] = update_summary
      return web.json_response(payload)

    if action == "git_sync":
      result = await run_locked_thread(sync_branches, REPO_DIR)
      return web.json_response(result)

    if action == "git_reset":
      mode = (body.get("mode") or "hard").strip()
      target = (body.get("target") or "HEAD").strip()
      if mode not in ("hard", "soft", "mixed"):
        return web.json_response({"ok": False, "error": "bad mode"}, status=400)
      rc_config, out_config = await run_locked_thread(repair_git_config, REPO_DIR)
      clear_git_status_cache()
      if rc_config != 0:
        return web.json_response({"ok": False, "rc": rc_config, "out": out_config})
      rc, out = run(["git", "reset", f"--{mode}", target], cwd=REPO_DIR)
      clear_git_status_cache()
      out = (out_config + "\n" + out).strip()
      return web.json_response({"ok": rc == 0, "rc": rc, "out": out, "summary_key": "git_result_reset_done", "summary_vars": {"mode": mode, "target": target}, "empty_output": not out})

    if action == "git_checkout":
      result = await run_locked_thread(checkout_branch, REPO_DIR, body)
      return web.json_response(result)

    if action == "git_branch_list":
      result = await asyncio.to_thread(list_branches, REPO_DIR)
      result.update(device_type=HARDWARE.get_device_type(), branch_prefix=jobs.get_branch_prefix())
      return web.json_response(result)

    if action == "git_remote_set":
      url = str(body.get("url") or "").strip()
      if not url:
        return web.json_response({"ok": False, "error": "missing url"}, status=400)
      rc, out = run(["git", "remote", "set-url", "origin", url], cwd=REPO_DIR)
      if rc != 0:
        return web.json_response({"ok": False, "rc": rc, "out": out})
      rc, out = await run_locked_thread(repair_git_config, REPO_DIR, remote="origin", repair_upstream=False)
      clear_git_status_cache()
      return web.json_response({"ok": rc == 0, "rc": rc, "out": out, "summary_key": "git_result_remote_set_done"})

    if action == "git_remote_add":
      name = (body.get("name") or "").strip()
      url = (body.get("url") or "").strip()
      if not name or not url:
        return web.json_response({"ok": False, "error": "missing name or url"}, status=400)

      rc_remotes, out_remotes = run(["git", "remote"], cwd=REPO_DIR)
      remotes = out_remotes.split() if rc_remotes == 0 else []
      remote_exists = name in remotes
      setup_cmd = ["git", "remote", "set-url", name, url] if remote_exists else ["git", "remote", "add", name, url]
      rc_setup, out_setup = run(setup_cmd, cwd=REPO_DIR)
      if rc_setup != 0:
        return web.json_response({"ok": False, "rc": rc_setup, "out": out_setup})

      rc_fetch, out_fetch = await run_locked_thread(repair_git_config, REPO_DIR, remote=name, repair_upstream=False)
      clear_git_status_cache()
      rc_remote_urls, out_remote_urls = run(["git", "remote", "-v"], cwd=REPO_DIR)
      out = (out_setup + "\n" + out_fetch + "\n\n> git remote -v\n" + (out_remote_urls if rc_remote_urls == 0 else "")).strip()
      return web.json_response({"ok": rc_fetch == 0, "rc": rc_fetch, "out": out, "summary_key": "git_result_remote_add_done", "summary_vars": {"name": name}, "empty_output": not out})

    if action == "git_log":
      count = min(int(body.get("count") or 20), 50)
      rc, out = run(["git", "log", "--oneline", f"-{count}"], cwd=REPO_DIR)
      rc_head, out_head = run(["git", "rev-parse", "--short", "HEAD"], cwd=REPO_DIR)
      current_commit = out_head.strip() if rc_head == 0 else ""
      commits = []
      for line in (out or "").splitlines():
        line = line.strip()
        if not line:
          continue
        parts = line.split(" ", 1)
        commits.append({"hash": parts[0], "message": parts[1] if len(parts) > 1 else ""})
      return web.json_response({"ok": rc == 0, "commits": commits, "current_commit": current_commit, "out": out, "summary_key": "git_result_log_done", "summary_vars": {"count": len(commits)}})

    if action == "git_reset_repo_fetch":
      url = "https://github.com/ajouatom/openpilot.git"
      out_all = ""

      # clear stale git locks so the remote/config/fetch steps aren't blocked
      run(["find", ".git", "-type", "f", "-name", "*.lock", "-delete"], cwd=REPO_DIR)
      rc_set, out_set = run(["git", "remote", "set-url", "origin", url], cwd=REPO_DIR)
      if rc_set != 0:
        run(["git", "remote", "remove", "origin"], cwd=REPO_DIR)
        rc_add, out_add = run(["git", "remote", "add", "origin", url], cwd=REPO_DIR)
        out_all += f"> git remote add origin {url}\n{out_add}\n\n"
        if rc_add != 0:
          return web.json_response({"ok": False, "error": f"failed to configure remote: {out_add}"})
      else:
        out_all += f"> git remote set-url origin {url}\n{out_set}\n\n"

      rc_branches, out_branches = run(["git", "remote", "set-branches", "origin", "*"], cwd=REPO_DIR)
      out_all += f"> git remote set-branches origin '*'\n{out_branches}\n\n"
      if rc_branches != 0:
        return web.json_response({"ok": False, "rc": rc_branches, "out": out_all.strip()})

      rc_rem, out_rem = run(["git", "remote"], cwd=REPO_DIR)
      for rname in (out_rem or "").splitlines():
        rname = rname.strip()
        if rname and rname != "origin":
          run(["git", "remote", "remove", rname], cwd=REPO_DIR)
          out_all += f"> removed remote: {rname}\n"

      # clear remote-tracking refs + repack so a corrupt/broken ref can't block
      # the fetch (git rebuilds origin/* fresh); --force allows non-ff updates
      run(["git", "pack-refs", "--all"], cwd=REPO_DIR)
      run(["rm", "-rf", ".git/refs/remotes/origin"], cwd=REPO_DIR)
      rc_fetch, out_fetch = run(["git", "fetch", "origin", "--prune", "--force"], cwd=REPO_DIR)
      out_all += f"> git fetch origin --prune --force\n{out_fetch}\n\n"
      if rc_fetch != 0:
        return web.json_response({"ok": False, "rc": rc_fetch, "out": out_all.strip()})

      rc_br, out_br = run(["git", "branch", "-r"], cwd=REPO_DIR)
      branches = []
      for line in (out_br or "").splitlines():
        line = line.strip()
        if not line or "->" in line:
          continue
        if not line.startswith("origin/"):
          continue
        branches.append(line.split("/", 1)[1])
      branches = sorted(set(branches))
      return web.json_response({"ok": True, "branches": branches, "out": out_all.strip(), "summary_key": "git_result_reset_repo_fetch_done", "summary_vars": {"count": len(branches)}, "empty_output": not out_all.strip()})

    if action == "git_reset_repo_checkout":
      branch = str(body.get("branch") or "").strip()
      if not branch:
        return web.json_response({"ok": False, "error": "missing branch"}, status=400)
      # Robust factory reset (see job path above): clear stuck lock + abort any
      # in-progress op, then force the branch to the remote. (cmd, allow_fail)
      commands = [
        (["git", "merge", "--abort"], True),
        (["git", "rebase", "--abort"], True),
        (["git", "cherry-pick", "--abort"], True),
        (["git", "revert", "--abort"], True),
        (["git", "am", "--abort"], True),
        (["git", "bisect", "reset"], True),
        (["git", "checkout", "-f", "-B", branch, f"origin/{branch}"], False),
        (["git", "reset", "--hard", f"origin/{branch}"], False),
        (["git", "clean", "-xfd"], False),
      ]
      out_all = ""
      for c, allow_fail in commands:
        rc, out = run(c, cwd=REPO_DIR)
        out_all += f"> {' '.join(c)}\n{out}\n\n"
        if rc != 0 and not allow_fail:
          return web.json_response({"ok": False, "rc": rc, "out": out_all.strip()})
      return web.json_response({"ok": True, "out": out_all.strip(), "summary_key": "git_result_reset_repo_checkout_done", "summary_vars": {"branch": branch}, "empty_output": not out_all.strip()})

    if action == "delete_all_videos":
      paths = ["/data/media/0/videos"]
      deleted = 0
      for pth in paths:
        if not os.path.isdir(pth):
          continue
        for fn in glob.glob(os.path.join(pth, "*")):
          try:
            os.remove(fn)
            deleted += 1
          except Exception:
            pass
      return web.json_response({"ok": True, "out": f"deleted files: {deleted}"})

    if action == "delete_all_logs":
      paths = ["/data/media/0/realdata"]
      deleted = 0
      for pth in paths:
        if not os.path.isdir(pth):
          continue

        for name in os.listdir(pth):
          full_path = os.path.join(pth, name)
          try:
            if os.path.isfile(full_path) or os.path.islink(full_path):
              os.remove(full_path)
              deleted += 1
            elif os.path.isdir(full_path):
              shutil.rmtree(full_path)
              deleted += 1
          except Exception as e:
            print("delete error:", e)

      return web.json_response({"ok": True, "out": f"deleted entries: {deleted}"})

    if action == "send_tmux_log":
      rc, out = capture_tmux_log_sync()
      if rc != 0:
        return web.json_response({"ok": False, "error": "tmux capture failed", "error_code": "TMUX_CAPTURE_FAIL", "out": out})

      return web.json_response({
        "ok": True,
        "out": "tmux log captured",
        "file": "/download/tmux.log",
      })

    if action == "server_tmux_log":
      params = Params()
      params.put_nonblocking("CarrotException", "tmux_send")
      return web.json_response({"ok": True, "out": "tmux send triggered"})

    if action == "backup_settings":
      if not HAS_PARAMS or ParamKeyType is None:
        return web.json_response({"ok": False, "error": "Params/ParamKeyType not available"}, status=500)

      try:
        values = get_all_param_values_for_backup()
        os.makedirs(os.path.dirname(PARAMS_BACKUP_PATH), exist_ok=True)
        with open(PARAMS_BACKUP_PATH, "w", encoding="utf-8") as f:
          json.dump(values, f, ensure_ascii=False, indent=2)
        return web.json_response({"ok": True, "out": f"backup saved ({len(values)} keys)", "file": "/download/params_backup.json"})
      except Exception as e:
        return web.json_response({"ok": False, "error": str(e)}, status=500)

    if action == "reset_calib":
      out_msg = []
      for f in ["/data/params/d_tmp/CalibrationParams", "/data/params/d/CalibrationParams"]:
        try:
          os.remove(f)
          out_msg.append(f"removed {f}")
        except FileNotFoundError:
          pass
        except Exception as e:
          out_msg.append(f"error removing {f}: {e}")
      subprocess.Popen(["bash", "-lc", "sleep 1 && sudo reboot"])
      return web.json_response({"ok": True, "out": "\n".join(out_msg) or "calibration reset"})

    if action == "reboot":
      subprocess.Popen(["sudo", "reboot"])
      return web.json_response({"ok": True, "out": "reboot requested"})

    if action == "rebuild_all":
      cmd = "cd /data/openpilot && scons -c && rm -rf prebuilt && sudo reboot"
      subprocess.Popen(["bash", "-lc", cmd])
      return web.json_response({"ok": True, "out": "rebuild_all requested (clean + remove prebuilt + reboot)"})

    if action == "shell_cmd":
      cmd_str = (body.get("cmd") or "").strip()
      if not cmd_str:
        return web.json_response({"ok": False, "error": "missing cmd"}, status=400)

      try:
        argv = shlex.split(cmd_str)
      except Exception:
        return web.json_response({"ok": False, "error": "bad cmd format"}, status=400)

      if not argv:
        return web.json_response({"ok": False, "error": "empty cmd"}, status=400)

      alias_map = {
        "pull": ["git", "pull"],
        "status": ["git", "status"],
        "branch": ["git", "branch"],
        "log": ["git", "log"],
      }
      if argv[0] in alias_map:
        argv = alias_map[argv[0]] + argv[1:]

      validation_error = validate_shell_argv(argv)
      if validation_error:
        error, error_code, detail = validation_error
        return web.json_response({"ok": False, "error": error, "error_code": error_code, "error_detail": detail}, status=403)

      try:
        p = subprocess.run(
          argv,
          cwd="/data/openpilot",
          capture_output=True,
          text=True,
          timeout=10,
        )
        out = ""
        if p.stdout:
          out += p.stdout
        if p.stderr:
          out += ("\n" + p.stderr if out else p.stderr)
        out = out.strip() or "(no output)"
        return web.json_response({"ok": True, "out": out, "returncode": p.returncode})
      except subprocess.TimeoutExpired:
        return web.json_response({"ok": False, "error": "timeout"}, status=504)
      except Exception as e:
        return web.json_response({"ok": False, "error": str(e)}, status=500)

    return web.json_response({"ok": False, "error": f"unknown action: {action}"}, status=400)

  except Exception as e:
    return web.json_response({"ok": False, "error": str(e)}, status=500)
