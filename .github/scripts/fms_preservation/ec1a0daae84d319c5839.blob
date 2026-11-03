"""Confirmed, persistent rlog delivery to fullmetalsonic's public PC receiver."""
from __future__ import annotations

import asyncio
import bz2
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import os
from pathlib import Path
import random
import re
import secrets
import stat
import struct
import threading
import time
from urllib.parse import urlsplit

from aiohttp import web

from .catalog import RLOG_SOURCE_NAMES, build_routes, route_creation_key, segment_is_complete
from .paths import route_name, safe_segment

PREFIX = "/api/dashcam/pc-transfer"
RECEIVER_ENDPOINT = "http://minipc-amd5500u.tail8d9376.ts.net"
RECIPIENT = "fullmetalsonic 개인 PC"
KST = timezone(timedelta(hours=9), "Asia/Seoul")
MIN_WALL_NS = 946684800_000000000  # 2000-01-01 UTC; only a coarse clock sanity check.
MAX_WALL_NS = 4102444800_000000000  # 2100-01-01 UTC, exclusive.
MAX_FILES = 2000
MAX_JOBS = 64
MAX_JSON_BYTES = 8 * 1024 * 1024
CHUNK = 1024 * 1024
HEX32 = re.compile(r"^[0-9a-f]{32}$")
HEX64 = re.compile(r"^[0-9a-f]{64}$")
COMPONENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,199}$")
TERMINAL = {"completed", "failed", "canceled"}
TRANSPORT_LEASE = 90
TRANSIENT_ERRORS = {"receiver_unavailable", "receiver_busy", "receiver_disk_full", "transport_busy"}


class TransferError(Exception):
  def __init__(self, code: str, status: int = 409, retry_after: float = 0):
    super().__init__(code)
    self.code, self.status = code, status
    self.retry_after = retry_after


def _is_link(path: Path) -> bool:
  info = path.lstat()
  return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & 0x400)


def _sync_directory(path: Path) -> None:
  if os.name == "nt":
    return  # Windows file fsync/replace is used; chmod does not establish account ACLs.
  descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
  try:
    os.fsync(descriptor)
  finally:
    os.close(descriptor)


