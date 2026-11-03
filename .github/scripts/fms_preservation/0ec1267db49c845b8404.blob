#!/usr/bin/env python3
"""Private, bounded PC receiver for Carrot push v1. No exposure is enabled here."""
import argparse
import copy
import ctypes
from dataclasses import dataclass
import datetime as dt
import errno
import hashlib
import hmac
import ipaddress
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import shutil
import socket
import stat
import subprocess
import sys
import threading
import time
import urllib.parse
import uuid

API = "/api/carrot-pc/v1"
DEFAULT_ENDPOINT = "http://minipc-amd5500u.tail8d9376.ts.net"
MAX_MANIFEST = 8 * 1024 * 1024
MAX_CHUNK = 1024 * 1024
RESERVE = 16 * 1024 * 1024
KST = dt.timezone(dt.timedelta(hours=9))
UNKNOWN_REASONS = {"missing_init_data", "invalid_wall_time", "unreadable_log", "inconsistent_route_start"}
CORS_HEADERS = {"authorization", "content-type", "content-range", "x-carrot-device-id", "x-content-sha256",
                "x-carrot-transport-epoch", "x-carrot-transport-id"}


def browser_origin(value, allow_test_http=False):
  require(isinstance(value, str) and len(value) <= 256 and
          not any(char.isspace() or ord(char) < 32 for char in value), "origin_rejected", 403)
  try:
    parsed = urllib.parse.urlsplit(value)
    port = parsed.port
    host = parsed.hostname
    address = ipaddress.ip_address(host) if host != "localhost" else None
  except ValueError:
    raise ReceiverError("origin_rejected", 403) from None
  private = isinstance(address, ipaddress.IPv4Address) and any(address in network for network in
            [ipaddress.ip_network("10.0.0.0/8"), ipaddress.ip_network("172.16.0.0/12"), ipaddress.ip_network("192.168.0.0/16")])
  local_test = allow_test_http and (host == "localhost" or isinstance(address, ipaddress.IPv4Address) and address.is_loopback)
  require(parsed.scheme in {"http", "https"} and parsed.username is None and parsed.password is None and
          parsed.path == "" and "?" not in value and "#" not in value and port != 0 and
          (private or local_test), "origin_rejected", 403)
  return value


@dataclass(frozen=True)
class Policy:
  max_storage: int = 256 * 1024**3
  max_metadata: int = 64 * 1024 * 1024
  min_free: int = 20 * 1024**3
  max_senders: int = 1000
  max_jobs: int = 10000
  max_active: int = 64
  max_active_per_sender: int = 4
  new_senders_per_day: int = 100
  daily_global: int = 128 * 1024**3
  daily_sender: int = 16 * 1024**3
  requests_per_minute: int = 2400
  sender_requests_per_minute: int = 600
  handlers: int = 4
  read_timeout: float = 10
  request_deadline: float = 30
  lease_seconds: float = 600


class ReceiverError(Exception):
  def __init__(self, code, status=400):
    self.code, self.status = code, status
    super().__init__(code)


def require(condition, code="invalid_input", status=400):
  if not condition:
    raise ReceiverError(code, status)


def hex_id(value, size):
  return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{%d}" % size, value) is not None


def component(value):
  require(isinstance(value, str) and 0 < len(value) <= 180 and
          re.fullmatch(r"[A-Za-z0-9_.-]+", value) is not None and value not in {".", ".."} and not value.endswith("."), "unsafe_path")
  stem = value.split(".")[0].upper()
  require(stem not in {"CON", "PRN", "AUX", "NUL"} and re.fullmatch(r"(?:COM|LPT)[0-9]+", stem) is None, "unsafe_path")
  return value


def checked_path(path, directory=False):
  path = Path(os.path.abspath(path))
  for item in reversed((path, *path.parents)):
    try:
      value = item.lstat()
    except FileNotFoundError:
      continue
    require(not stat.S_ISLNK(value.st_mode) and not getattr(value, "st_file_attributes", 0) & 0x400, "unsafe_path", 409)
    if item != path or directory:
      require(stat.S_ISDIR(value.st_mode), "unsafe_path", 409)
    else:
      require(stat.S_ISREG(value.st_mode), "unsafe_path", 409)
  return path


def private_directory(path):
  path = checked_path(path, True)
  path.mkdir(parents=True, exist_ok=True, mode=0o700)
  return checked_path(path, True)


def free_space(path, count, reserve=RESERVE):
  path = checked_path(path, True)
  while not path.exists():
    path = path.parent
  require(shutil.disk_usage(path).free >= count + reserve, "disk_full", 507)


def tree_bytes(root):
  total = 0
  for directory, directories, files in os.walk(root, followlinks=False):
    checked_path(directory, True)
    for name in directories:
      checked_path(Path(directory) / name, True)
    for name in files:
      total += checked_path(Path(directory) / name).stat().st_size
  return total


def sync_directory(path):
  if os.name != "nt":
    descriptor = os.open(path, os.O_RDONLY)
    try:
      os.fsync(descriptor)
    finally:
      os.close(descriptor)


def json_bytes(value):
  return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def private_json(path, value):
  path = checked_path(path)
  data = json_bytes(value)
  private_directory(path.parent)
  free_space(path.parent, len(data))
  temp = checked_path(path.with_name(path.name + ".tmp-" + uuid.uuid4().hex))
  descriptor = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
  try:
    with os.fdopen(descriptor, "wb") as stream:
      stream.write(data)
      stream.flush()
      os.fsync(stream.fileno())
    checked_path(path)
    os.replace(temp, path)
    os.chmod(path, 0o600)
    sync_directory(path.parent)
  finally:
    if temp.exists():
      temp.unlink()


