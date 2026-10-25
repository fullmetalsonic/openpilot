"""Local physical-QR grants and origin-bound sessions. HTTP is not encrypted.

The issuer is called only by the native QR UI. Grant metadata is bound into the
secret digest; neither raw grant secrets nor session tokens are persisted.
Windows fixtures exercise file semantics, not POSIX ownership or directory fsync.
"""
from contextlib import contextmanager
import hashlib
import hmac
from ipaddress import ip_address
import json
import os
from pathlib import Path
import re
import secrets
import stat
import threading
import time
from urllib.parse import urlsplit

DEFAULT_ROOT = Path("/tmp/fms_control_advisor/access")
MAX_GRANTS = 256
MAX_FILE_BYTES = 4096
_ID = re.compile(r"[0-9a-f]{32}")
_TOKEN = re.compile(r"[A-Za-z0-9_-]{43}")
_NAME = re.compile(r"([0-9a-f]{32})\.(json|used)")
_SHA = re.compile(r"[0-9a-f]{64}")
_FIELDS = {"schema_version", "origin", "boot_id", "issued_mono_ns", "expires_mono_ns", "secret_sha256"}
_MARKER_FIELDS = (_FIELDS - {"secret_sha256"}) | {"grant_sha256"}
_NS = 1_000_000_000


class AccessDenied(ValueError):
  """A safe reason identifier; errors never include supplied credentials."""


def _origin(value):
  try:
    if type(value) is not str or len(value) > 256:
      raise ValueError
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or parsed.username is not None or parsed.password is not None or parsed.path or parsed.query or parsed.fragment:
      raise ValueError
    address = ip_address(parsed.hostname)
    if address.is_unspecified or address.is_loopback or address.is_multicast or parsed.port is None or not 1 <= parsed.port <= 65535:
      raise ValueError
    host = f"[{address.compressed}]" if address.version == 6 else address.compressed
    canonical = f"{parsed.scheme}://{host}:{parsed.port}"
    if value != canonical:
      raise ValueError
    return canonical
  except (ValueError, TypeError, AttributeError):
    raise AccessDenied("native_access_origin_invalid") from None


def _integer(value, minimum=0, maximum=2**63 - 1):
  if type(value) is not int or not minimum <= value <= maximum:
    raise AccessDenied("native_access_time_invalid")
  return value


def _boot(value):
  if value is None:
    try:
      with open("/proc/sys/kernel/random/boot_id", "r", encoding="ascii") as stream:
        value = stream.read(129).strip()
    except (OSError, UnicodeError):
      raise AccessDenied("native_access_boot_unavailable") from None
  if type(value) is not str or re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value) is None:
    raise AccessDenied("native_access_boot_invalid")
  return value