def _atomic_json(path: Path, payload: dict) -> None:
  path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
  if _is_link(path.parent) or (os.path.lexists(path) and _is_link(path)):
    raise TransferError("unsafe_state_path")
  temporary = path.with_name(f".{path.name}.{secrets.token_hex(8)}.tmp")
  descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
  try:
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
      json.dump(payload, stream, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
      stream.flush()
      os.fsync(stream.fileno())
    os.replace(temporary, path)
    _sync_directory(path.parent)
  finally:
    temporary.unlink(missing_ok=True)


def _read_json(path: Path) -> dict:
  if _is_link(path):
    raise TransferError("unsafe_state_path")
  with path.open("rb") as stream:
    raw = stream.read(MAX_JSON_BYTES + 1)
  if len(raw) > MAX_JSON_BYTES:
    raise TransferError("state_too_large")
  value = json.loads(raw)
  if not isinstance(value, dict):
    raise TransferError("invalid_state")
  return value


def _default_state() -> Path:
  from ...config import CARROT_STATE_DIR
  return Path(CARROT_STATE_DIR) / "pc_log_transfer"


def validate_endpoint(value: str, allow_test_http: bool = False) -> str:
  if not isinstance(value, str) or len(value) > 256:
    raise TransferError("invalid_receiver_endpoint", 400)
  parsed = urlsplit(value)
  try:
    port = parsed.port
  except ValueError:
    raise TransferError("invalid_receiver_endpoint", 400) from None
  if parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment:
    raise TransferError("invalid_receiver_endpoint", 400)
  host = parsed.hostname or ""
  production = value == RECEIVER_ENDPOINT
  testing = allow_test_http and parsed.scheme == "http" and host in {"127.0.0.1", "localhost", "::1"} and port is not None
  if not (production or testing) or value != value.strip():
    raise TransferError("invalid_receiver_endpoint", 400)
  return value


async def _bounded_read(content, limit: int) -> bytes:
  value = bytearray()
  while block := await content.read(min(64 * 1024, limit + 1 - len(value))):
    value.extend(block)
    if len(value) > limit:
      raise TransferError("request_too_large", 413)
  return bytes(value)


def _unknown_date(reason: str) -> dict:
  return {"dateBasis": "route_start", "timezone": "Asia/Seoul", "routeStartWallTimeNanos": None,
          "driveDate": None, "dateStatus": "unknown", "dateSource": "initData.wallTimeNanos", "dateReason": reason}


def _recorded_start(stream, name: str) -> tuple[int | None, str | None]:
  """Read only bounded first messages, not a LogReader that expands an entire rlog."""
  try:
    from openpilot.cereal import log
    with ExitStack() as stack:
      stream.seek(0)
      if name.endswith(".zst"):
        import zstandard
        incoming = stack.enter_context(zstandard.ZstdDecompressor().stream_reader(stream, closefd=False))
      elif name.endswith(".bz2"):
        incoming = stack.enter_context(bz2.BZ2File(stream, "rb"))
      else:
        incoming = stream
      consumed = 0

      def read_exact(size: int) -> bytes:
        nonlocal consumed
        if size < 0 or consumed + size > 16 * CHUNK:
          raise ValueError("metadata_budget")
        value = incoming.read(size)
        consumed += len(value)
        if len(value) != size:
          raise ValueError("truncated_message")
        return value

      for _ in range(16):
        header = read_exact(8)
        segments = struct.unpack_from("<I", header)[0] + 1
        if not 1 <= segments <= 128:
          raise ValueError("segment_table")
        header += read_exact(8 * ((segments + 2) // 2) - 8)
        size = sum(struct.unpack_from(f"<{segments}I", header, 4)) * 8
        if size > 8 * CHUNK:
          raise ValueError("event_budget")
        data = header + read_exact(size)
        with log.Event.from_bytes(data, traversal_limit_in_words=CHUNK, nesting_limit=64) as event:
          if event.which() == "initData":
            wall = int(event.initData.wallTimeNanos)
            return (wall, None) if MIN_WALL_NS <= wall < MAX_WALL_NS else (None, "invalid_wall_time")
      return None, "missing_init_data"
  except Exception:
    return None, "unreadable_log"
  finally:
    stream.seek(0)


def _route_date(observations: list[tuple[int | None, str | None]]) -> dict:
  if any(reason is not None for _, reason in observations):
    return _unknown_date(next(reason for _, reason in observations if reason is not None))
  values = {wall for wall, _ in observations}
  if len(values) != 1:
    return _unknown_date("inconsistent_route_start")
  wall = values.pop()
  if wall is None:
    return _unknown_date("missing_init_data")
  day = datetime.fromtimestamp(wall // 1_000_000_000, timezone.utc).astimezone(KST).date().isoformat()
  return {"dateBasis": "route_start", "timezone": "Asia/Seoul", "routeStartWallTimeNanos": str(wall),
          "driveDate": day, "dateStatus": "known", "dateSource": "initData.wallTimeNanos", "dateReason": None}


class PcLogStore:
  def __init__(self, log_root: Path | str | None = None, state_root: Path | str | None = None, *,
               allow_test_http: bool = False, receiver_endpoint: str = RECEIVER_ENDPOINT):
    if log_root is None:
      from ...config import DASHCAM_ROOT
      log_root = DASHCAM_ROOT
    self.log_root = Path(log_root).resolve()
    self.state_root = Path(state_root) if state_root is not None else _default_state()
    self.state_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    if _is_link(self.state_root):
      raise TransferError("unsafe_state_path")
    self.jobs_root = self.state_root / "jobs"
    self.jobs_root.mkdir(exist_ok=True, mode=0o700)
    if _is_link(self.jobs_root):
      raise TransferError("unsafe_state_path")
    self.lock = threading.RLock()
    self.allow_test_http = allow_test_http
    self.endpoint = validate_endpoint(receiver_endpoint, allow_test_http)

  def profile(self, create: bool = False) -> dict:
    if any((self.state_root / name).exists() for name in ("profile.json", "pairing_candidate.json", "auth.json")):
      raise TransferError("legacy_configuration")
    path = self.state_root / "sender.json"
    if not path.exists():
      if not create:
        raise TransferError("sender_not_created")
      _atomic_json(path, {"version": 1, "endpoint": self.endpoint, "deviceId": None, "key": secrets.token_urlsafe(32)})
    value = _read_json(path)
    if (value.get("version") != 1 or value.get("endpoint") != self.endpoint or
        not re.fullmatch(r"[A-Za-z0-9_-]{43}", str(value.get("key", ""))) or
        (value.get("deviceId") is not None and not HEX32.fullmatch(str(value["deviceId"])))):
      raise TransferError("invalid_sender_state")
    return value

  def bind_sender(self, device_id: str) -> dict:
    if not HEX32.fullmatch(str(device_id)):
      raise TransferError("invalid_receiver_response")
    with self.lock:
      profile = self.profile()
      if profile["deviceId"] not in {None, device_id}:
        raise TransferError("sender_identity_changed")
      if profile["deviceId"] is None:
        profile["deviceId"] = device_id
        _atomic_json(self.state_root / "sender.json", profile)
      return profile

  def bind_job(self, job_id: str, device_id: str) -> dict:
    with self.lock:
      job = self._load(job_id)
      if job.get("destination") != self.endpoint:
        raise TransferError("job_destination_changed")
      if job["deviceId"] not in {None, device_id}:
        raise TransferError("sender_identity_changed")
      if job["deviceId"] is None:
        job["deviceId"] = device_id
        self._save(job)
      return job

  def _path(self, job_id: str) -> Path:
    if not HEX64.fullmatch(job_id):
      raise TransferError("invalid_job_id", 400)
    return self.jobs_root / f"{job_id}.json"

  def _load(self, job_id: str) -> dict:
    try:
      return _read_json(self._path(job_id))
    except FileNotFoundError:
      raise TransferError("job_not_found", 404) from None

  def _save(self, job: dict) -> None:
    job["updatedAt"] = time.time()
    _atomic_json(self._path(job["id"]), job)

  @staticmethod
  def public(job: dict) -> dict:
    return {**{key: value for key, value in job.items() if key not in {"files", "destination", "transport"}},
            "files": [{**{key: value for key, value in item.items() if key != "identity"},
                       "status": "acknowledged" if item["status"] == "completed" else "pending"}
                      for item in job["files"]]}

  def list_jobs(self) -> dict:
    with self.lock:
      jobs = [_read_json(path) for path in self.jobs_root.glob("*.json")]
      jobs.sort(key=lambda value: value["createdAt"], reverse=True)
      return {"ok": True, "jobs": [self.public(job) for job in jobs]}

  def get_job(self, job_id: str) -> dict:
    with self.lock:
      return self.public(self._load(job_id))

  def _segment_path(self, segment: str) -> Path:
    try:
      safe_segment(segment)
    except web.HTTPException:
      raise TransferError("invalid_segment", 400) from None
    if not COMPONENT.fullmatch(segment) or segment.endswith((".", " ")):
      raise TransferError("invalid_segment", 400)
    path = self.log_root / segment
    try:
      if _is_link(path) or not path.is_dir() or path.resolve().parent != self.log_root:
        raise TransferError("unsafe_source_path")
      if any(item.name.endswith(".lock") for item in path.iterdir()):
        raise TransferError("source_locked")
    except FileNotFoundError:
      raise TransferError("source_missing") from None
    return path

  def _open_snapshot(self, segment: str, expected: dict | None = None, date: bool = False, verify_hash: bool = True):
    directory = self._segment_path(segment)
    candidates = [directory / expected["name"]] if expected else [directory / name for name in RLOG_SOURCE_NAMES]
    path = next((candidate for candidate in candidates if candidate.exists() or candidate.is_symlink()), None)
    if path is None:
      raise TransferError("source_missing")
    if path.name not in RLOG_SOURCE_NAMES or _is_link(path):
      raise TransferError("unsafe_source_path")
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0))
    stream = os.fdopen(descriptor, "rb")
    try:
      before = os.fstat(stream.fileno())
      if not stat.S_ISREG(before.st_mode) or not 0 < before.st_size <= 8 * 1024**3:
        raise TransferError("invalid_source")
      if verify_hash:
        digest = hashlib.sha256()
        while block := stream.read(CHUNK):
          digest.update(block)
      after = os.fstat(stream.fileno())
      identity = {"size": after.st_size, "mtimeNs": after.st_mtime_ns, "inode": after.st_ino, "device": after.st_dev}
      if (before.st_size, before.st_mtime_ns, before.st_ino) != (after.st_size, after.st_mtime_ns, after.st_ino):
        raise TransferError("source_modified")
      sha = digest.hexdigest() if verify_hash else expected["sha256"]
      if expected and (expected["size"] != after.st_size or expected["sha256"] != sha or expected["identity"] != identity):
        raise TransferError("source_modified")
      if any(item.name.endswith(".lock") for item in directory.iterdir()):
        raise TransferError("source_locked")
      observation = _recorded_start(stream, path.name) if date else None
      stream.seek(0)
      return stream, {"segment": segment, "route": route_name(segment), "name": path.name,
                      "size": after.st_size, "sha256": sha, "identity": identity}, observation
    except Exception:
      stream.close()
      raise

  def _latest_segments(self) -> list[str]:
    # Same route ordering/closure semantics as the existing catalog, with an
    # injected root for the standalone HTTP tests.
    from . import catalog
    if self.log_root == Path(catalog.DASHCAM_ROOT).resolve():
      routes = build_routes()
    else:
      grouped: dict[str, list[str]] = {}
      for entry in self.log_root.iterdir():
        if entry.is_dir() and not _is_link(entry):
          try:
            safe_segment(entry.name)
          except web.HTTPException:
            continue
          grouped.setdefault(route_name(entry.name), []).append(entry.name)
      routes = [{"route": name, "segmentFolders": segments} for name, segments in grouped.items()]
    if not routes:
      raise TransferError("no_completed_logs", 400)
    latest = max(routes, key=lambda route: route_creation_key(route["route"]))
    result = []
    for segment in latest["segmentFolders"]:
      try:
        directory = self._segment_path(segment)
        complete = (segment_is_complete(segment) if self.log_root == Path(catalog.DASHCAM_ROOT).resolve() else
                    any((directory / name).is_file() for name in RLOG_SOURCE_NAMES))
        if complete:
          result.append(segment)
      except TransferError as error:
        if error.code != "source_locked":
          raise
    if not result:
      raise TransferError("no_completed_logs", 400)
    return result

  def _snapshot(self, body: dict) -> tuple[str, list, dict, str]:
    if set(body) == {"scope"} and body["scope"] == "latest_route":
      segments, scope = self._latest_segments(), "latest_route"
    elif set(body) == {"segments"} and isinstance(body["segments"], list):
      segments, scope = body["segments"], "selected"
    else:
      raise TransferError("invalid_selection", 400)
    if not segments or len(segments) > MAX_FILES or any(not isinstance(item, str) for item in segments):
      raise TransferError("invalid_selection", 400)
    files, dates = [], {}
    for segment in sorted(set(segments)):
      stream, item, date = self._open_snapshot(segment, date=True)
      stream.close()
      dates.setdefault(item["route"], []).append(date)
      item["id"] = hashlib.sha256(f"{segment}/{item['name']}/{item['sha256']}".encode()).hexdigest()[:32]
      item.update(status="queued", receiptId=None)
      files.append(item)
    if sum(item["size"] for item in files) > 64 * 1024**3:
      raise TransferError("job_too_large", 400)
    routes = {name: _route_date(observations) for name, observations in dates.items()}
    key = json.dumps({"destination": self.endpoint, "routes": routes,
                     "files": [(item["segment"], item["name"], item["size"], item["sha256"]) for item in files]},
                     sort_keys=True, separators=(",", ":"))
    return scope, files, routes, hashlib.sha256(key.encode()).hexdigest()

  def preview(self, selection: dict) -> dict:
    with self.lock:
      _, files, routes, snapshot_id = self._snapshot(selection)
      return {"previewId": snapshot_id, "recipient": RECIPIENT, "fileCount": len(files),
              "totalBytes": sum(item["size"] for item in files),
              "driveDates": sorted({info["driveDate"] if info["dateStatus"] == "known" else "날짜미확인" for info in routes.values()})}

  def enqueue(self, body: dict) -> tuple[dict, bool]:
    if set(body) != {"selection", "previewId", "confirmed"} or body["confirmed"] is not True or not isinstance(body["selection"], dict):
      raise TransferError("confirmation_required", 400)
    with self.lock:
      scope, files, routes, job_id = self._snapshot(body["selection"])
      if body["previewId"] != job_id:
        raise TransferError("selection_changed")
      # Both the private identity and immutable queue snapshot are durable
      # before the worker is allowed to contact the recipient for the first time.
      profile = self.profile(create=True)
      if self._path(job_id).exists():
        return self.public(self._load(job_id)), True
      existing = [_read_json(path) for path in self.jobs_root.glob("*.json")]
      if len(existing) >= MAX_JOBS:
        disposable = sorted((job for job in existing if job["status"] in TERMINAL and not job.get("releasePending")), key=lambda job: job["createdAt"])
        if not disposable:
          raise TransferError("queue_full")
        self._path(disposable[0]["id"]).unlink()
      now = time.time()
      job = {"version": 1, "id": job_id, "deviceId": profile["deviceId"], "destination": self.endpoint, "status": "queued",
             "scope": scope, "createdAt": now, "updatedAt": now, "error": None, "routes": routes, "files": files}
      self._save(job)
      return self.public(job), False

  def pending(self) -> list[dict]:
    with self.lock:
      jobs = [_read_json(path) for path in self.jobs_root.glob("*.json")]
      return sorted((job for job in jobs if job["status"] not in TERMINAL), key=lambda job: job["createdAt"])

  def releases(self) -> list[dict]:
    with self.lock:
      return [job for path in sorted(self.jobs_root.glob("*.json"))
              if (job := _read_json(path)).get("releasePending") and job.get("releaseRetryAt", 0) <= time.time()][:4]

  @staticmethod
  def _terminal(job: dict, status: str, error: str | None) -> None:
    job.update(status=status, error=error, retryRequested=False)
    if job.get("deviceId") is not None:
      job.update(releasePending=True, releaseRetryAt=0)

  def finish_release(self, job_id: str) -> None:
    with self.lock:
      job = self._load(job_id)
      job.update(releasePending=False, releaseRetryAt=0, releaseError=None)
      if job.get("retryRequested"):
        job.update(status="queued", error=None, retryRequested=False, retryAt=0, attempts=0)
      self._save(job)

  def defer_release(self, job_id: str, error: str, retry_after: float = 0) -> None:
    with self.lock:
      job = self._load(job_id)
      attempts = job.get("releaseAttempts", 0) + 1
      delay = max(min(300, 2 ** min(attempts, 8)), min(3600, retry_after))
      job.update(releaseAttempts=attempts, releaseError=error,
                 releaseRetryAt=time.time() + min(3600, delay * random.uniform(1.0, 1.2)))
      self._save(job)

  def defer(self, job_id: str, error: str, *, failed: bool = False, retry_after: float = 0) -> None:
    with self.lock:
      job = self._load(job_id)
      if job["status"] in TERMINAL:
        return
      attempts = job.get("attempts", 0) + 1
      delay = max(min(300, 2 ** min(attempts, 8)), min(3600, retry_after))
      if failed:
        self._terminal(job, "failed", error)
      job.update(status="failed" if failed else "queued", error=error, attempts=attempts,
                 retryAt=time.time() + min(3600, delay * random.uniform(1.0, 1.2)))
      self._save(job)

  def _file(self, job: dict, file_id: str) -> dict:
    if not HEX32.fullmatch(file_id):
      raise TransferError("invalid_file_id", 400)
    item = next((item for item in job["files"] if item["id"] == file_id), None)
    if item is None:
      raise TransferError("file_not_found", 404)
    return item

  def open_file(self, job_id: str, file_id: str):
    with self.lock:
      job = self._load(job_id)
      if job["status"] in {"failed", "canceled"}:
        raise TransferError("job_not_transferable")
      item = self._file(job, file_id)
      try:
        stream, _, _ = self._open_snapshot(item["segment"], item)
      except (OSError, TransferError) as error:
        self._terminal(job, "failed", error.code if isinstance(error, TransferError) else "source_missing")
        self._save(job)
        raise TransferError(job["error"]) from None
      if item["status"] != "completed":
        item["status"] = "transferring"
        job["status"] = "transferring"
        self._save(job)
      return stream, dict(item)

  def finish_stream(self, job_id: str, file_id: str) -> None:
    with self.lock:
      job = self._load(job_id)
      if job["status"] in TERMINAL:
        return
      item = self._file(job, file_id)
      if item["status"] != "completed":
        item["status"] = "verifying"
        job["status"] = "verifying"
        self._save(job)

  def ack(self, job_id: str, body: dict) -> tuple[dict, bool]:
    if set(body) != {"fileId", "size", "sha256", "receiptId"} or type(body.get("size")) is not int:
      raise TransferError("invalid_ack", 400)
    if not isinstance(body["receiptId"], str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", body["receiptId"]):
      raise TransferError("invalid_ack", 400)
    with self.lock:
      job = self._load(job_id)
      if job["status"] in {"failed", "canceled"}:
        raise TransferError("job_not_transferable")
      item = self._file(job, str(body["fileId"]))
      if body["size"] != item["size"] or body["sha256"] != item["sha256"]:
        raise TransferError("ack_digest_mismatch")
      duplicate = item["status"] == "completed"
      if not duplicate:
        try:
          stream, _, _ = self._open_snapshot(item["segment"], item)
          stream.close()
        except (OSError, TransferError) as error:
          self._terminal(job, "failed", error.code if isinstance(error, TransferError) else "source_missing")
          self._save(job)
          raise TransferError(job["error"]) from None
        item.update(status="completed", receiptId=body["receiptId"])
        job["status"] = "completed" if all(item["status"] == "completed" for item in job["files"]) else "queued"
        self._save(job)
      return self.public(job), duplicate

  def retry(self, job_id: str) -> dict:
    with self.lock:
      job = self._load(job_id)
      if job.get("destination") != self.endpoint:
        raise TransferError("job_destination_changed")
      for item in job["files"]:
        if item["status"] != "completed":
          stream, _, _ = self._open_snapshot(item["segment"], item)
          stream.close()
          item["status"] = "queued"
      if job.get("releasePending"):
        job["retryRequested"] = True
        self._save(job)
        return self.public(job)
      job.update(status="completed" if all(item["status"] == "completed" for item in job["files"]) else "queued", error=None, retryAt=0, attempts=0)
      self._save(job)
      return self.public(job)

  def cancel(self, job_id: str) -> dict:
    with self.lock:
      job = self._load(job_id)
      if job["status"] == "completed":
        raise TransferError("already_completed")
      self._terminal(job, "canceled", None)
      self._save(job)
      return self.public(job)

  @staticmethod
  def manifest(job: dict) -> dict:
    value = {name: job[name] for name in ("version", "id", "deviceId", "scope", "createdAt", "routes")}
    value.update(status="queued", updatedAt=job["createdAt"], error=None,
                 files=[{**{name: item[name] for name in ("id", "route", "segment", "name", "size", "sha256")},
                         "status": "pending", "receiptId": None} for item in job["files"]])
    return value

  def _transport(self, job: dict, owner: str, epoch: int) -> None:
    current = job.get("transport", {})
    if (not isinstance(owner, str) or not HEX32.fullmatch(owner) or type(epoch) is not int or
        current.get("owner") != owner or current.get("epoch") != epoch or current.get("until", 0) <= time.time()):
      raise TransferError("transport_stale", 409)

  def claim(self, job_id: str, owner: str) -> dict:
    if not isinstance(owner, str) or not HEX32.fullmatch(owner):
      raise TransferError("invalid_transport", 400)
    with self.lock:
      job = self._load(job_id)
      current = job.get("transport", {})
      now = time.time()
      if current.get("until", 0) > now and current.get("owner") != owner:
        raise TransferError("transport_busy", 409)
      if current.get("until", 0) <= now:
        epoch = current.get("epoch", 0) + 1
        if epoch > 2**53 - 1:
          raise TransferError("transport_epoch_exhausted")
        current = {"owner": owner, "epoch": epoch}
      current["until"] = now + TRANSPORT_LEASE
      job["transport"] = current
      self._save(job)
      profile = self.profile()
      return {"endpoint": self.endpoint, "key": profile["key"], "deviceId": profile["deviceId"],
              "job": self.public(job), "manifest": self.manifest(job), "owner": owner,
              "epoch": current["epoch"], "leaseExpiresAt": current["until"]}

  def transport_action(self, job_id: str, body: dict, action: str) -> dict:
    if not {"owner", "epoch"} <= set(body):
      raise TransferError("invalid_transport", 400)
    with self.lock:
      job = self._load(job_id)
      self._transport(job, body["owner"], body["epoch"])
      fields = {name: value for name, value in body.items() if name not in {"owner", "epoch"}}
      if action in {"heartbeat", "yield", "cancel", "retry", "released"}:
        if fields:
          raise TransferError("unexpected_fields", 400)
        if action == "heartbeat":
          job["transport"]["until"] = time.time() + TRANSPORT_LEASE
          self._save(job)
        elif action == "yield":
          job["transport"]["until"] = 0
          self._save(job)
        elif action == "released":
          self.finish_release(job_id)
        else:
          (self.cancel if action == "cancel" else self.retry)(job_id)
      elif action == "bind":
        if set(fields) != {"deviceId"} or job["status"] in TERMINAL:
          raise TransferError("invalid_sender_binding")
        self.bind_sender(fields["deviceId"])
        self.bind_job(job_id, fields["deviceId"])
      elif action == "ack":
        self.ack(job_id, fields)
      elif action == "defer":
        if set(fields) != {"error", "retryAfter"} or fields["error"] not in TRANSIENT_ERRORS | {"receiver_auth_failed", "receiver_conflict", "invalid_receiver_receipt", "invalid_receiver_response", "receiver_redirect_rejected", "receiver_rejected", "receiver_limit"}:
          raise TransferError("invalid_defer", 400)
        if type(fields["retryAfter"]) not in {int, float} or not 0 <= fields["retryAfter"] <= 3600:
          raise TransferError("invalid_defer", 400)
        if job.get("releasePending"):
          self.defer_release(job_id, fields["error"], fields["retryAfter"])
        else:
          self.defer(job_id, fields["error"], failed=fields["error"] not in TRANSIENT_ERRORS, retry_after=fields["retryAfter"])
      else:
        raise TransferError("unknown_action", 404)
      return self.public(self._load(job_id))

  def chunk(self, job_id: str, body: dict) -> tuple[bytes, dict, int]:
    if set(body) != {"owner", "epoch", "fileId", "offset", "length"} or type(body["offset"]) is not int or type(body["length"]) is not int:
      raise TransferError("invalid_range", 400)
    with self.lock:
      job = self._load(job_id)
      self._transport(job, body["owner"], body["epoch"])
      if job["status"] in TERMINAL:
        raise TransferError("job_not_transferable")
      item = self._file(job, body["fileId"])
      start, length = body["offset"], body["length"]
      if not 0 <= start < item["size"] or not 1 <= length <= CHUNK or start + length > item["size"]:
        raise TransferError("invalid_range", 416)
      try:
        stream, _, _ = self._open_snapshot(item["segment"], item, verify_hash=False)
        try:
          stream.seek(start)
          data = stream.read(length)
          stat_now = os.fstat(stream.fileno())
          identity = {"size": stat_now.st_size, "mtimeNs": stat_now.st_mtime_ns, "inode": stat_now.st_ino, "device": stat_now.st_dev}
          if len(data) != length or identity != item["identity"]:
            raise TransferError("source_modified")
        finally:
          stream.close()
      except (OSError, TransferError) as error:
        self._terminal(job, "failed", error.code if isinstance(error, TransferError) else "source_missing")
        self._save(job)
        raise TransferError(job["error"]) from None
      self._transport(job, body["owner"], body["epoch"])
      job["transport"]["until"] = time.time() + TRANSPORT_LEASE
      job["status"], item["status"] = "transferring", "transferring"
      self._save(job)
      return data, dict(item), start


def register(app: web.Application, store: PcLogStore | None = None) -> PcLogStore:
  store = store or PcLogStore()
  nonce = secrets.token_urlsafe(32)
  app["pc_log_transfer_store"] = store

  async def body(request):
    if request.query or request.headers.get("Origin") != f"{request.scheme}://{request.host}":
      raise TransferError("origin_rejected", 403)
    supplied = request.headers.get("X-Carrot-CSRF", "")
    if not supplied or not hmac.compare_digest(supplied, nonce):
      raise TransferError("csrf_rejected", 403)
    try:
      value = json.loads(await _bounded_read(request.content, 256 * 1024))
    except (ValueError, UnicodeDecodeError):
      raise TransferError("invalid_json", 400) from None
    if not isinstance(value, dict):
      raise TransferError("invalid_json", 400)
    return value

  def handler(function):
    async def wrapped(request):
      try:
        return await function(request)
      except TransferError as error:
        return web.json_response({"ok": False, "error": error.code}, status=error.status, headers={"Cache-Control": "no-store"})
      except (OSError, ValueError, KeyError):
        return web.json_response({"ok": False, "error": "storage_error"}, status=409)
    return wrapped

  async def status(request):
    if request.query:
      raise TransferError("unexpected_query", 400)
    error = None
    try:
      await asyncio.to_thread(store.profile)
    except TransferError as failure:
      if failure.code != "sender_not_created":
        error = failure.code
    return web.json_response({"ok": True, "recipient": RECIPIENT, "error": error, "csrfNonce": nonce,
                              "jobs": (await asyncio.to_thread(store.list_jobs))["jobs"]}, headers={"Cache-Control": "no-store"})

  async def preview(request):
    return web.json_response({"ok": True, **await asyncio.to_thread(store.preview, await body(request))})

  async def enqueue(request):
    job, duplicate = await asyncio.to_thread(store.enqueue, await body(request))
    return web.json_response({"ok": True, "job": job, "duplicate": duplicate})

  async def action(request):
    value = await body(request)
    job_id, operation = request.match_info["job_id"], request.match_info["action"]
    if operation == "claim":
      if set(value) != {"owner"}:
        raise TransferError("invalid_transport", 400)
      return web.json_response({"ok": True, **await asyncio.to_thread(store.claim, job_id, value["owner"])}, headers={"Cache-Control": "no-store"})
    if operation == "chunk":
      data, item, start = await asyncio.to_thread(store.chunk, job_id, value)
      return web.Response(body=data, content_type="application/octet-stream", headers={"Cache-Control": "no-store",
                          "Content-Range": f"bytes {start}-{start + len(data) - 1}/{item['size']}", "X-Content-SHA256": item["sha256"]})
    return web.json_response({"ok": True, "job": await asyncio.to_thread(store.transport_action, job_id, value, operation)})

  app.router.add_get(PREFIX + "/status", handler(status))
  app.router.add_post(PREFIX + "/preview", handler(preview))
  app.router.add_post(PREFIX + "/jobs", handler(enqueue))
  app.router.add_post(PREFIX + "/jobs/{job_id}/{action:claim|chunk|bind|ack|heartbeat|yield|defer|released|cancel|retry}", handler(action))
  return store
