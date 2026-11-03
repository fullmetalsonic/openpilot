import copy
from dataclasses import replace
from types import SimpleNamespace
from concurrent.futures import ThreadPoolExecutor
import datetime as dt
import hashlib
import http.client
import importlib.util
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import tempfile
import threading
import time
import unittest
import urllib.parse
from unittest import mock

SPEC = importlib.util.spec_from_file_location("carrot_pc_receiver", Path(__file__).parents[1] / "receiver.py")
pc = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(pc)
ENDPOINT = pc.DEFAULT_ENDPOINT
ROUTE = "2026-10-25--23-00-00"
DATA = b"synthetic-rlog\x00" * 80000


def date_info():
  stamp = int(dt.datetime(2026, 10, 25, 16, tzinfo=dt.timezone.utc).timestamp()) * 10**9
  return {"dateBasis": "route_start", "timezone": "Asia/Seoul", "routeStartWallTimeNanos": str(stamp),
          "driveDate": "2026-10-26", "dateStatus": "known", "dateSource": "initData.wallTimeNanos", "dateReason": None}


def job(device):
  return {"version": 1, "id": "b" * 64, "deviceId": device, "status": "queued", "createdAt": 1, "updatedAt": 1,
          "error": None, "scope": "selected", "routes": {ROUTE: date_info()}, "files": [
            {"id": "c" * 32, "route": ROUTE, "segment": ROUTE + "--3", "name": "rlog.bz2", "size": len(DATA),
             "sha256": hashlib.sha256(DATA).hexdigest(), "status": "pending", "receiptId": None}]}