def strict_json(raw):
  def pairs(items):
    result = {}
    for key, value in items:
      require(key not in result, "duplicate_field")
      result[key] = value
    return result
  try:
    value = json.loads(raw, object_pairs_hook=pairs, parse_constant=lambda _: require(False))
  except (ValueError, UnicodeError, RecursionError):
    raise ReceiverError("invalid_json") from None
  require(isinstance(value, dict), "invalid_json")
  return value


def read_json(path, limit=MAX_MANIFEST):
  path = checked_path(path)
  require(path.stat().st_size <= limit, "invalid_local_state", 409)
  return strict_json(path.read_bytes())


def endpoint_url(value, allow_test_http=False):
  require(isinstance(value, str), "endpoint_unconfigured")
  try:
    parsed = urllib.parse.urlsplit(value)
    port = parsed.port
  except ValueError:
    raise ReceiverError("invalid_endpoint") from None
  common = (parsed.hostname and parsed.username is None and parsed.password is None and
            parsed.path in {"", "/"} and "?" not in value and "#" not in value and
            not any(char.isspace() or ord(char) < 32 for char in value))
  production = value == DEFAULT_ENDPOINT
  test = allow_test_http and parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost", "::1"} and (port is None or 0 < port < 65536)
  require(common and (production or test), "invalid_endpoint")
  return value.rstrip("/")


