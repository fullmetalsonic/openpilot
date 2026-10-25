"""Real temporary-file grants. Windows is not POSIX ownership validation."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import secrets
import tempfile
import threading
import unittest
from unittest.mock import patch

from openpilot.selfdrive.carrot.control_advisor import native_access as access

ORIGIN = "http://192.168.0.2:7000"
BOOT = "native-fixture-boot"


class Clock:
  def __init__(self): self.now = 1_000_000_000
  def __call__(self): return self.now


class NativeAccessTests(unittest.TestCase):
  def setUp(self):
    self.temp = tempfile.TemporaryDirectory()
    self.addCleanup(self.temp.cleanup)
    self.root, self.clock = Path(self.temp.name) / "private" / "access", Clock()

  def grant(self, origin=ORIGIN, **options):
    return access.issue_local_grant(origin, root=self.root, clock_ns=self.clock, boot_id=BOOT, **options)

  def sessions(self, **options):
    value = access.AccessSessions(self.root, clock_ns=self.clock, boot_id=options.pop("boot_id", BOOT), **options)
    self.addCleanup(value.close)
    return value

  def redeem(self, sessions, grant, origin=ORIGIN):
    return sessions.redeem(grant["grant_id"], grant["secret"], origin)

  def test_actual_file_fragment_and_memory_do_not_persist_credentials(self):
    grant = self.grant()
    url = access.connection_url(ORIGIN, grant)
    self.assertEqual(url, ORIGIN + "#ca_grant=" + grant["grant_id"] + "." + grant["secret"])
    self.assertNotIn("?", url)
    files = list(self.root.iterdir())
    self.assertTrue(all(grant["secret"].encode() not in path.read_bytes() for path in files))
    if os.name == "posix":
      self.assertEqual(0o700, self.root.stat().st_mode & 0o777)
      self.assertTrue(all(path.stat().st_mode & 0o777 == 0o600 for path in files))
    sessions = self.sessions()
    token = self.redeem(sessions, grant)
    self.assertTrue(sessions.authorized(token, ORIGIN))
    self.assertTrue(sessions.authorize(token, ORIGIN))
    self.assertNotIn(token, repr(sessions.__dict__))
    self.assertTrue(all(token.encode() not in path.read_bytes() for path in self.root.iterdir()))
    self.assertEqual(sessions.ownership_verified, os.name == "posix")

  def test_exact_origin_boot_and_secret_binding_no_wrong_attempt_consumption(self):
    grant = self.grant()
    sessions = self.sessions()
    for secret, origin in [(secrets.token_urlsafe(32), ORIGIN), (grant["secret"], "http://192.168.0.3:7000")]:
      with self.assertRaises(access.AccessDenied) as caught:
        sessions.redeem(grant["grant_id"], secret, origin)
      self.assertNotIn(secret, str(caught.exception))
    with self.assertRaises(access.AccessDenied): self.redeem(self.sessions(boot_id="other-boot"), grant)
    token = self.redeem(sessions, grant)
    self.assertFalse(sessions.authorized(token, "http://192.168.0.2:7001"))
    self.assertTrue(sessions.authorized(token, ORIGIN))

  def test_concurrent_managers_one_winner_and_restart_replay_denied(self):
    grant, barrier = self.grant(), threading.Barrier(2)
    def race(_):
      sessions = self.sessions()
      barrier.wait(timeout=2)
      try: return self.redeem(sessions, grant)
      except access.AccessDenied: return None
    with ThreadPoolExecutor(max_workers=2) as executor:
      results = list(executor.map(race, range(2)))
    self.assertEqual(1, sum(value is not None for value in results))
    with self.assertRaises(access.AccessDenied): self.redeem(self.sessions(), grant)
    # Removing a consumed marker does not restore the already consumed source.
    (self.root / (grant["grant_id"] + ".used")).unlink()
    with self.assertRaises(access.AccessDenied): self.redeem(self.sessions(), grant)

  def test_expiry_revocation_close_and_clock_regression_cannot_revive(self):
    grant, sessions = self.grant(ttl_seconds=1), self.sessions(session_seconds=1)
    self.clock.now += 1_000_000_000
    with self.assertRaises(access.AccessDenied): self.redeem(sessions, grant)
    token = self.redeem(sessions, self.grant())
    self.clock.now += 1_000_000_000
    self.assertFalse(sessions.authorized(token, ORIGIN))
    self.clock.now -= 500_000_000
    self.assertFalse(sessions.authorized(token, ORIGIN))
    revoked = self.redeem(sessions, self.grant())
    sessions.revoke(revoked)
    self.assertFalse(sessions.authorized(revoked, ORIGIN))
    closed = self.redeem(sessions, self.grant())
    sessions.close()
    self.assertFalse(sessions.authorized(closed, ORIGIN))
    with self.assertRaises(access.AccessDenied): self.redeem(sessions, self.grant())

  def test_session_capacity_does_not_consume_unredeemed_grant(self):
    sessions = self.sessions(max_sessions=1, session_seconds=1)
    token = self.redeem(sessions, self.grant())
    pending = self.grant()
    with self.assertRaises(access.AccessDenied): self.redeem(sessions, pending)
    sessions.revoke(token)
    self.assertTrue(sessions.authorized(self.redeem(sessions, pending), ORIGIN))

  def test_full_cache_can_expire_across_later_drives_without_permanent_exhaustion(self):
    for _ in range(access.MAX_GRANTS): self.grant(ttl_seconds=1)
    with self.assertRaises(access.AccessDenied): self.grant()
    self.clock.now += 1_000_000_000
    latest = self.grant()
    self.assertEqual(2, len(list(self.root.iterdir())))  # one grant + issuer lock
    sessions = self.sessions()
    self.assertTrue(sessions.authorized(self.redeem(sessions, latest), ORIGIN))
    self.clock.now += 91_000_000_000
    self.grant()
    self.assertEqual(2, len(list(self.root.iterdir())))

  def test_invalid_duplicate_oversize_and_metadata_tamper_fail_closed(self):
    for replacement in [b'{"schema_version":1,"schema_version":1}', b"x" * (access.MAX_FILE_BYTES + 1), b'{"schema_version":2}']:
      grant = self.grant()
      path = self.root / (grant["grant_id"] + ".json")
      path.write_bytes(replacement)
      with self.assertRaises(access.AccessDenied): self.redeem(self.sessions(), grant)
      path.unlink()
    for field, replacement in [("expires_mono_ns", 91_000_000_001), ("origin", "http://192.168.0.3:7000")]:
      grant = self.grant()
      path = self.root / (grant["grant_id"] + ".json")
      value = json.loads(path.read_bytes())
      value[field] = replacement
      path.write_text(json.dumps(value))
      with self.assertRaises(access.AccessDenied): self.redeem(self.sessions(), grant, value["origin"])
      path.unlink()

  def test_symlink_and_nonregular_grant_roots_and_files_rejected(self):
    grant = self.grant()
    path = self.root / (grant["grant_id"] + ".json")
    saved = self.root.parent / "saved.json"
    path.rename(saved)
    try:
      path.symlink_to(saved)
    except OSError as error:
      self.skipTest("OS cannot create fixture symlink: " + str(error.errno))
    with self.assertRaises((access.AccessDenied, OSError)): self.redeem(self.sessions(), grant)
    path.unlink()
    path.mkdir()
    with self.assertRaises((access.AccessDenied, OSError)): self.redeem(self.sessions(), grant)
    path.rmdir()
    linked = self.root.parent / "linked"
    linked.symlink_to(self.root, target_is_directory=True)
    with self.assertRaises((access.AccessDenied, OSError)):
      access.issue_local_grant(ORIGIN, root=linked, boot_id=BOOT, clock_ns=self.clock)

  @unittest.skipUnless(os.name == "posix", "POSIX owner/mode semantics are not Windows ACL verification")
  def test_posix_writable_or_foreign_owner_root_and_file_are_rejected(self):
    grant = self.grant()
    self.root.chmod(0o777)
    try:
      with self.assertRaises(access.AccessDenied): self.redeem(self.sessions(), grant)
    finally: self.root.chmod(0o700)
    path = self.root / (grant["grant_id"] + ".json")
    path.chmod(0o666)
    try:
      with self.assertRaises(access.AccessDenied): self.redeem(self.sessions(), grant)
    finally: path.chmod(0o600)
    if os.geteuid() == 0:
      os.chown(path, 65534, -1)
      try:
        with self.assertRaises(access.AccessDenied): self.redeem(self.sessions(), grant)
      finally: os.chown(path, 0, -1)
      os.chown(self.root, 65534, -1)
      try:
        with self.assertRaises(access.AccessDenied): self.redeem(self.sessions(), grant)
      finally: os.chown(self.root, 0, -1)

  def test_fd_and_path_each_reject_metadata_change_during_read(self):
    grant = self.grant()
    path = self.root / (grant["grant_id"] + ".json")
    original, changed = os.read, False
    def tamper(fd, size):
      nonlocal changed
      value = original(fd, size)
      if not changed:
        changed = True
        info = path.stat()
        os.utime(path, ns=(info.st_atime_ns, info.st_mtime_ns + 1_000_000))
      return value
    with access._directory(self.root) as directory, patch.object(access.os, "read", tamper):
      with self.assertRaisesRegex(access.AccessDenied, "file_changed"):
        directory.read(path.name)

  def test_tampered_used_marker_cannot_redeem_restored_original(self):
    grant = self.grant()
    path = self.root / (grant["grant_id"] + ".json")
    original = path.read_bytes()
    self.redeem(self.sessions(), grant)
    (self.root / (grant["grant_id"] + ".used")).write_bytes(b'{"schema_version":999}')
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600)
    try: os.write(fd, original)
    finally: os.close(fd)
    with self.assertRaises(access.AccessDenied): self.redeem(self.sessions(), grant)

  def test_marker_fsync_failure_cannot_return_or_replay_session(self):
    grant, sessions = self.grant(), self.sessions()
    with patch.object(access.os, "fsync", side_effect=OSError("injected marker durability failure")):
      with self.assertRaises(access.AccessDenied): self.redeem(sessions, grant)
    with self.assertRaises(access.AccessDenied): self.redeem(self.sessions(), grant)
    self.assertEqual({}, sessions._sessions)

  def test_authorization_does_not_wait_for_redeem_filesystem(self):
    sessions = self.sessions()
    token = self.redeem(sessions, self.grant())
    pending = self.grant()
    entered, release = threading.Event(), threading.Event()
    original = access._Directory.create
    def delayed(directory, name, value):
      if name.endswith(".used"):
        entered.set()
        if not release.wait(2): raise AssertionError("test release not signaled")
      return original(directory, name, value)
    with patch.object(access._Directory, "create", delayed), ThreadPoolExecutor(max_workers=1) as executor:
      future = executor.submit(self.redeem, sessions, pending)
      self.assertTrue(entered.wait(2))
      try:
        with patch.object(access, "_directory", side_effect=AssertionError("authorization touched filesystem")):
          self.assertTrue(sessions.authorized(token, ORIGIN))
      finally: release.set()
      self.assertIsInstance(future.result(timeout=2), str)

  def test_origin_policy_and_malformed_fragment_fields(self):
    for origin in ["http://100.64.1.2:7000", "http://8.8.8.8:7000", "https://[2001:db8::1]:7000"]:
      self.assertTrue(access.connection_url(origin, self.grant(origin)).startswith(origin + "#"))
    for origin in ["http://localhost:7000", "http://127.0.0.1:7000", "http://0.0.0.0:7000", "http://224.0.0.1:7000",
                   "http://192.168.0.2:0", "http://192.168.0.2:65536", ORIGIN + "/", ORIGIN + "?x=1", ORIGIN + "#x", "http://u@192.168.0.2:7000"]:
      with self.assertRaises(access.AccessDenied): self.grant(origin)
    grant = self.grant()
    for field, value in [("grant_id", "../escape"), ("secret", "invalid"), ("expires_mono_ns", True)]:
      with self.assertRaises(access.AccessDenied): access.connection_url(ORIGIN, {**grant, field: value})
    with patch("builtins.open", side_effect=FileNotFoundError):
      with self.assertRaisesRegex(access.AccessDenied, "boot_unavailable"):
        access.AccessSessions(self.root)


if __name__ == "__main__": unittest.main()