class ReceiverTests(unittest.TestCase):
  def setUp(self):
    self.temp = tempfile.TemporaryDirectory()
    self.addCleanup(self.temp.cleanup)
    self.root = Path(self.temp.name)
    self.clock = [1000.0]
    self.policy = pc.Policy(min_free=pc.RESERVE, max_active_per_sender=64)
    self.receiver = self.new_receiver()
    self.key = secrets.token_urlsafe(32)
    self.owner = self.receiver.register_sender({"version": 1}, "Bearer " + self.key)["deviceId"]
    self.source = job(self.owner)

  def new_receiver(self):
    return pc.Receiver(self.root / "state", self.root / "logs", ENDPOINT, clock=lambda: self.clock[0], policy=self.policy)

  def announce(self):
    return self.receiver.create_job(self.owner, {"version": 1, "job": self.source}, 1, "e" * 32)

  def upload(self, data=DATA, start=0):
    result = None
    for offset in range(start, len(data), pc.MAX_CHUNK):
      block = data[offset:offset + pc.MAX_CHUNK]
      result = self.receiver.put_chunk(self.owner, self.source["id"], self.source["files"][0]["id"], offset,
                                      offset + len(block) - 1, len(DATA), self.source["files"][0]["sha256"], block)
    return result

  def final(self):
    return self.root / "logs" / "2026-10-26" / self.owner / ROUTE / (ROUTE + "--3") / "rlog.bz2"

  def test_production_endpoint_and_explicit_loopback_test_mode(self):
    self.assertEqual(pc.endpoint_url(pc.DEFAULT_ENDPOINT), pc.DEFAULT_ENDPOINT)
    for value in ["http://host.ts.net", "https://host.example.com", "https://host.ts.net:444", "https://@host.ts.net",
                  "https://host.ts.net/?", "https://host.ts.net/#", "https://host.ts.net/a", "https://.ts.net", "https://bad!.ts.net", "http://127.0.0.1:1234",
                  pc.DEFAULT_ENDPOINT + "/", pc.DEFAULT_ENDPOINT + ":80", pc.DEFAULT_ENDPOINT.replace("http:", "https:")]:
      with self.subTest(value=value), self.assertRaises(pc.ReceiverError):
        pc.endpoint_url(value)
    self.assertEqual(pc.endpoint_url("http://127.0.0.1:1234", True), "http://127.0.0.1:1234")
    with self.assertRaises(pc.ReceiverError):
      pc.endpoint_url("http://192.168.1.1:1234", True)

  def test_auth_owner_hash_constanttime_boundary(self):
    self.assertEqual(self.receiver.authorize(self.owner, "Bearer " + self.key), self.owner)
    for owner, key in [("0" * 32, self.key), (self.owner, "x" * 43), (self.owner, self.key + "\n")]:
      with self.subTest(owner=owner), self.assertRaises(pc.ReceiverError):
        self.receiver.authorize(owner, "Bearer " + key)

  def test_registration_limits_idempotency_day_and_restart(self):
    self.policy = replace(self.policy, max_senders=2, new_senders_per_day=1)
    self.receiver.policy = self.policy
    with self.assertRaises(pc.ReceiverError) as caught:
      self.receiver.register_sender({"version": 1}, "Bearer " + secrets.token_urlsafe(32))
    self.assertEqual(caught.exception.code, "registration_rate_limited")
    self.assertEqual(self.receiver.register_sender({"version": 1}, "Bearer " + self.key)["deviceId"], self.owner)
    self.clock[0] += 86400
    second_key = secrets.token_urlsafe(32)
    second = self.receiver.register_sender({"version": 1}, "Bearer " + second_key)
    self.receiver = self.new_receiver()
    self.assertEqual(self.receiver.register_sender({"version": 1}, "Bearer " + second_key), second)
    self.clock[0] += 86400
    with self.assertRaises(pc.ReceiverError) as caught:
      self.receiver.register_sender({"version": 1}, "Bearer " + secrets.token_urlsafe(32))
    self.assertEqual(caught.exception.code, "sender_quota")

  def test_persistent_request_rate_and_clock_rollback(self):
    self.policy = replace(self.policy, requests_per_minute=2, sender_requests_per_minute=1)
    self.receiver.policy = self.policy
    self.receiver.accept_request(self.owner)
    self.receiver = self.new_receiver()
    with self.assertRaises(pc.ReceiverError) as caught:
      self.receiver.accept_request(self.owner)
    self.assertEqual(caught.exception.status, 429)
    self.clock[0] += 60
    self.receiver.accept_request(self.owner)
    self.clock[0] -= 60
    with self.assertRaises(pc.ReceiverError):
      self.receiver.accept_request(self.owner)

  def test_daily_bytes_restart_global_and_per_sender(self):
    self.announce()
    self.policy = replace(self.policy, daily_global=5, daily_sender=4)
    self.receiver.policy = self.policy
    digest = self.source["files"][0]["sha256"]
    self.receiver.put_chunk(self.owner, self.source["id"], "c" * 32, 0, 3, len(DATA), digest, DATA[:4])
    self.receiver = self.new_receiver()
    with self.assertRaises(pc.ReceiverError) as caught:
      self.receiver.put_chunk(self.owner, self.source["id"], "c" * 32, 4, 4, len(DATA), digest, DATA[4:5])
    self.assertEqual(caught.exception.code, "daily_quota")
    second_key = secrets.token_urlsafe(32)
    second_owner = self.receiver.register_sender({"version": 1}, "Bearer " + second_key)["deviceId"]
    second_job = job(second_owner)
    self.receiver.create_job(second_owner, {"version": 1, "job": second_job})
    with self.assertRaises(pc.ReceiverError):
      self.receiver.put_chunk(second_owner, second_job["id"], "c" * 32, 0, 1, len(DATA), digest, DATA[:2])
    self.clock[0] += 86400
    self.announce()
    self.receiver.put_chunk(self.owner, self.source["id"], "c" * 32, 4, 4, len(DATA), digest, DATA[4:5])

  def test_charge_before_failed_write_persists_across_restart(self):
    self.announce()
    self.policy = replace(self.policy, daily_global=4)
    self.receiver.policy = self.policy
    original = pc.os.open
    def fail_partial(path, *args, **kwargs):
      if str(path).endswith(".partial"):
        raise OSError(5, "synthetic_write_failure")
      return original(path, *args, **kwargs)
    with mock.patch.object(pc.os, "open", side_effect=fail_partial), self.assertRaises(OSError):
      self.receiver.put_chunk(self.owner, self.source["id"], "c" * 32, 0, 3, len(DATA), self.source["files"][0]["sha256"], DATA[:4])
    self.receiver = self.new_receiver()
    self.assertEqual(self.receiver.auth["dailyBytes"], 4)
    with self.assertRaises(pc.ReceiverError):
      self.receiver.put_chunk(self.owner, self.source["id"], "c" * 32, 0, 0, len(DATA), self.source["files"][0]["sha256"], DATA[:1])

  def test_cumulative_quota_counts_quarantine_and_state_after_restart(self):
    self.announce()
    (self.root / "logs" / "preserved.invalid-test").write_bytes(b"x" * 2048)
    self.receiver = self.new_receiver()
    self.assertEqual(self.receiver.stored_bytes, pc.tree_bytes(self.root / "logs") + pc.tree_bytes(self.root / "state"))
    self.receiver.policy = replace(self.policy, max_storage=self.receiver.stored_bytes + 16384)
    with self.assertRaises(pc.ReceiverError) as caught:
      self.receiver.put_chunk(self.owner, self.source["id"], "c" * 32, 0, 0, len(DATA), self.source["files"][0]["sha256"], DATA[:1])
    self.assertEqual(caught.exception.code, "storage_quota")
    self.assertEqual((self.root / "logs" / "preserved.invalid-test").stat().st_size, 2048)

  def test_per_sender_active_total_jobs_and_persisted_pause(self):
    self.announce()
    self.receiver.policy = replace(self.policy, max_active_per_sender=1)
    second = copy.deepcopy(self.source)
    second["id"] = "d" * 64
    with self.assertRaises(pc.ReceiverError) as caught:
      self.receiver.create_job(self.owner, {"version": 1, "job": second})
    self.assertEqual(caught.exception.code, "active_job_bound")
    self.receiver.set_paused(True)
    self.receiver = self.new_receiver()
    self.assertTrue(self.receiver.auth["paused"])
    self.assertEqual(self.receiver.snapshot(self.owner, self.source["id"])["status"], "queued")
    with self.assertRaises(pc.ReceiverError) as caught:
      self.upload()
    self.assertEqual(caught.exception.status, 503)
    self.receiver.set_paused(False)
    self.upload()
    self.receiver.policy = replace(self.policy, max_jobs=1)
    with self.assertRaises(pc.ReceiverError) as caught:
      self.receiver.create_job(self.owner, {"version": 1, "job": second})
    self.assertEqual(caught.exception.code, "job_quota")

  def test_real_upload_hash_fsync_receipt_and_kst_day(self):
    self.assertEqual(self.announce()["job"]["status"], "queued")
    original = pc.private_json
    receipts = []

    def check_receipt(path, value):
      if str(path).endswith(".receipt.json"):
        self.assertEqual(self.final().read_bytes(), DATA)
        receipts.append(value)
      return original(path, value)

    with mock.patch.object(pc, "private_json", side_effect=check_receipt):
      result = self.upload()
    self.assertEqual(result["job"]["status"], "completed")
    self.assertEqual(self.final().read_bytes(), DATA)
    self.assertEqual(len(receipts), 1)
    self.assertEqual(receipts[0]["dateInfo"], date_info())
    self.assertNotIn(self.key, json.dumps(receipts))

  def test_partial_restart_and_lost_response_returns_disk_proof(self):
    self.announce()
    first = DATA[:pc.MAX_CHUNK]
    self.receiver.put_chunk(self.owner, self.source["id"], "c" * 32, 0, len(first) - 1, len(DATA), self.source["files"][0]["sha256"], first)
    self.receiver = self.new_receiver()
    current = self.receiver.snapshot(self.owner, self.source["id"])
    self.assertEqual(current["files"][0]["receivedBytes"], len(first))
    result = self.upload(start=len(first))
    receipt = result["job"]["files"][0]["receiptId"]
    self.receiver = self.new_receiver()
    self.assertEqual(self.announce()["job"]["files"][0]["receiptId"], receipt)
    with self.assertRaises(pc.ReceiverError) as caught:
      self.receiver.put_chunk(self.owner, self.source["id"], "c" * 32, 0, len(first) - 1,
                              len(DATA), self.source["files"][0]["sha256"], first)
    self.assertEqual(caught.exception.code, "lease_expired")
    self.assertEqual(self.receiver.snapshot(self.owner, self.source["id"])["files"][0]["receiptId"], receipt)

  def test_complete_partial_crash_reconciles_before_ack(self):
    self.announce()
    self.final().parent.mkdir(parents=True)
    self.final().with_name("rlog.bz2.partial").write_bytes(DATA)
    self.receiver = self.new_receiver()
    self.assertEqual(self.receiver.snapshot(self.owner, self.source["id"])["status"], "completed")
    self.assertEqual(self.final().read_bytes(), DATA)

  def test_sender_completion_claim_is_not_receiver_proof_and_progress_is_mutable(self):
    self.source.update(status="completed", error="sender_claim", updatedAt=999)
    self.source["files"][0].update(status="acknowledged", receiptId="sender-claim")
    self.assertEqual(self.announce()["job"]["status"], "queued")
    self.source.update(status="transferring", error=None, updatedAt=1000)
    self.source["files"][0].update(status="pending", receiptId=None)
    self.assertTrue(self.announce()["duplicate"])
    changed = copy.deepcopy(self.source)
    changed["files"][0]["sha256"] = "0" * 64
    with self.assertRaises(pc.ReceiverError):
      self.receiver.create_job(self.owner, {"version": 1, "job": changed})

  def test_wrong_offset_chunk_bound_and_foreign_job(self):
    self.announce()
    for args in [(1, 1, len(DATA), b"x"), (0, pc.MAX_CHUNK, len(DATA), b"x" * (pc.MAX_CHUNK + 1))]:
      with self.subTest(start=args[0]), self.assertRaises(pc.ReceiverError):
        self.receiver.put_chunk(self.owner, self.source["id"], "c" * 32, args[0], args[1], args[2], self.source["files"][0]["sha256"], args[3])
    with self.assertRaises(pc.ReceiverError) as caught:
      self.receiver.snapshot("0" * 32, self.source["id"])
    self.assertEqual(caught.exception.status, 404)

  def test_disk_full_writes_no_partial_or_ack(self):
    self.announce()
    usage = shutil.disk_usage(self.root)
    with mock.patch.object(pc.shutil, "disk_usage", return_value=type(usage)(usage.total, usage.used, pc.RESERVE)):
      with self.assertRaises(pc.ReceiverError) as caught:
        self.upload()
    self.assertEqual(caught.exception.status, 507)
    self.assertFalse(self.final().exists())
    self.assertFalse(self.final().with_name("rlog.bz2.partial").exists())

  def test_hash_mismatch_quarantines_preserves_and_can_restart(self):
    self.announce()
    with self.assertRaises(pc.ReceiverError):
      self.upload(b"?" + DATA[1:])
    self.assertFalse(self.final().exists())
    self.assertEqual(len(list(self.final().parent.glob("*.invalid-*"))), 1)
    self.assertEqual(self.receiver.snapshot(self.owner, self.source["id"])["status"], "failed")
    self.assertEqual(self.upload()["job"]["status"], "completed")

  def test_existing_file_and_receipt_conflicts_never_clobber(self):
    self.announce()
    self.final().parent.mkdir(parents=True)
    self.final().write_bytes(b"user-existing-file")
    self.assertEqual(self.receiver.snapshot(self.owner, self.source["id"])["status"], "failed")
    with self.assertRaises(pc.ReceiverError):
      self.upload()
    self.assertEqual(self.final().read_bytes(), b"user-existing-file")

  def test_tampered_receipt_or_deleted_final_not_accepted(self):
    self.announce()
    self.upload()
    path = self.final().with_name("rlog.bz2.receipt.json")
    receipt = pc.read_json(path)
    receipt["size"] += 1
    pc.private_json(path, receipt)
    self.assertEqual(self.receiver.snapshot(self.owner, self.source["id"])["status"], "failed")
    receipt["size"] -= 1
    pc.private_json(path, receipt)
    self.final().unlink()
    self.assertEqual(self.receiver.snapshot(self.owner, self.source["id"])["status"], "failed")

  def test_unknown_date_nonzero_segment_and_metadata_bounds(self):
    info = self.source["routes"][ROUTE]
    info.update(dateStatus="unknown", driveDate=None, routeStartWallTimeNanos=None, dateReason="missing_init_data")
    self.announce()
    self.upload()
    unknown = self.root / "logs" / "날짜미확인" / self.owner / ROUTE / (ROUTE + "--3") / "rlog.bz2"
    self.assertEqual(unknown.read_bytes(), DATA)
    for mutate in [lambda j: j["routes"][ROUTE].update(driveDate="2026-10-26"),
                   lambda j: j["files"][0].update(size=8 * 1024**3 + 1),
                   lambda j: j["files"].extend([j["files"][0]] * 2000),
                   lambda j: j["files"][0].update(name="qlog.zst")]:
      with self.subTest(mutate=mutate), self.assertRaises(pc.ReceiverError):
        value = copy.deepcopy(self.source)
        mutate(value)
        pc.manifest(value, self.owner)

  def test_paths_date_mismatch_and_windows_reserved(self):
    for name in ["..", "../outside", "x\\outside", "C:escape", "CON", "nul.txt", "LPT1", "trailing."]:
      with self.subTest(name=name), self.assertRaises(pc.ReceiverError):
        pc.component(name)
    info = date_info()
    info["driveDate"] = "2026-10-25"
    with self.assertRaises(pc.ReceiverError):
      pc.date_folder(info)

  def test_junction_or_symlink_output_ancestor_rejected(self):
    target, link = self.root / "target", self.root / "link"
    target.mkdir()
    if os.name == "nt":
      result = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], capture_output=True)
      if result.returncode:
        self.skipTest("Junction creation unavailable")
    else:
      link.symlink_to(target, target_is_directory=True)
    with self.assertRaises(pc.ReceiverError):
      pc.Receiver(self.root / "state2", link / "logs", ENDPOINT)
    self.assertFalse((target / "logs").exists())

  def test_active_job_bound(self):
    for index in range(64):
      self.source["id"] = "%064x" % index
      self.announce()
    self.source["id"] = "f" * 64
    with self.assertRaises(pc.ReceiverError) as caught:
      self.announce()
    self.assertEqual(caught.exception.status, 429)

  def test_previous_job_state_migration_preserves_original(self):
    self.announce()
    path = self.root / "state" / "jobs" / self.owner / (self.source["id"] + ".json")
    legacy = path.parent.parent / path.name
    original = path.read_bytes()
    legacy.write_bytes(original)
    path.unlink()
    self.receiver = self.new_receiver()
    self.assertEqual(path.read_bytes(), original)
    self.assertEqual(legacy.read_bytes(), original)
    self.assertEqual(self.receiver.snapshot(self.owner, self.source["id"])["status"], "queued")

  def test_release_returns_four_slots_without_deleting_partial(self):
    self.receiver.policy = replace(self.policy, max_active_per_sender=4)
    ids = []
    for index in range(4):
      self.source["id"] = "%064x" % index
      ids.append(self.source["id"])
      self.announce()
    self.source["id"] = "e" * 64
    with self.assertRaises(pc.ReceiverError):
      self.announce()
    self.receiver.put_chunk(self.owner, ids[0], "c" * 32, 0, 3, len(DATA), self.source["files"][0]["sha256"], DATA[:4])
    partial = self.final().with_name("rlog.bz2.partial")
    self.receiver.set_paused(True)
    expected = {"ok": True, "released": True}
    self.assertEqual(self.receiver.release(self.owner, ids[0], {"version": 1}), expected)
    self.assertEqual(self.receiver.release(self.owner, ids[0], {"version": 1}), expected)
    self.assertEqual(self.receiver.release("0" * 32, ids[1], {"version": 1}), expected)
    self.assertEqual(self.receiver.release(self.owner, "f" * 64, {"version": 1}), expected)
    self.assertGreater(self.receiver.jobs[(self.owner, ids[1])]["leaseUntil"], self.clock[0])
    self.assertEqual(partial.read_bytes(), DATA[:4])
    self.receiver.set_paused(False)
    self.assertEqual(self.announce()["job"]["status"], "transferring")

  def test_ttl_global_recovery_get_no_renew_restart_and_resume(self):
    self.policy = replace(self.policy, max_active=1, lease_seconds=10)
    self.receiver.policy = self.policy
    self.announce()
    digest = self.source["files"][0]["sha256"]
    self.receiver.put_chunk(self.owner, self.source["id"], "c" * 32, 0, 3, len(DATA), digest, DATA[:4])
    lease = self.receiver.jobs[(self.owner, self.source["id"])]["leaseUntil"]
    self.clock[0] += 9
    self.receiver.snapshot(self.owner, self.source["id"])
    self.assertEqual(self.receiver.jobs[(self.owner, self.source["id"])]["leaseUntil"], lease)
    self.receiver = self.new_receiver()
    self.assertEqual(self.receiver.jobs[(self.owner, self.source["id"])]["leaseUntil"], lease)
    self.clock[0] += 1
    with self.assertRaises(pc.ReceiverError) as caught:
      self.receiver.put_chunk(self.owner, self.source["id"], "c" * 32, 4, 4, len(DATA), digest, DATA[4:5])
    self.assertEqual(caught.exception.code, "lease_expired")
    another = copy.deepcopy(self.source)
    another["id"] = "d" * 64
    self.receiver.create_job(self.owner, {"version": 1, "job": another})
    self.receiver.release(self.owner, another["id"], {"version": 1})
    self.announce()
    self.assertEqual(self.upload(start=4)["job"]["status"], "completed")
    self.assertEqual(self.receiver.jobs[(self.owner, self.source["id"])]["leaseUntil"], 0)

  def test_missing_old_lease_is_inactive_and_metadata_bound_precedes_reads(self):
    self.announce()
    path = self.root / "state" / "jobs" / self.owner / (self.source["id"] + ".json")
    value = pc.read_json(path)
    value.pop("leaseUntil")
    pc.private_json(path, value)
    self.receiver = self.new_receiver()
    self.assertEqual(self.receiver.jobs[(self.owner, self.source["id"])]["leaseUntil"], 0)
    self.policy = replace(self.policy, max_metadata=1)
    with mock.patch.object(pc, "read_json") as read, self.assertRaises(pc.ReceiverError) as caught:
      self.new_receiver()
    self.assertEqual(caught.exception.code, "metadata_quota")
    read.assert_not_called()

  def test_metadata_growth_rejected_without_replacing_existing_state(self):
    before = self.receiver.auth_path.read_bytes()
    self.receiver.policy = replace(self.policy, max_metadata=self.receiver.metadata_bytes)
    with self.assertRaises(pc.ReceiverError) as caught:
      self.receiver.persist(self.root / "state" / "extra.json", {"unused": "x" * 500})
    self.assertEqual(caught.exception.code, "metadata_quota")
    self.assertEqual(self.receiver.auth_path.read_bytes(), before)
    self.assertFalse((self.root / "state" / "extra.json").exists())

  def test_duplicate_json_fields_and_loopback_listener_only(self):
    with self.assertRaises(pc.ReceiverError):
      pc.strict_json(b'{"version":1,"version":2}')
    with self.assertRaises(pc.ReceiverError):
      pc.make_server(self.receiver, "0.0.0.0", 0)