def date_folder(info):
  require(isinstance(info, dict) and set(info) == {"dateBasis", "timezone", "routeStartWallTimeNanos", "driveDate", "dateStatus", "dateSource", "dateReason"}, "invalid_date")
  require(info["dateBasis"] == "route_start" and info["timezone"] == "Asia/Seoul" and info["dateSource"] == "initData.wallTimeNanos", "invalid_date")
  if info["dateStatus"] == "unknown":
    require(info["driveDate"] is None and info["routeStartWallTimeNanos"] is None and info["dateReason"] in UNKNOWN_REASONS, "invalid_date")
    return "날짜미확인"
  stamp = info["routeStartWallTimeNanos"]
  require(info["dateStatus"] == "known" and info["dateReason"] is None and isinstance(stamp, str) and
          re.fullmatch(r"[0-9]{1,20}", stamp) is not None, "invalid_date")
  nanos = int(stamp)
  require(946684800 * 10**9 <= nanos < 4102444800 * 10**9, "invalid_date")
  day = dt.datetime.fromtimestamp(nanos // 10**9, dt.timezone.utc).astimezone(KST).date().isoformat()
  require(day == info["driveDate"], "invalid_date")
  return day


def manifest(job, owner):
  require(isinstance(job, dict) and hex_id(owner, 32) and hex_id(job.get("id"), 64) and job.get("deviceId") == owner, "invalid_manifest")
  require(set(job) == {"version", "id", "deviceId", "status", "createdAt", "updatedAt", "error", "scope", "routes", "files"} and
          type(job["version"]) is int and job["version"] == 1, "invalid_manifest")
  require(job["status"] in {"queued", "transferring", "verifying", "completed", "failed", "canceled"} and job["scope"] in {"selected", "latest_route"}, "invalid_manifest")
  require(isinstance(job["routes"], dict) and isinstance(job["files"], list) and 0 < len(job["files"]) <= 2000, "manifest_bound", 413)
  require(0 < len(job["routes"]) <= 2000, "manifest_bound", 413)
  for route, info in job["routes"].items():
    component(route)
    date_folder(info)
  ids, paths, total = set(), set(), 0
  for file in job["files"]:
    require(isinstance(file, dict) and set(file) == {"id", "route", "segment", "name", "size", "sha256", "status", "receiptId"}, "invalid_manifest")
    require(hex_id(file["id"], 32) and hex_id(file["sha256"], 64), "invalid_manifest")
    route, segment = component(file["route"]), component(file["segment"])
    require(route in job["routes"] and re.fullmatch(re.escape(route) + r"--[0-9]+", segment) is not None, "invalid_manifest")
    require(file["name"] in {"rlog", "rlog.bz2", "rlog.zst"} and file["status"] in {"pending", "acknowledged"}, "invalid_manifest")
    require(type(file["size"]) is int and 0 < file["size"] <= 8 * 1024**3, "file_bound", 413)
    require(file["receiptId"] is None or (isinstance(file["receiptId"], str) and re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", file["receiptId"])), "invalid_manifest")
    path = (date_folder(job["routes"][route]), owner, route.casefold(), segment.casefold(), file["name"])
    require(file["id"] not in ids and path not in paths, "duplicate_file")
    ids.add(file["id"])
    paths.add(path)
    total += file["size"]
  require(total <= 64 * 1024**3, "job_bound", 413)
  # Sender progress is deliberately excluded from the immutable source snapshot.
  result = copy.deepcopy(job)
  result.pop("updatedAt")
  result.pop("error")
  result.pop("status")
  for file in result["files"]:
    file.pop("status")
    file.pop("receiptId")
  return result


def publish(partial, final):
  checked_path(partial)
  checked_path(final)
  if os.name == "nt":
    os.rename(partial, final)
  elif sys.platform.startswith("linux"):
    libc = ctypes.CDLL(None, use_errno=True)
    rename = getattr(libc, "renameat2", None)
    require(rename is not None, "atomic_publish_unavailable", 409)
    if rename(-100, os.fsencode(partial), -100, os.fsencode(final), 1) != 0:
      number = ctypes.get_errno()
      raise OSError(number, "atomic_publish_failed")
  else:
    raise ReceiverError("atomic_publish_unavailable", 409)
  sync_directory(final.parent)


class Receiver:
  def __init__(self, state_root, output, endpoint=DEFAULT_ENDPOINT, allow_test_http=False, clock=time.time, policy=None):
    self.state_root = private_directory(state_root)
    self.output = private_directory(output)
    require(self.output != self.state_root and not self.state_root.is_relative_to(self.output) and
            not self.output.is_relative_to(self.state_root), "overlapping_state_output")
    self.allow_test_http = allow_test_http
    self.endpoint = endpoint_url(endpoint, allow_test_http)
    self.clock = clock
    self.policy = policy or Policy()
    self.lock = threading.RLock()
    self.metadata_bytes = sum(checked_path(path).stat().st_size for path in self.state_root.rglob("*.json"))
    require(self.metadata_bytes <= self.policy.max_metadata, "metadata_quota", 507)
    self.auth_path = self.state_root / "senders.json"
    self.auth = read_json(self.auth_path) if self.auth_path.exists() else {
      "version": 1, "devices": {}, "paused": False, "day": "", "newSenders": 0, "dailyBytes": 0,
      "senderDaily": {}, "minute": 0, "minuteRequests": 0, "senderMinute": {}}
    require(self.auth.get("version") == 1 and isinstance(self.auth.get("devices"), dict) and type(self.auth.get("paused")) is bool,
            "invalid_local_state", 409)
    require(all(hex_id(key, 32) and hex_id(value, 64) for key, value in self.auth["devices"].items()), "invalid_local_state", 409)
    require(all(type(self.auth.get(key)) is int and self.auth[key] >= 0 for key in ["newSenders", "dailyBytes", "minute", "minuteRequests"]) and
            isinstance(self.auth.get("day"), str) and all(isinstance(self.auth.get(key), dict) and
            all(hex_id(owner, 32) and type(count) is int and count >= 0 for owner, count in self.auth[key].items())
            for key in ["senderDaily", "senderMinute"]), "invalid_local_state", 409)
    self.stored_bytes = tree_bytes(self.output) + tree_bytes(self.state_root)
    self.jobs_root = private_directory(self.state_root / "jobs")
    self.jobs = {}
    # Read namespaced jobs first. Earlier local drafts remain intact during migration.
    for path in [*self.jobs_root.glob("*/*.json"), *self.jobs_root.glob("*.json")]:
      require(hex_id(path.stem, 64), "invalid_local_state", 409)
      value = read_json(path)
      require(isinstance(value.get("manifest"), dict) and value["manifest"].get("id") == path.stem, "invalid_local_state", 409)
      restored = copy.deepcopy(value["manifest"])
      restored.update(status="queued", updatedAt=restored.get("createdAt"), error=None)
      for file in restored.get("files", []):
        file.update(status="pending", receiptId=None)
      require(manifest(restored, restored.get("deviceId")) == value["manifest"], "invalid_local_state", 409)
      require(type(value.get("leaseUntil", 0)) in {int, float} and 0 <= value.get("leaseUntil", 0) < float("inf"), "invalid_local_state", 409)
      value.setdefault("leaseUntil", 0)
      require(type(value.get("transportEpoch", 0)) is int and 0 <= value.get("transportEpoch", 0) <= 2**53 - 1 and
              (value.get("transportEpoch", 0) == 0 or hex_id(value.get("transportId"), 32)), "invalid_local_state", 409)
      owner = restored["deviceId"]
      require(path.parent == self.jobs_root or path.parent.name == owner, "invalid_local_state", 409)
      key = (owner, path.stem)
      if key not in self.jobs:
        self.jobs[key] = value
        if path.parent == self.jobs_root:
          self.save_job(value)
    self._verified = {}
    self.last_status = "수신 프로그램 실행 중 · 비공개 연결 설정 필요/미확인"
    self.tunnel = None

  def persist(self, path, value, control=False):
    path = checked_path(path)
    before = path.stat().st_size if path.exists() else 0
    after = len(json_bytes(value))
    growth = after - before
    require(growth <= 0 or self.stored_bytes + growth <= self.policy.max_storage, "storage_quota", 507)
    require(growth <= 0 or self.metadata_bytes + growth <= self.policy.max_metadata or not path.is_relative_to(self.state_root), "metadata_quota", 507)
    free_space(path.parent, after, RESERVE if control else self.policy.min_free)
    private_json(path, value)
    self.stored_bytes += after - before
    if path.is_relative_to(self.state_root):
      self.metadata_bytes += growth

  def current_counters(self):
    value = copy.deepcopy(self.auth)
    day = dt.datetime.fromtimestamp(self.clock(), KST).date().isoformat()
    if day > value["day"]:
      value.update(day=day, newSenders=0, dailyBytes=0, senderDaily={})
    minute = int(self.clock() // 60)
    if minute > value["minute"]:
      value.update(minute=minute, minuteRequests=0, senderMinute={})
    return value

  def accept_request(self, owner=None):
    with self.lock:
      value = self.current_counters()
      require(value["minuteRequests"] < self.policy.requests_per_minute and
              (owner is None or value["senderMinute"].get(owner, 0) < self.policy.sender_requests_per_minute), "rate_limited", 429)
      value["minuteRequests"] += 1
      if owner:
        value["senderMinute"][owner] = value["senderMinute"].get(owner, 0) + 1
      self.persist(self.auth_path, value)
      self.auth = value

  def set_paused(self, paused):
    with self.lock:
      value = dict(self.auth, paused=bool(paused))
      self.auth = value
      # A local stop stays effective even when a full disk prevents saving it.
      self.persist(self.auth_path, value, control=True)

  def receiving(self):
    require(not self.auth["paused"], "receiver_paused", 503)

  def register_sender(self, body, authorization):
    require(set(body) == {"version"} and type(body["version"]) is int and body["version"] == 1, "invalid_registration")
    require(isinstance(authorization, str) and re.fullmatch(r"Bearer [A-Za-z0-9_-]{43}", authorization) is not None, "unauthorized", 401)
    digest = hashlib.sha256(authorization[7:].encode()).hexdigest()
    with self.lock:
      self.receiving()
      self.accept_request()
      for owner, existing in self.auth["devices"].items():
        if hmac.compare_digest(existing, digest):
          return {"ok": True, "version": 1, "endpoint": self.endpoint, "deviceId": owner}
      value = self.current_counters()
      require(len(value["devices"]) < self.policy.max_senders, "sender_quota", 429)
      require(value["newSenders"] < self.policy.new_senders_per_day, "registration_rate_limited", 429)
      owner = uuid.uuid4().hex
      value["devices"][owner] = digest
      value["newSenders"] += 1
      self.persist(self.auth_path, value)
      self.auth = value
      return {"ok": True, "version": 1, "endpoint": self.endpoint, "deviceId": owner}

  def authorize(self, device, authorization):
    require(hex_id(device, 32) and isinstance(authorization, str) and authorization.startswith("Bearer "), "unauthorized", 401)
    key = authorization[7:]
    require(re.fullmatch(r"[!-~]{32,4096}", key) is not None, "unauthorized", 401)
    with self.lock:
      expected = self.auth["devices"].get(device, "0" * 64)
      matched = hmac.compare_digest(hashlib.sha256(key.encode()).hexdigest(), expected)
      require(device in self.auth["devices"] and matched, "unauthorized", 401)
    return device

  def save_job(self, value):
    source = value["manifest"]
    self.persist(self.jobs_root / source["deviceId"] / (source["id"] + ".json"), value)

  def acquire_lease(self, owner, value):
    now = self.clock()
    if value.get("leaseUntil", 0) <= now:
      active = [device for (device, _), existing in self.jobs.items() if existing.get("leaseUntil", 0) > now]
      require(len(active) < self.policy.max_active and active.count(owner) < self.policy.max_active_per_sender, "active_job_bound", 429)
    value["leaseUntil"] = now + self.policy.lease_seconds

  def check_fence(self, value, epoch, transport_id, takeover=False):
    # Direct stdlib fixture callers may omit fencing; every HTTP job route requires it.
    if epoch is None and transport_id is None:
      return
    require(type(epoch) is int and 0 < epoch <= 2**53 - 1 and hex_id(transport_id, 32), "invalid_transport")
    current = value.get("transportEpoch", 0)
    require(epoch >= current, "stale_transport", 409)
    if epoch == current:
      require(value.get("transportId") == transport_id, "transport_conflict", 409)
    else:
      require(takeover, "transport_not_owner", 409)

  def set_fence(self, value, epoch, transport_id):
    self.check_fence(value, epoch, transport_id, takeover=True)
    if epoch is not None:
      value.update(transportEpoch=epoch, transportId=transport_id)

  def create_job(self, owner, body, epoch=None, transport_id=None):
    require(set(body) == {"version", "job"} and type(body["version"]) is int and body["version"] == 1, "invalid_manifest")
    source = manifest(body["job"], owner)
    require(len(json_bytes(body)) <= MAX_MANIFEST, "manifest_bound", 413)
    with self.lock:
      self.receiving()
      key = (owner, source["id"])
      existing = self.jobs.get(key)
      if existing:
        require(existing["manifest"]["deviceId"] == owner and existing["manifest"] == source, "job_conflict", 409)
        self.check_fence(existing, epoch, transport_id, takeover=True)
        result = self.snapshot(owner, source["id"])
        updated = copy.deepcopy(existing)
        self.set_fence(updated, epoch, transport_id)
        if result["status"] != "completed":
          self.acquire_lease(owner, updated)
        self.save_job(updated)
        self.jobs[key] = updated
        return {"ok": True, "job": result, "duplicate": True}
      require(len(self.jobs) < self.policy.max_jobs, "job_quota", 429)
      value = {"manifest": source, "error": None, "leaseUntil": 0}
      self.set_fence(value, epoch, transport_id)
      self.acquire_lease(owner, value)
      self.save_job(value)
      self.jobs[key] = value
      self.last_status = "전송 대기 중"
      return {"ok": True, "job": self.snapshot(owner, source["id"]), "duplicate": False}

  def release(self, owner, job_id, body, epoch=None, transport_id=None):
    require(hex_id(job_id, 64) and set(body) == {"version"} and type(body["version"]) is int and body["version"] == 1, "invalid_release")
    with self.lock:
      value = self.jobs.get((owner, job_id))
      self.check_fence(value or {}, epoch, transport_id, takeover=value is None)
      if value is not None and value.get("leaseUntil", 0):
        updated = dict(value, leaseUntil=0)
        self.save_job(updated)
        self.jobs[(owner, job_id)] = updated
      return {"ok": True, "released": True}

  def owned_job(self, owner, job_id):
    require(hex_id(job_id, 64), "invalid_job")
    value = self.jobs.get((owner, job_id))
    require(value is not None and value["manifest"]["deviceId"] == owner, "unknown_job", 404)
    return value

  def paths(self, source, file):
    relative = Path(date_folder(source["routes"][file["route"]]), source["deviceId"], file["route"], file["segment"], file["name"])
    final = checked_path(self.output / relative)
    require(final.is_relative_to(self.output), "unsafe_path", 409)
    return final, checked_path(final.with_name(final.name + ".partial")), checked_path(final.with_name(final.name + ".receipt.json")), relative

  def verify(self, path, file):
    path = checked_path(path)
    value = path.stat()
    signature = (value.st_size, value.st_mtime_ns, value.st_ino, file["sha256"])
    require(value.st_size == file["size"], "existing_file_conflict", 409)
    if self._verified.get(str(path)) != signature:
      digest = hashlib.sha256()
      with path.open("rb") as stream:
        for block in iter(lambda: stream.read(MAX_CHUNK), b""):
          digest.update(block)
      require(hmac.compare_digest(digest.hexdigest(), file["sha256"]), "hash_mismatch", 409)
      self._verified[str(path)] = signature

  def file_state(self, source, file):
    final, partial, receipt_path, relative = self.paths(source, file)
    expected = {"version": 1, "deviceId": source["deviceId"], "fileId": file["id"], "size": file["size"],
                "sha256": file["sha256"], "relativePath": relative.as_posix(), "dateInfo": source["routes"][file["route"]]}
    receipt = read_json(receipt_path, 16384) if receipt_path.exists() else None
    if receipt is not None:
      require(set(receipt) == set(expected) | {"receiptId", "receivedAt"} and
              all(receipt.get(key) == value for key, value in expected.items()) and hex_id(receipt.get("receiptId"), 32) and
              isinstance(receipt.get("receivedAt"), str) and final.exists(), "receipt_conflict", 409)
    if final.exists():
      self.verify(final, file)
      if receipt is None:
        with final.open("rb+") as stream:
          os.fsync(stream.fileno())
        receipt = dict(expected, receiptId=uuid.uuid4().hex, receivedAt=dt.datetime.now(dt.timezone.utc).isoformat())
        self.persist(receipt_path, receipt)
      return {"id": file["id"], "size": file["size"], "sha256": file["sha256"], "status": "acknowledged", "receivedBytes": file["size"], "receiptId": receipt["receiptId"]}
    count = partial.stat().st_size if partial.exists() else 0
    require(count <= file["size"], "partial_size_conflict", 409)
    if count == file["size"]:
      try:
        self.verify(partial, file)
      except ReceiverError:
        partial.rename(checked_path(partial.with_name(partial.name + ".invalid-" + uuid.uuid4().hex)))
        raise
      publish(partial, final)
      return self.file_state(source, file)
    return {"id": file["id"], "size": file["size"], "sha256": file["sha256"], "status": "pending", "receivedBytes": count, "receiptId": None}

  def snapshot(self, owner, job_id, epoch=None, transport_id=None):
    with self.lock:
      value = self.owned_job(owner, job_id)
      self.check_fence(value, epoch, transport_id)
      files, error = [], value["error"]
      for file in value["manifest"]["files"]:
        try:
          files.append(self.file_state(value["manifest"], file))
        except ReceiverError as failure:
          error = failure.code
          if value["error"] != error:
            value["error"] = error
            self.save_job(value)
          files.append({"id": file["id"], "size": file["size"], "sha256": file["sha256"], "status": "pending", "receivedBytes": 0, "receiptId": None})
      complete = all(file["status"] == "acknowledged" for file in files)
      if complete and value.get("leaseUntil", 0):
        value["leaseUntil"] = 0
        self.save_job(value)
      status = "completed" if complete else "failed" if error else "transferring" if any(file["receivedBytes"] for file in files) else "queued"
      return {"id": job_id, "deviceId": owner, "status": status, "files": files, "error": None if complete else error}

  def put_chunk(self, owner, job_id, file_id, start, end, total, digest, data, epoch=None, transport_id=None):
    require(hex_id(file_id, 32), "invalid_file")
    require(type(start) is int and 0 <= start <= end < total and len(data) == end - start + 1, "invalid_range")
    require(len(data) <= MAX_CHUNK, "chunk_bound", 413)
    with self.lock:
      self.receiving()
      value = self.owned_job(owner, job_id)
      self.check_fence(value, epoch, transport_id)
      require(value.get("leaseUntil", 0) > self.clock(), "lease_expired", 409)
      file = next((item for item in value["manifest"]["files"] if item["id"] == file_id), None)
      require(file is not None, "unknown_file", 404)
      require(total == file["size"] and digest == file["sha256"], "manifest_conflict", 409)
      current = self.file_state(value["manifest"], file)
      if current["status"] == "acknowledged":
        return {"ok": True, "job": self.snapshot(owner, job_id)}
      require(start == current["receivedBytes"], "offset_conflict", 409)
      final, partial, _, _ = self.paths(value["manifest"], file)
      require(self.stored_bytes + len(data) + 16384 <= self.policy.max_storage, "storage_quota", 507)
      free_space(final.parent, len(data) + 16384, self.policy.min_free)
      counters = self.current_counters()
      require(counters["dailyBytes"] + len(data) <= self.policy.daily_global and
              counters["senderDaily"].get(owner, 0) + len(data) <= self.policy.daily_sender, "daily_quota", 429)
      counters["dailyBytes"] += len(data)
      counters["senderDaily"][owner] = counters["senderDaily"].get(owner, 0) + len(data)
      self.persist(self.auth_path, counters)  # Charge durably before data: a crash cannot reset daily limits.
      self.auth = counters
      private_directory(final.parent)
      flags = os.O_WRONLY | (os.O_APPEND if partial.exists() else os.O_CREAT | os.O_EXCL) | getattr(os, "O_NOFOLLOW", 0)
      descriptor = os.open(partial, flags, 0o600)
      with os.fdopen(descriptor, "ab") as stream:
        require(stat.S_ISREG(os.fstat(stream.fileno()).st_mode) and os.fstat(stream.fileno()).st_size == start, "offset_conflict", 409)
        try:
          stream.write(data)
          stream.flush()
          os.fsync(stream.fileno())
        finally:
          self.stored_bytes += os.fstat(stream.fileno()).st_size - start
      if end + 1 == total:
        self.last_status = "전체 파일 검증 중"
        try:
          self.verify(partial, file)
        except ReceiverError:
          partial.rename(checked_path(partial.with_name(partial.name + ".invalid-" + uuid.uuid4().hex)))
          value["error"] = "hash_mismatch"
          self.save_job(value)
          self.last_status = "파일 검증 실패 · 재전송 필요"
          raise
        publish(partial, final)
      value["error"] = None
      value["leaseUntil"] = self.clock() + self.policy.lease_seconds
      self.save_job(value)
      result = {"ok": True, "job": self.snapshot(owner, job_id)}
      self.last_status = "검증 완료 · 로그 저장됨" if result["job"]["status"] == "completed" else "로그 수신 중"
      return result


class LimitedServer(ThreadingHTTPServer):
  daemon_threads = True
  block_on_close = True

  def __init__(self, address, handler, receiver):
    self.receiver = receiver
    self.slots = threading.BoundedSemaphore(receiver.policy.handlers)
    super().__init__(address, handler)

  def process_request(self, request, address):
    if not self.slots.acquire(blocking=False):
      data = b'{"ok":false,"error":"handler_bound"}'
      try:
        request.settimeout(1)
        request.sendall(b"HTTP/1.0 503 Service Unavailable\r\nRetry-After: 5\r\nContent-Type: application/json\r\nContent-Length: " + str(len(data)).encode() + b"\r\n\r\n" + data)
      except OSError:
        pass
      self.shutdown_request(request)
      return
    try:
      super().process_request(request, address)
    except Exception:
      self.slots.release()
      raise

  def process_request_thread(self, request, address):
    try:
      super().process_request_thread(request, address)
    finally:
      self.slots.release()

  def handle_error(self, request, address):
    pass  # Never print request headers, paths or bodies from server exceptions.


class Handler(BaseHTTPRequestHandler):
  server_version = "CarrotPC/1"

  def setup(self):
    self.request.settimeout(self.server.receiver.policy.read_timeout)
    super().setup()
    self.deadline = time.monotonic() + self.server.receiver.policy.request_deadline
    self.read_timer = threading.Timer(self.server.receiver.policy.request_deadline, self.expire_read)
    self.read_timer.daemon = True
    self.read_timer.start()

  def expire_read(self):
    try:
      self.request.shutdown(socket.SHUT_RDWR)
    except OSError:
      pass

  def finish(self):
    self.read_timer.cancel()
    super().finish()

  def log_message(self, *args):
    pass

  def send_json(self, status, value):
    data = json_bytes(value)
    self.send_response(status)
    self.send_header("Content-Type", "application/json")
    self.send_header("Content-Length", str(len(data)))
    self.send_header("Cache-Control", "no-store")
    self.send_cors_headers()
    if status in {429, 503}:
      self.send_header("Retry-After", "60" if status == 429 else "5")
    self.end_headers()
    self.wfile.write(data)

  def send_cors_headers(self):
    self.send_header("Vary", "Origin")
    if getattr(self, "cors_origin", None):
      self.send_header("Access-Control-Allow-Origin", self.cors_origin)
      self.send_header("Access-Control-Expose-Headers", "Retry-After")

  def transport_fence(self):
    raw = self.headers.get("X-Carrot-Transport-Epoch", "")
    transport_id = self.headers.get("X-Carrot-Transport-Id")
    require(len(self.headers.get_all("X-Carrot-Transport-Epoch", [])) == 1 and
            len(self.headers.get_all("X-Carrot-Transport-Id", [])) == 1 and
            re.fullmatch(r"[1-9][0-9]{0,15}", raw) is not None and int(raw) <= 2**53 - 1 and
            hex_id(transport_id, 32), "invalid_transport")
    return int(raw), transport_id

  def send_error(self, code, message=None, explain=None):
    self.send_json(code, {"ok": False, "error": "invalid_http"})

  def read_body(self, limit):
    require(self.headers.get("Transfer-Encoding") is None and len(self.headers.get_all("Content-Length", [])) == 1, "invalid_length")
    size = self.headers.get("Content-Length", "")
    require(re.fullmatch(r"[0-9]{1,12}", size) is not None, "invalid_length")
    count = int(size)
    require(count <= limit, "request_bound", 413)
    data = bytearray()
    while len(data) < count:
      remaining = self.deadline - time.monotonic()
      require(remaining > 0, "request_timeout", 503)
      self.request.settimeout(min(remaining, self.server.receiver.policy.read_timeout))
      block = self.rfile.read1(min(count - len(data), 64 * 1024))
      require(block, "incomplete_request")
      data.extend(block)
    self.read_timer.cancel()
    return bytes(data)

  def dispatch(self):
    try:
      receiver = self.server.receiver
      path = self.path
      self.cors_origin = None
      if self.headers.get("Origin") is not None:
        require(len(self.headers.get_all("Origin", [])) == 1, "origin_rejected", 403)
        self.cors_origin = browser_origin(self.headers["Origin"], receiver.allow_test_http)
      if self.command == "OPTIONS":
        require(self.cors_origin is not None, "origin_rejected", 403)
        require(self.headers.get("Access-Control-Request-Method") in {"POST", "GET", "PUT"}, "cors_method_rejected", 403)
        requested = self.headers.get("Access-Control-Request-Headers", "")
        require(not requested or all(item.strip().lower() in CORS_HEADERS for item in requested.split(",")), "cors_headers_rejected", 403)
        require(re.fullmatch(re.escape(API) + r"/(?:senders|jobs(?:/[0-9a-f]{64}(?:/release|/files/[0-9a-f]{32})?)?)", path) is not None, "unknown_endpoint", 404)
        self.read_timer.cancel()
        self.send_response(204)
        self.send_cors_headers()
        self.send_header("Access-Control-Allow-Methods", "POST, GET, PUT")
        self.send_header("Access-Control-Allow-Headers", ", ".join(sorted(CORS_HEADERS)))
        self.send_header("Access-Control-Max-Age", "300")
        self.send_header("Content-Length", "0")
        self.end_headers()
        return
      if self.command == "POST" and path == API + "/senders":
        require(self.headers.get("Content-Type") == "application/json", "invalid_content_type")
        require(self.headers.get("X-Carrot-Device-Id") is None, "invalid_registration")
        result = receiver.register_sender(strict_json(self.read_body(16384)), self.headers.get("Authorization"))
      else:
        owner = receiver.authorize(self.headers.get("X-Carrot-Device-Id"), self.headers.get("Authorization"))
        epoch, transport_id = self.transport_fence()
        receiver.accept_request(owner)
        if self.command == "POST" and path == API + "/jobs":
          require(self.headers.get("Content-Type") == "application/json", "invalid_content_type")
          result = receiver.create_job(owner, strict_json(self.read_body(MAX_MANIFEST)), epoch, transport_id)
        elif self.command == "POST" and (match := re.fullmatch(re.escape(API) + r"/jobs/([0-9a-f]{64})/release", path)):
          require(self.headers.get("Content-Type") == "application/json", "invalid_content_type")
          result = receiver.release(owner, match[1], strict_json(self.read_body(16384)), epoch, transport_id)
        elif self.command == "GET" and (match := re.fullmatch(re.escape(API) + r"/jobs/([0-9a-f]{64})", path)):
          self.read_timer.cancel()
          result = {"ok": True, "job": receiver.snapshot(owner, match[1], epoch, transport_id)}
        elif self.command == "PUT" and (match := re.fullmatch(re.escape(API) + r"/jobs/([0-9a-f]{64})/files/([0-9a-f]{32})", path)):
          require(self.headers.get("Content-Type") == "application/octet-stream", "invalid_content_type")
          span = re.fullmatch(r"bytes ([0-9]{1,12})-([0-9]{1,12})/([0-9]{1,12})", self.headers.get("Content-Range", ""))
          require(span is not None, "invalid_range")
          data = self.read_body(MAX_CHUNK)
          result = receiver.put_chunk(owner, match[1], match[2], *map(int, span.groups()), self.headers.get("X-Content-SHA256"), data,
                                      epoch, transport_id)
        else:
          raise ReceiverError("unknown_endpoint", 404)
      self.send_json(200, result)
    except ReceiverError as error:
      self.send_json(error.status, {"ok": False, "error": error.code})
    except TimeoutError:
      self.send_json(503, {"ok": False, "error": "request_timeout"})
    except OSError as error:
      self.send_json(507 if error.errno in {errno.ENOSPC, errno.EDQUOT} else 409, {"ok": False, "error": "disk_full" if error.errno in {errno.ENOSPC, errno.EDQUOT} else "storage_unavailable"})
    except (ValueError, TypeError, KeyError, OverflowError, RecursionError):
      self.send_json(400, {"ok": False, "error": "invalid_input"})

  do_GET = dispatch
  do_POST = dispatch
  do_PUT = dispatch
  do_OPTIONS = dispatch


def make_server(receiver, host="127.0.0.1", port=8766):
  require(host in {"127.0.0.1", "localhost"}, "loopback_listener_only")
  return LimitedServer((host, port), Handler, receiver)


def tailscale_executable():
  executable = shutil.which("tailscale")
  if not executable:
    candidate = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Tailscale" / "tailscale.exe"
    executable = str(candidate) if candidate.is_file() else None
  return executable


class PrivateTunnel:
  """An explicit opt-in foreground child; never changes persistent Serve settings."""
  def __init__(self, receiver, port, executable=None, run=None, popen=None):
    self.receiver, self.port = receiver, port
    self.executable = executable or tailscale_executable()
    self.run, self.popen, self.child = run or subprocess.run, popen or subprocess.Popen, None
    self.failed = False
    self.failure_status = "비공개 수신 연결 실패 · 폰에서 수신 불가"
    self.ready = False
    self.checked_at = 0.0

  def query(self, arguments):
    result = self.run([self.executable, *arguments], capture_output=True, timeout=5, check=False,
                      creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    require(result.returncode == 0 and len(result.stdout) <= MAX_MANIFEST, "private_connection_unavailable", 503)
    return strict_json(result.stdout)

  def start(self):
    require(self.executable and self.receiver.endpoint == DEFAULT_ENDPOINT and 0 < self.port < 65536,
            "private_connection_unavailable", 503)
    status = self.query(["status", "--json"])
    require(status.get("Self", {}).get("DNSName", "").rstrip(".").lower() == urllib.parse.urlsplit(DEFAULT_ENDPOINT).hostname,
            "private_host_mismatch", 503)
    config = self.query(["serve", "status", "--json"])
    def configured(value):
      return any(configured(item) for item in value.values()) if isinstance(value, dict) else bool(value)
    require(not configured(config), "existing_serve_configuration", 409)
    self.child = self.popen([self.executable, "serve", "--yes", "--http=80", "http://127.0.0.1:" + str(self.port)], stdin=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    require(self.child.poll() is None, "private_connection_unavailable", 503)
    self.receiver.last_status = "PC 비공개 HTTP 연결 준비 중"

  def status(self):
    if self.failed or self.child is None or self.child.poll() is not None:
      return self.failure_status
    if time.monotonic() - self.checked_at >= 2:
      self.checked_at = time.monotonic()
      try:
        config = self.query(["serve", "status", "--json"])
        host = urllib.parse.urlsplit(self.receiver.endpoint).hostname + ":80"
        entries = [config, *config.get("Foreground", {}).values()]
        def own_proxy(entry):
          proxy = entry.get("Web", {}).get(host, {}).get("Handlers", {}).get("/", {}).get("Proxy")
          return (entry.get("TCP", {}).get("80", {}).get("HTTP") is True and isinstance(proxy, str) and
                  proxy.rstrip("/") == "http://127.0.0.1:" + str(self.port))
        self.ready = (not any(any(entry.get("AllowFunnel", {}).values()) for entry in entries) and
                      any(own_proxy(entry) for entry in entries))
      except (ReceiverError, OSError, ValueError, TypeError, AttributeError, subprocess.TimeoutExpired):
        self.ready = False
    if not self.ready:
      return "PC 비공개 HTTP 연결 준비 중"
    if self.receiver.last_status == "PC 비공개 HTTP 연결 준비 중":
      self.receiver.last_status = "비공개 HTTP 구성 확인 · 폰 Tailscale 연결 미확인"
    return None

  def close(self):
    if self.child is not None and self.child.poll() is None:
      self.child.terminate()
      try:
        self.child.wait(timeout=3)
      except subprocess.TimeoutExpired:
        self.child.kill()
        self.child.wait(timeout=3)


def desktop(receiver):
  import tkinter as tk
  from tkinter import messagebox
  try:
    window = tk.Tk()
  except tk.TclError:
    raise RuntimeError("desktop_unavailable") from None
  window.title("Carrot PC 로그 수집기")
  window.geometry("580x300")
  window.resizable(False, False)
  status = tk.StringVar(value=receiver.last_status)
  tk.Label(window, text="PC 로그 수신", font=("맑은 고딕", 15, "bold")).pack(pady=(16, 10))
  tk.Label(window, textvariable=status, wraplength=470).pack(pady=5)
  tk.Label(window, text=receiver.endpoint, wraplength=550).pack(pady=5)
  devices = tk.StringVar()
  tk.Label(window, textvariable=devices, wraplength=470).pack(pady=5)
  usage = tk.StringVar()
  tk.Label(window, textvariable=usage, wraplength=550).pack(pady=5)

  def pause():
    try:
      receiver.set_paused(not receiver.auth["paused"])
    except (ReceiverError, OSError):
      messagebox.showerror("설정 저장 실패", "수신 상태를 저장할 수 없습니다. 로컬 저장 공간을 확인하세요.")

  def open_folder():
    if os.name == "nt":
      os.startfile(receiver.output)
    else:
      messagebox.showinfo("로그 폴더", str(receiver.output))

  buttons = tk.Frame(window)
  buttons.pack(pady=12)
  pause_button = tk.Button(buttons, command=pause, width=18)
  pause_button.pack(side="left", padx=6)
  tk.Button(buttons, text="로그 폴더 열기", command=open_folder, width=18).pack(side="left", padx=6)

  def refresh():
    tunnel_error = receiver.tunnel.status() if receiver.tunnel else None
    status.set("수신 일시중지" if receiver.auth["paused"] else tunnel_error or receiver.last_status)
    pause_button.config(text="수신 재개" if receiver.auth["paused"] else "수신 일시중지")
    devices.set("전송 기기: %d / %d대" % (len(receiver.auth["devices"]), receiver.policy.max_senders))
    counters = receiver.current_counters()
    usage.set("누적 %.2f / %.0f GiB · 오늘 %.2f / %.0f GiB" %
              (receiver.stored_bytes / 1024**3, receiver.policy.max_storage / 1024**3,
               counters["dailyBytes"] / 1024**3, receiver.policy.daily_global / 1024**3))
    window.after(1000, refresh)
  refresh()
  window.mainloop()


def main(argv=None):
  for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
      stream.reconfigure(encoding="utf-8")
  parser = argparse.ArgumentParser(description="Carrot PC 수신기 · 고정 목적지 · 자동실행 설정 없음")
  base = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / ".config"))) / "CarrotPcLogs"
  parser.add_argument("--output", type=Path, default=Path.cwd() / "로그기록")
  parser.add_argument("--state", type=Path, default=base / "receiver")
  parser.add_argument("--port", type=int, default=8766)
  parser.add_argument("--console", action="store_true", help="창 대신 명시적 콘솔 동작")
  parser.add_argument("--tailscale", action="store_true", help="비공개 수신: 설치된 Tailscale Serve foreground 자식 관리")
  args = parser.parse_args(argv)
  server = None
  tunnel = None
  try:
    receiver = Receiver(args.state, args.output)
    server = make_server(receiver, port=args.port)
    if args.tailscale:
      tunnel = PrivateTunnel(receiver, server.server_port)
      receiver.tunnel = tunnel
      try:
        tunnel.start()
      except (ReceiverError, OSError, ValueError, subprocess.TimeoutExpired) as error:
        tunnel.failed = True
        if isinstance(error, ReceiverError):
          tunnel.failure_status = {
            "existing_serve_configuration": "기존 Serve 설정 충돌 · 설정 변경 없이 수신 중단",
            "private_host_mismatch": "고정 수신 주소와 PC 이름 불일치 · 수신 불가",
          }.get(error.code, tunnel.failure_status)
        receiver.last_status = tunnel.failure_status
        server.server_close()
        server = None
    if server:
      thread = threading.Thread(target=server.serve_forever, daemon=True)
      thread.start()
    if not args.console:
      try:
        desktop(receiver)
        return 0
      except (ImportError, RuntimeError):
        pass
    print(receiver.last_status)
    while True:
      action = input("수신 일시중지/재개: p / 상태: s / 종료: q > ").strip().lower()
      if action == "q":
        return 0
      if action == "p":
        receiver.set_paused(not receiver.auth["paused"])
      elif action == "s":
        print("수신 일시중지" if receiver.auth["paused"] else (tunnel.status() if tunnel else None) or receiver.last_status)
  except (ReceiverError, OSError):
    print("수신기를 시작할 수 없습니다 · 중복 실행 또는 로컬 설정을 확인하세요", file=sys.stderr)
    return 1
  except (KeyboardInterrupt, EOFError):
    return 0
  finally:
    if tunnel:
      tunnel.close()
    if server:
      server.shutdown()
      server.server_close()


if __name__ == "__main__":
  raise SystemExit(main())