def _json(value):
  return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _decode(raw):
  def unique(pairs):
    result = {}
    for key, value in pairs:
      if key in result:
        raise AccessDenied("native_access_file_invalid")
      result[key] = value
    return result
  try:
    return json.loads(raw, object_pairs_hook=unique, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
  except (ValueError, UnicodeError, RecursionError):
    raise AccessDenied("native_access_file_invalid") from None


def _metadata(value, *, marker=False):
  fields, hash_key = (_MARKER_FIELDS, "grant_sha256") if marker else (_FIELDS, "secret_sha256")
  if type(value) is not dict or set(value) != fields or type(value["schema_version"]) is not int or value["schema_version"] != 1:
    raise AccessDenied("native_access_file_invalid")
  _origin(value["origin"])
  if type(value["boot_id"]) is not str:
    raise AccessDenied("native_access_file_invalid")
  _boot(value["boot_id"])
  start, end = _integer(value["issued_mono_ns"]), _integer(value["expires_mono_ns"])
  if not 0 < end - start <= 300 * _NS or type(value[hash_key]) is not str or _SHA.fullmatch(value[hash_key]) is None:
    raise AccessDenied("native_access_file_invalid")
  return value


def _secret_digest(secret, value):
  binding = {key: item for key, item in value.items() if key != "secret_sha256"}
  return hashlib.sha256(_json(binding) + b"\0" + secret.encode("ascii")).hexdigest()


def _private(info, *, directory=False):
  expected = stat.S_ISDIR if directory else stat.S_ISREG
  if not expected(info.st_mode) or os.name == "posix" and not directory and info.st_nlink != 1:
    raise AccessDenied("native_access_file_invalid")
  if os.name == "posix" and (info.st_uid != os.geteuid() or info.st_mode & 0o077):
    raise AccessDenied("native_access_ownership_invalid")


def _identity(info):
  return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def _cross_identity(info):
  # Windows path stat reports creation time as ctime, while fd stat reports a
  # different timestamp. Keep full ctime checks within each individual API.
  return _identity(info)[:4] if os.name == "nt" else _identity(info)


class _Directory:
  def __init__(self, path, fd):
    self.path, self.fd = path, fd
    info = os.fstat(fd) if fd is not None else path.lstat()
    self.identity = (info.st_dev, info.st_ino)

  def check(self):
    info = self.path.lstat()
    _private(info, directory=True)
    if (info.st_dev, info.st_ino) != self.identity:
      raise AccessDenied("native_access_root_changed")

  def open(self, name, flags, mode=0o600):
    flags |= getattr(os, "O_BINARY", 0)
    if self.fd is not None:
      return os.open(name, flags | getattr(os, "O_NOFOLLOW", 0), mode, dir_fd=self.fd)
    path = self.path / name
    if path.is_symlink():
      raise AccessDenied("native_access_file_invalid")
    return os.open(path, flags, mode)

  def info(self, name):
    return os.stat(name, dir_fd=self.fd, follow_symlinks=False) if self.fd is not None else (self.path / name).lstat()

  def same(self, name, original):
    current = self.info(name)
    _private(current)
    if _identity(current) != _identity(original):
      raise AccessDenied("native_access_file_changed")
    return current

  def read(self, name):
    self.check()
    original = self.info(name)
    _private(original)
    if original.st_size > MAX_FILE_BYTES:
      raise AccessDenied("native_access_file_limit")
    fd = self.open(name, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0))
    try:
      info = os.fstat(fd)
      _private(info)
      if _cross_identity(info) != _cross_identity(original):
        raise AccessDenied("native_access_file_changed")
      raw = bytearray()
      while len(raw) <= MAX_FILE_BYTES:
        block = os.read(fd, MAX_FILE_BYTES + 1 - len(raw))
        if not block:
          break
        raw.extend(block)
      if len(raw) > MAX_FILE_BYTES:
        raise AccessDenied("native_access_file_limit")
      after_fd = os.fstat(fd)
      if _identity(after_fd) != _identity(info):
        raise AccessDenied("native_access_file_changed")
      after_path = self.same(name, original)
      if _cross_identity(after_fd) != _cross_identity(after_path):
        raise AccessDenied("native_access_file_changed")
      return _decode(raw), hashlib.sha256(raw).hexdigest(), after_path
    finally:
      os.close(fd)

  def create(self, name, value):
    self.check()
    raw = _json(value)
    if len(raw) > MAX_FILE_BYTES:
      raise AccessDenied("native_access_file_limit")
    fd = self.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
    try:
      _private(os.fstat(fd))
      remaining = memoryview(raw)
      while remaining:
        count = os.write(fd, remaining)
        if count <= 0:
          raise OSError("native_access_short_write")
        remaining = remaining[count:]
      os.fsync(fd)
    finally:
      os.close(fd)
    self.sync()

  def sync(self):
    self.check()
    if self.fd is not None:
      os.fsync(self.fd)

  def remove(self, name):
    try:
      _private(self.info(name))
    except FileNotFoundError:
      return
    if self.fd is not None:
      os.unlink(name, dir_fd=self.fd)
    else:
      (self.path / name).unlink()


@contextmanager
def _directory(root, *, create=False):
  path, fd = Path(root), None
  if not path.is_absolute() or ".." in path.parts or len(path.parts) > 32:
    raise AccessDenied("native_access_root_invalid")
  try:
    if os.name == "posix":
      fd = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY)
      for part in path.parts[1:]:
        if create:
          try:
            os.mkdir(part, 0o700, dir_fd=fd)
          except FileExistsError:
            pass
        child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
        os.close(fd)
        fd = child
        info = os.fstat(fd)
        # Ordinary sticky /tmp is allowed; mutable non-sticky parents are not.
        if info.st_mode & 0o022 and not info.st_mode & stat.S_ISVTX:
          raise AccessDenied("native_access_root_invalid")
      _private(os.fstat(fd), directory=True)
    else:
      for candidate in [*reversed(path.parents), path]:
        if candidate.is_symlink():
          raise AccessDenied("native_access_root_invalid")
        if create:
          candidate.mkdir(mode=0o700, exist_ok=True)
      _private(path.lstat(), directory=True)
    directory = _Directory(path, fd)
    directory.check()
    yield directory
  finally:
    if fd is not None:
      os.close(fd)


@contextmanager
def _issue_lock(directory):
  fd = directory.open(".issue.lock", os.O_RDWR | os.O_CREAT)
  try:
    _private(os.fstat(fd))
    if os.name == "posix":
      import fcntl
      fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    else:
      import msvcrt
      if not os.fstat(fd).st_size:
        os.write(fd, b"\0")
      os.lseek(fd, 0, os.SEEK_SET)
      msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
    yield
  finally:
    os.close(fd)


def _clean(directory, now, boot):
  def inventory():
    names = []
    with os.scandir(directory.fd if directory.fd is not None else directory.path) as entries:
      for entry in entries:
        if len(names) >= MAX_GRANTS * 2 + 1:
          raise AccessDenied("native_access_cache_full")
        _private(entry.stat(follow_symlinks=False))
        if entry.name != ".issue.lock" and _NAME.fullmatch(entry.name) is None:
          raise AccessDenied("native_access_cache_invalid")
        names.append(entry.name)
    return names
  names = inventory()
  for name in names:
    if name == ".issue.lock":
      continue
    try:
      value, _, _ = directory.read(name)
    except FileNotFoundError:
      continue
    _metadata(value, marker=name.endswith(".used"))
    if value["boot_id"] != boot or now >= value["expires_mono_ns"]:
      directory.remove(name)
  directory.sync()
  ids = {name.split(".")[0] for name in inventory() if name != ".issue.lock"}
  if len(ids) >= MAX_GRANTS:
    raise AccessDenied("native_access_cache_full")


