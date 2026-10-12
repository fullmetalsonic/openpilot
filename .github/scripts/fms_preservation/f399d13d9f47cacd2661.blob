"""Read remote branch names without downloading objects; fetch only a selection."""
from __future__ import annotations

import re
import subprocess

from openpilot.common.repo_update import child_lock_kwargs
from .fork_remote import FORK_URL, local_branch_name

UPSTREAM_URL = "https://github.com/ajouatom/openpilot.git"


def _git(repo, *args, timeout=30):
  try:
    proc = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=timeout, **child_lock_kwargs())
    return proc.returncode, ((proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")).strip()
  except (OSError, subprocess.TimeoutExpired) as exc:
    return 1, str(exc)


def _same_url(left, right):
  def identity(url):
    value = url.strip().rstrip("/").removesuffix(".git")
    return value.replace("git@github.com:", "https://github.com/").replace("ssh://git@github.com/", "https://github.com/")
  return identity(left) == identity(right)


def sources(repo):
  rc, origin = _git(repo, "remote", "get-url", "origin")
  upstream = "origin" if rc == 0 and _same_url(origin, UPSTREAM_URL) else "ajouatom"
  return {upstream: UPSTREAM_URL, "fullmetalsonic": FORK_URL}


def list_branches(repo):
  rc, current = _git(repo, "branch", "--show-current")
  if rc:
    return {"ok": False, "rc": rc, "out": current}
  urls = sources(repo)
  items = [{"kind": "local", "ref": current, "name": current, "label": current}] if current else []
  errors = {}
  for remote, url in urls.items():
    rc, output = _git(repo, "ls-remote", "--heads", url, timeout=30)
    if rc:
      errors[remote] = output
      continue
    names = set()
    for line in output.splitlines():
      parts = line.split("\t", 1)
      if len(parts) == 2 and re.fullmatch(r"[0-9a-fA-F]{40,64}", parts[0]) and parts[1].startswith("refs/heads/"):
        names.add(parts[1][11:])
    for name in sorted(names):
      items.append({"kind": "remote", "ref": f"{remote}/{name}", "remote": remote, "name": name, "label": name})
  return {"ok": True, "summary_key": "git_result_branch_list_done", "summary_vars": {"count": len(items)},
          "branch_items": items, "branches": [item["ref"] for item in items], "current_branch": current,
          "remotes": list(urls), "remote_urls": urls, "remote_errors": errors, "fetch": "",
          "out": "\n".join(f"{remote}: {error}" for remote, error in errors.items())}


def _valid_name(repo, name):
  return bool(name) and not name.startswith("-") and _git(repo, "check-ref-format", f"refs/heads/{name}")[0] == 0


def checkout_branch(repo, body):
  branch = str(body.get("branch") or "").strip()
  kind = str(body.get("kind") or "").strip()
  name = str(body.get("name") or "").strip()
  remote = str(body.get("remote") or "").strip()
  urls = sources(repo)
  if not kind:
    for candidate in urls:
      if branch.startswith(candidate + "/"):
        kind, remote, name = "remote", candidate, branch[len(candidate) + 1:]
        break
    else:
      kind, name = "local", branch
  if kind == "local":
    name = name or branch
    if not _valid_name(repo, name):
      return {"ok": False, "error": "invalid branch", "error_code": "INVALID_BRANCH"}
    rc, out = _git(repo, "switch", "--", name, timeout=120)
  elif kind == "remote":
    if remote not in urls or not _valid_name(repo, name):
      return {"ok": False, "error": "invalid remote branch", "error_code": "INVALID_BRANCH"}
    rc, configured = _git(repo, "remote", "get-url", remote)
    if rc:
      rc, out = _git(repo, "remote", "add", remote, urls[remote])
      if rc:
        return {"ok": False, "rc": rc, "out": out}
    elif not _same_url(configured, urls[remote]):
      return {"ok": False, "error": f"Reserved remote {remote} has an unexpected URL"}
    mapping = f"+refs/heads/{name}:refs/remotes/{remote}/{name}"
    rc_specs, specs = _git(repo, "config", "--get-all", f"remote.{remote}.fetch")
    if mapping not in specs.splitlines() and f"+refs/heads/*:refs/remotes/{remote}/*" not in specs.splitlines():
      rc, out = _git(repo, "config", "--add", f"remote.{remote}.fetch", mapping)
      if rc:
        return {"ok": False, "rc": rc, "out": out}
    ref = f"refs/remotes/{remote}/{name}"
    rc, out = _git(repo, "fetch", "--no-tags", "--no-recurse-submodules", remote,
                   f"+refs/heads/{name}:{ref}", timeout=180)
    if rc:
      return {"ok": False, "rc": rc, "out": out}
    local = local_branch_name(remote, name)
    if _git(repo, "show-ref", "--verify", "--quiet", f"refs/heads/{local}")[0] == 0:
      rc_upstream, upstream = _git(repo, "for-each-ref", "--format=%(upstream:short)", f"refs/heads/{local}")
      upstream_remote, separator, upstream_name = upstream.partition("/")
      rc_url, upstream_url = _git(repo, "remote", "get-url", upstream_remote) if separator else (1, "")
      if rc_upstream or rc_url or upstream_name != name or not _same_url(upstream_url, urls[remote]):
        return {"ok": False, "error": f"Local branch {local} tracks a different repository; use its local entry"}
      rc, out = _git(repo, "switch", "--", local, timeout=120)
    else:
      rc, out = _git(repo, "switch", "-c", local, "--track", f"{remote}/{name}", timeout=120)
    name = local
  else:
    return {"ok": False, "error": "invalid branch kind", "error_code": "INVALID_BRANCH"}
  return {"ok": rc == 0, "rc": rc, "out": out, "summary_key": "git_result_checkout_done",
          "summary_vars": {"branch": name}, "empty_output": not out}


def sync_branches(repo):
  rc, current = _git(repo, "branch", "--show-current")
  if rc or not current:
    return {"ok": False, "error": "Cannot clean local branches without an installed branch"}
  rc, refs = _git(repo, "for-each-ref", "--format=%(refname:short)", "refs/heads")
  if rc:
    return {"ok": False, "rc": rc, "out": refs}
  output = []
  for name in refs.splitlines():
    if name != current:
      rc, out = _git(repo, "branch", "-D", "--", name)
      output.append(out)
      if rc:
        return {"ok": False, "rc": rc, "out": "\n".join(output)}
  for remote, url in sources(repo).items():
    rc, configured = _git(repo, "remote", "get-url", remote)
    if rc == 0 and _same_url(configured, url):
      rc, out = _git(repo, "remote", "prune", remote)
      output.append(out)
      if rc:
        return {"ok": False, "rc": rc, "out": "\n".join(output)}
  out = "\n".join(output).strip()
  return {"ok": True, "rc": 0, "out": out, "summary_key": "git_result_sync_done", "empty_output": not out}