class HttpTests(unittest.TestCase):
  new_receiver = ReceiverTests.new_receiver
  announce = ReceiverTests.announce
  upload = ReceiverTests.upload
  final = ReceiverTests.final

  def setUp(self):
    ReceiverTests.setUp(self)
    self.server = pc.make_server(self.receiver, port=0)
    self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
    self.thread.start()
    self.addCleanup(self.close_server)

  def close_server(self):
    self.server.shutdown()
    self.server.server_close()
    self.thread.join()

  def request_http(self, method, path, data=None, headers=None, auth=True):
    connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
    values = {"X-Carrot-Device-Id": self.owner, "Authorization": "Bearer " + self.key, "X-Carrot-Transport-Epoch": "1", "X-Carrot-Transport-Id": "e" * 32} if auth else {}
    if isinstance(data, dict):
      data = json.dumps(data).encode()
      values["Content-Type"] = "application/json"
    values.update(headers or {})
    connection.request(method, pc.API + path, body=data, headers=values)
    response = connection.getresponse()
    raw = response.read()
    self.last_headers = dict(response.headers)
    status, body = response.status, json.loads(raw) if raw else None
    connection.close()
    return status, body

  def test_http_normal_file_and_owner_authorization(self):
    status, created = self.request_http("POST", "/jobs", {"version": 1, "job": self.source})
    self.assertEqual(status, 200)
    for offset in range(0, len(DATA), pc.MAX_CHUNK):
      block = DATA[offset:offset + pc.MAX_CHUNK]
      status, uploaded = self.request_http("PUT", "/jobs/" + self.source["id"] + "/files/" + "c" * 32, block, {
        "Content-Type": "application/octet-stream", "Content-Range": "bytes %d-%d/%d" % (offset, offset + len(block) - 1, len(DATA)),
        "X-Content-SHA256": self.source["files"][0]["sha256"]})
      self.assertEqual(status, 200)
    self.assertEqual(uploaded["job"]["status"], "completed")
    self.assertEqual(self.final().read_bytes(), DATA)
    self.assertEqual(self.request_http("GET", "/jobs/" + self.source["id"])[1]["job"], uploaded["job"])
    self.assertEqual(self.request_http("GET", "/jobs/" + self.source["id"], auth=False)[0], 401)

  def test_http_sender_registration_lost_response_restart_and_safe_errors(self):
    key = secrets.token_urlsafe(32)
    headers = {"Authorization": "Bearer " + key}
    status, response = self.request_http("POST", "/senders", {"version": 1}, headers, auth=False)
    self.assertEqual(status, 200)
    self.assertEqual(set(response), {"ok", "version", "endpoint", "deviceId"})
    self.assertEqual(response, self.request_http("POST", "/senders", {"version": 1}, headers, auth=False)[1])
    restarted = self.new_receiver()
    self.assertEqual(restarted.register_sender({"version": 1}, "Bearer " + key), response)
    self.assertNotIn(key, (self.root / "state" / "senders.json").read_text())
    status, response = self.request_http("POST", "/jobs", {"version": 1, "job": self.source}, {"Authorization": "Bearer " + "wrong" * 10})
    self.assertEqual((status, response), (401, {"ok": False, "error": "unauthorized"}))

  def test_http_cors_private_origin_preflight_actual_and_rejection(self):
    origin = "http://192.168.43.1:7000"
    headers = {"Origin": origin, "Access-Control-Request-Method": "PUT", "Access-Control-Request-Headers": ", ".join(pc.CORS_HEADERS)}
    status, _ = self.request_http("OPTIONS", "/jobs/" + self.source["id"] + "/files/" + "c" * 32, headers=headers, auth=False)
    self.assertEqual(status, 204)
    self.assertEqual(self.last_headers["Access-Control-Allow-Origin"], origin)
    self.assertEqual(self.last_headers["Vary"], "Origin")
    self.assertEqual(set(self.last_headers["Access-Control-Allow-Headers"].split(", ")), pc.CORS_HEADERS)
    self.assertEqual(self.request_http("POST", "/jobs", {"version": 1, "job": self.source}, {"Origin": origin})[0], 200)
    self.assertEqual(self.last_headers["Access-Control-Expose-Headers"], "Retry-After")
    for invalid in ["null", "*", "https://example.com", "http://user@192.168.1.1", "http://192.168.1.1/", "http://192.168.1.1?",
                    "http://192.168.1.1#", "http://172.32.1.1", "http://0.0.0.0", "http://localhost:7000", "http://127.0.0.1:7000"]:
      with self.subTest(origin=invalid):
        self.assertEqual(self.request_http("GET", "/jobs/" + self.source["id"], headers={"Origin": invalid})[0], 403)
        self.assertNotIn("Access-Control-Allow-Origin", self.last_headers)
    self.receiver.allow_test_http = True
    self.assertEqual(self.request_http("GET", "/jobs/" + self.source["id"], headers={"Origin": "http://localhost:7000"})[0], 200)
    headers["Access-Control-Request-Headers"] = "Authorization, X-Unapproved"
    self.assertEqual(self.request_http("OPTIONS", "/jobs", headers=headers, auth=False)[0], 403)

  def test_http_epoch_takeover_blocks_old_upload_release_and_get_then_resumes(self):
    old = {"Origin": "http://10.0.0.2:7000", "X-Carrot-Transport-Epoch": "3", "X-Carrot-Transport-Id": "a" * 32}
    new = dict(old, **{"X-Carrot-Transport-Epoch": "4", "X-Carrot-Transport-Id": "b" * 32})
    path = "/jobs/" + self.source["id"]
    body = {"version": 1, "job": self.source}
    self.assertEqual(self.request_http("POST", "/jobs", body, old)[0], 200)
    def upload(headers, start, data):
      return self.request_http("PUT", path + "/files/" + "c" * 32, data, dict(headers, **{
        "Content-Type": "application/octet-stream", "Content-Range": "bytes %d-%d/%d" % (start, start + len(data) - 1, len(DATA)),
        "X-Content-SHA256": self.source["files"][0]["sha256"]}))
    self.assertEqual(upload(old, 0, DATA[:4])[0], 200)
    self.assertEqual(self.request_http("POST", "/jobs", body, new)[0], 200)
    lease = self.receiver.jobs[(self.owner, self.source["id"])]["leaseUntil"]
    for method, route, value, headers in [("POST", "/jobs", body, old), ("POST", path + "/release", {"version": 1}, old),
      ("GET", path, None, old), ("GET", path, None, dict(new, **{"X-Carrot-Transport-Epoch": "5"})),
      ("POST", "/jobs", body, dict(new, **{"X-Carrot-Transport-Id": "a" * 32}))]:
      self.assertEqual(self.request_http(method, route, value, headers)[0], 409)
    self.assertEqual(upload(old, 4, DATA[4:5])[0], 409)
    self.assertEqual(self.receiver.jobs[(self.owner, self.source["id"])]["leaseUntil"], lease)
    self.assertEqual(self.final().with_name("rlog.bz2.partial").read_bytes(), DATA[:4])
    self.receiver = self.new_receiver()
    self.server.receiver = self.receiver
    self.assertEqual(self.request_http("GET", path, headers=old)[0], 409)
    self.assertEqual(self.request_http("GET", path, headers=new)[1]["job"]["files"][0]["receivedBytes"], 4)
    for offset in range(4, len(DATA), pc.MAX_CHUNK):
      status, result = upload(new, offset, DATA[offset:offset + pc.MAX_CHUNK])
      self.assertEqual(status, 200)
    self.assertEqual(result["job"]["status"], "completed")
    self.assertEqual(self.final().read_bytes(), DATA)

    latest = dict(new, **{"X-Carrot-Transport-Epoch": "5", "X-Carrot-Transport-Id": "c" * 32})
    status, completed = self.request_http("POST", "/jobs", body, latest)
    self.assertEqual((status, completed["job"]["status"]), (200, "completed"))
    self.assertEqual(self.request_http("GET", path, headers=new)[0], 409)
    self.assertEqual(self.request_http("POST", path + "/release", {"version": 1}, latest), (200, {"ok": True, "released": True}))

  def test_http_job_fence_is_required_and_bounded(self):
    for epoch, identity in [("", "e" * 32), ("0", "e" * 32), ("01", "e" * 32), (str(2**53), "e" * 32), ("1", "not-hex")]:
      with self.subTest(epoch=epoch):
        self.assertEqual(self.request_http("POST", "/jobs", {"version": 1, "job": self.source}, {
          "X-Carrot-Transport-Epoch": epoch, "X-Carrot-Transport-Id": identity})[0], 400)
    connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
    connection.request("POST", pc.API + "/jobs", body=json.dumps({"version": 1, "job": self.source}), headers={
      "Authorization": "Bearer " + self.key, "X-Carrot-Device-Id": self.owner, "Content-Type": "application/json"})
    response = connection.getresponse()
    self.assertEqual(response.status, 400)
    response.read()
    connection.close()
    self.assertFalse(self.receiver.jobs)

  def test_http_release_is_idempotent_owner_private_and_works_when_paused(self):
    self.announce()
    self.receiver.set_paused(True)
    path = "/jobs/" + self.source["id"] + "/release"
    first = self.request_http("POST", path, {"version": 1})
    self.assertEqual(first, (200, {"ok": True, "released": True}))
    self.assertEqual(self.request_http("POST", path, {"version": 1}), first)
    self.assertEqual(self.request_http("POST", "/jobs/" + "f" * 64 + "/release", {"version": 1}), first)

  def test_http_cut_chunk_has_no_ack_and_can_resume(self):
    self.announce()
    connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
    connection.connect()
    header = ("PUT %s/jobs/%s/files/%s HTTP/1.0\r\nAuthorization: Bearer %s\r\nX-Carrot-Device-Id: %s\r\n"
              "X-Carrot-Transport-Epoch: 1\r\nX-Carrot-Transport-Id: eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee\r\nContent-Type: application/octet-stream\r\nContent-Range: bytes 0-999/1120000\r\nContent-Length: 1000\r\n"
              "X-Content-SHA256: %s\r\n\r\n" % (pc.API, self.source["id"], "c" * 32, self.key, self.owner, self.source["files"][0]["sha256"]))
    connection.sock.sendall(header.encode() + b"short")
    connection.sock.shutdown(1)
    try:
      response = http.client.HTTPResponse(connection.sock)
      response.begin()
      self.assertEqual(response.status, 400)
      response.read()
      response.close()
    finally:
      connection.close()
    self.assertEqual(self.receiver.snapshot(self.owner, self.source["id"])["files"][0]["receivedBytes"], 0)
    self.assertEqual(self.upload()["job"]["status"], "completed")

  def test_http_two_devices_same_ids_concurrent_upload_restart_and_ownership(self):
    second_key = secrets.token_urlsafe(32)
    second_owner = self.receiver.register_sender({"version": 1}, "Bearer " + second_key)["deviceId"]
    second_data = b"second-device!" * 80000
    second_source = job(second_owner)
    second_source["files"][0].update(size=len(second_data), sha256=hashlib.sha256(second_data).hexdigest())
    self.assertEqual(len(self.receiver.auth["devices"]), 2)
    self.assertEqual(self.receiver.authorize(self.owner, "Bearer " + self.key), self.owner)

    def send(owner, key, source, data):
      auth = {"X-Carrot-Device-Id": owner, "Authorization": "Bearer " + key}
      self.assertEqual(self.request_http("POST", "/jobs", {"version": 1, "job": source}, auth)[0], 200)
      result = None
      for offset in range(0, len(data), pc.MAX_CHUNK):
        block = data[offset:offset + pc.MAX_CHUNK]
        status, result = self.request_http("PUT", "/jobs/" + source["id"] + "/files/" + "c" * 32, block, dict(auth, **{
          "Content-Type": "application/octet-stream", "Content-Range": "bytes %d-%d/%d" % (offset, offset + len(block) - 1, len(data)),
          "X-Content-SHA256": source["files"][0]["sha256"]}))
        self.assertEqual(status, 200)
      return result["job"]

    with ThreadPoolExecutor(max_workers=2) as pool:
      first = pool.submit(send, self.owner, self.key, self.source, DATA)
      second = pool.submit(send, second_owner, second_key, second_source, second_data)
      first_result, second_result = first.result(), second.result()
    self.assertEqual(first_result["status"], "completed")
    self.assertEqual(second_result["status"], "completed")
    self.assertNotEqual(first_result["files"][0]["receiptId"], second_result["files"][0]["receiptId"])
    self.assertEqual(self.final().read_bytes(), DATA)
    second_final = self.root / "logs" / "2026-10-26" / second_owner / ROUTE / (ROUTE + "--3") / "rlog.bz2"
    self.assertEqual(second_final.read_bytes(), second_data)
    restarted = self.new_receiver()
    self.assertEqual(restarted.snapshot(self.owner, self.source["id"]), first_result)
    self.assertEqual(restarted.snapshot(second_owner, self.source["id"]), second_result)
    self.assertEqual(self.request_http("GET", "/jobs/" + self.source["id"], headers={"Authorization": "Bearer " + second_key})[0], 401)
    self.assertEqual(self.request_http("PUT", "/jobs/" + self.source["id"] + "/files/" + "c" * 32, b"x", {
      "X-Carrot-Device-Id": second_owner, "Content-Type": "application/octet-stream", "Content-Range": "bytes 0-0/1",
      "X-Content-SHA256": "0" * 64})[0], 401)

  def test_http_handler_and_chunk_bounds_before_any_disk_write(self):
    self.announce()
    path = pc.API + "/jobs/" + self.source["id"] + "/files/" + "c" * 32
    waiting = []
    try:
      for _ in range(4):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        connection.putrequest("PUT", path)
        for key, value in {"Authorization": "Bearer " + self.key, "X-Carrot-Device-Id": self.owner,
                           "X-Carrot-Transport-Epoch": "1", "X-Carrot-Transport-Id": "e" * 32,
                           "Content-Type": "application/octet-stream", "Content-Length": "1000",
                           "Content-Range": "bytes 0-999/%d" % len(DATA), "X-Content-SHA256": self.source["files"][0]["sha256"]}.items():
          connection.putheader(key, value)
        connection.endheaders()
        waiting.append(connection)
      deadline = time.monotonic() + 2
      while self.server.slots._value and time.monotonic() < deadline:
        time.sleep(0.01)
      status, response = self.request_http("GET", "/jobs/" + self.source["id"])
      self.assertEqual((status, response["error"]), (503, "handler_bound"))
    finally:
      for connection in waiting:
        connection.sock.shutdown(1)
        response = connection.getresponse()
        response.read()
        connection.close()
    status, response = self.request_http("PUT", "/jobs/" + self.source["id"] + "/files/" + "c" * 32, b"", {
      "Content-Type": "application/octet-stream", "Content-Length": str(pc.MAX_CHUNK + 1),
      "Content-Range": "bytes 0-%d/%d" % (pc.MAX_CHUNK, len(DATA)), "X-Content-SHA256": self.source["files"][0]["sha256"]})
    self.assertEqual((status, response["error"]), (413, "request_bound"))
    self.assertFalse(self.final().exists())
    self.assertFalse(self.final().with_name("rlog.bz2.partial").exists())

  def test_http_rate_retry_after_and_slow_whole_request_deadline(self):
    self.announce()
    self.receiver.policy = replace(self.policy, sender_requests_per_minute=1, read_timeout=0.2, request_deadline=0.35)
    self.receiver.accept_request(self.owner)
    connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=2)
    connection.request("GET", pc.API + "/jobs/" + self.source["id"], headers={"Authorization": "Bearer " + self.key, "X-Carrot-Device-Id": self.owner, "X-Carrot-Transport-Epoch": "1", "X-Carrot-Transport-Id": "e" * 32})
    response = connection.getresponse()
    self.assertEqual(response.status, 429)
    self.assertEqual(response.headers["Retry-After"], "60")
    response.read()
    connection.close()
    slow = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=2)
    slow.connect()
    started = time.monotonic()
    slow.sock.sendall(b"POST /api/carrot-pc/v1/senders HTTP/1.0\r\n")
    try:
      for _ in range(12):
        time.sleep(0.06)
        try:
          slow.sock.sendall(b"X")
        except OSError:
          break
      try:
        closed = not slow.sock.recv(1024)
      except (ConnectionAbortedError, ConnectionResetError):
        closed = True
      self.assertTrue(closed)
      self.assertLess(time.monotonic() - started, 1.2)
    finally:
      slow.close()