def issue_local_grant(origin, *, root=DEFAULT_ROOT, ttl_seconds=90, clock_ns=time.monotonic_ns, boot_id=None):
  origin, boot = _origin(origin), _boot(boot_id)
  ttl, now = _integer(ttl_seconds, 1, 300), _integer(clock_ns())
  end = _integer(now + ttl * _NS)
  grant_id, secret = secrets.token_hex(16), secrets.token_urlsafe(32)
  value = {"schema_version": 1, "origin": origin, "boot_id": boot, "issued_mono_ns": now, "expires_mono_ns": end}
  value["secret_sha256"] = _secret_digest(secret, value)
  with _directory(root, create=True) as directory, _issue_lock(directory):
    _clean(directory, now, boot)
    directory.create(grant_id + ".json", value)
  return {"grant_id": grant_id, "secret": secret, "expires_mono_ns": end}


def connection_url(origin, grant):
  origin = _origin(origin)
  if (type(grant) is not dict or set(grant) != {"grant_id", "secret", "expires_mono_ns"}
      or type(grant["grant_id"]) is not str or _ID.fullmatch(grant["grant_id"]) is None
      or type(grant["secret"]) is not str or _TOKEN.fullmatch(grant["secret"]) is None):
    raise AccessDenied("native_access_grant_invalid")
  _integer(grant["expires_mono_ns"])
  return origin + "#ca_grant=" + grant["grant_id"] + "." + grant["secret"]


class AccessSessions:
  ownership_verified = os.name == "posix"

  def __init__(self, root=DEFAULT_ROOT, *, clock_ns=time.monotonic_ns, boot_id=None, session_seconds=28800, max_sessions=16):
    self.root, self.clock_ns, self.boot_id = Path(root), clock_ns, _boot(boot_id)
    self.session_ns = _integer(session_seconds, 1, 28800) * _NS
    self.max_sessions = _integer(max_sessions, 1, 16)
    self._sessions, self._closed = {}, False
    self._lock, self._redeem_lock = threading.Lock(), threading.Lock()

  def _expire(self, now):
    self._sessions = {key: item for key, item in self._sessions.items() if item[1] <= now < item[2]}

  def redeem(self, grant_id, secret, origin):
    origin = _origin(origin)
    if type(grant_id) is not str or _ID.fullmatch(grant_id) is None or type(secret) is not str or _TOKEN.fullmatch(secret) is None:
      raise AccessDenied("native_access_denied")
    with self._redeem_lock:
      now = _integer(self.clock_ns())
      with self._lock:
        self._expire(now)
        if self._closed or len(self._sessions) >= self.max_sessions:
          raise AccessDenied("native_access_denied")
      try:
        with _directory(self.root) as directory:
          value, digest, original = directory.read(grant_id + ".json")
          _metadata(value)
          if (value["origin"] != origin or value["boot_id"] != self.boot_id or not value["issued_mono_ns"] <= now < value["expires_mono_ns"]
              or not hmac.compare_digest(value["secret_sha256"], _secret_digest(secret, value))):
            raise AccessDenied("native_access_denied")
          marker = {key: item for key, item in value.items() if key != "secret_sha256"}
          marker["grant_sha256"] = digest
          directory.create(grant_id + ".used", marker)
          directory.same(grant_id + ".json", original)
          # A consumed source is no longer redeemable even if its used marker
          # is later removed. The private marker retains bounded expiry data.
          directory.remove(grant_id + ".json")
          directory.sync()
          directory.check()
        with self._lock:
          now = _integer(self.clock_ns())
          if self._closed or not value["issued_mono_ns"] <= now < value["expires_mono_ns"]:
            raise AccessDenied("native_access_denied")
          token = secrets.token_urlsafe(32)
          self._sessions[hashlib.sha256(token.encode()).hexdigest()] = (origin, now, _integer(now + self.session_ns))
          return token
      except (OSError, ValueError):
        raise AccessDenied("native_access_denied") from None

  def authorized(self, token, origin):
    try:
      origin = _origin(origin)
      if type(token) is not str or _TOKEN.fullmatch(token) is None:
        return False
      key, now = hashlib.sha256(token.encode()).hexdigest(), _integer(self.clock_ns())
      with self._lock:
        entry = self._sessions.get(key)
        if entry is not None and not entry[1] <= now < entry[2]:
          self._sessions.pop(key, None)
          return False
        return not self._closed and entry is not None and entry[0] == origin
    except (ValueError, TypeError):
      return False

  authorize = authorized

  def revoke(self, token):
    if type(token) is str and _TOKEN.fullmatch(token):
      with self._lock:
        self._sessions.pop(hashlib.sha256(token.encode()).hexdigest(), None)

  def close(self):
    with self._lock:
      self._closed = True
      self._sessions.clear()