class PrivateLifecycleTests(unittest.TestCase):
  def receiver(self):
    return SimpleNamespace(endpoint=pc.DEFAULT_ENDPOINT, last_status="local")

  def runner(self, config=None, hostname=None):
    def run(arguments, **kwargs):
      value = {"Self": {"DNSName": hostname or urllib.parse.urlsplit(pc.DEFAULT_ENDPOINT).hostname + "."}} if arguments[1] == "status" else (config or {})
      return SimpleNamespace(returncode=0, stdout=json.dumps(value).encode())
    return mock.Mock(side_effect=run)

  def test_foreground_child_owned_cleanup_and_no_background_or_reset(self):
    child = mock.Mock()
    child.poll.return_value = None
    run, popen = self.runner(), mock.Mock(return_value=child)
    tunnel = pc.PrivateTunnel(self.receiver(), 8766, "synthetic-tailscale", run, popen)
    tunnel.start()
    self.assertEqual(popen.call_args.args[0], ["synthetic-tailscale", "serve", "--yes", "--http=80", "http://127.0.0.1:8766"])
    self.assertIn("연결 준비", tunnel.status())
    tunnel.close()
    child.terminate.assert_called_once()
    child.wait.assert_called_once_with(timeout=3)
    self.assertFalse(any("reset" in str(call) or "--bg" in str(call) for call in run.call_args_list))

  def test_existing_configuration_and_wrong_hostname_never_spawn(self):
    for run in [self.runner({"TCP": {"443": {"HTTPS": True}}}), self.runner(hostname="other.ts.net")]:
      popen = mock.Mock()
      tunnel = pc.PrivateTunnel(self.receiver(), 8766, "synthetic-tailscale", run, popen)
      with self.assertRaises(pc.ReceiverError):
        tunnel.start()
      popen.assert_not_called()

  def test_running_child_is_not_ready_until_own_http_proxy_is_observed(self):
    child = mock.Mock()
    child.poll.return_value = None
    config = {}
    def run(arguments, **kwargs):
      result = {"Self": {"DNSName": urllib.parse.urlsplit(pc.DEFAULT_ENDPOINT).hostname}} if arguments[1] == "status" else config
      return SimpleNamespace(returncode=0, stdout=json.dumps(result).encode())
    receiver = self.receiver()
    tunnel = pc.PrivateTunnel(receiver, 8766, "synthetic-tailscale", run, mock.Mock(return_value=child))
    tunnel.start()
    self.assertIn("연결 준비", tunnel.status())
    host = urllib.parse.urlsplit(pc.DEFAULT_ENDPOINT).hostname + ":80"
    config.update(TCP={"80": {"HTTP": True}}, Web={host: {"Handlers": {"/": {"Proxy": "http://127.0.0.1:8766"}}}})
    tunnel.checked_at = 0
    self.assertIsNone(tunnel.status())
    self.assertIn("폰 Tailscale 연결 미확인", receiver.last_status)
    config["Web"][host]["Handlers"]["/"]["Proxy"] = "http://127.0.0.1:9999"
    tunnel.checked_at = 0
    self.assertIn("연결 준비", tunnel.status())
    config["Web"][host]["Handlers"]["/"]["Proxy"] = "http://127.0.0.1:8766"
    config["AllowFunnel"] = {host: True}
    tunnel.checked_at = 0
    self.assertIn("연결 준비", tunnel.status())

  def test_failed_child_and_shutdown_timeout_are_visible_and_bounded(self):
    child = mock.Mock()
    child.poll.return_value = 1
    tunnel = pc.PrivateTunnel(self.receiver(), 8766, "synthetic-tailscale", self.runner(), mock.Mock(return_value=child))
    with self.assertRaises(pc.ReceiverError):
      tunnel.start()
    self.assertIn("수신 불가", tunnel.status())
    child.poll.return_value = None
    child.wait.side_effect = [subprocess.TimeoutExpired("synthetic", 3), 0]
    tunnel.close()
    child.kill.assert_called_once()

  def test_foreground_serve_json_envelope_requires_own_proxy_and_no_funnel(self):
    host = "minipc-amd5500u.tail8d9376.ts.net:80"
    config = {"Foreground": {"0df459b6b59aa40a": {
      "TCP": {"80": {"HTTP": True}}, "Web": {host: {"Handlers": {"/": {"Proxy": "http://127.0.0.1:8766"}}}}}}}
    child = mock.Mock()
    child.poll.return_value = None
    tunnel = pc.PrivateTunnel(self.receiver(), 8766, "synthetic-tailscale", self.runner(config))
    tunnel.child = child
    self.assertIsNone(tunnel.status())
    entry = config["Foreground"]["0df459b6b59aa40a"]
    for changed in [
      {"Web": {host: {"Handlers": {"/": {"Proxy": "http://127.0.0.1:9999"}}}}},
      {"Web": {host: {"Handlers": {"/other": {"Proxy": "http://127.0.0.1:8766"}}}}},
      {"AllowFunnel": {host: True}},
    ]:
      original = copy.deepcopy(entry)
      entry.update(changed)
      tunnel.checked_at = 0
      self.assertIn("연결 준비", tunnel.status())
      entry.clear()
      entry.update(original)
    for target in [config, config["Foreground"].setdefault("other", {})]:
      target["AllowFunnel"] = {host: True}
      tunnel.checked_at = 0
      self.assertIn("연결 준비", tunnel.status())
      target.pop("AllowFunnel")


if __name__ == "__main__":
  unittest.main()
