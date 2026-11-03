import asyncio
import bz2
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from openpilot.selfdrive.carrot.server.features.dashcam import pc_transfer as feature


def recorded_log(wall="2026-10-27T14:59:00+00:00"):
  from openpilot.cereal import log
  event = log.Event.new_message()
  event.logMonoTime = 1000000
  event.init("initData")
  event.initData.wallTimeNanos = int(datetime.fromisoformat(wall).timestamp()) * 1_000_000_000 if wall else 0
  return event.to_bytes()


def write_log(root, segment="route-a--1", name="rlog", wall="2026-10-27T14:59:00+00:00"):
  directory = root / segment
  directory.mkdir(parents=True, exist_ok=True)
  data = recorded_log(wall)
  if name.endswith(".bz2"):
    data = bz2.compress(data)
  elif name.endswith(".zst"):
    import zstandard
    data = zstandard.ZstdCompressor().compress(data)
  path = directory / name
  path.write_bytes(data)
  return path, data


def store_fixture(tmp_path, endpoint="http://127.0.0.1:1"):
  logs, state = tmp_path / "logs", tmp_path / "state"
  logs.mkdir(parents=True)
  return feature.PcLogStore(logs, state, allow_test_http=True, receiver_endpoint=endpoint), None


def queue(store, selection):
  preview = store.preview(selection)
  return store.enqueue({"selection": selection, "previewId": preview["previewId"], "confirmed": True})


@pytest.mark.parametrize("name", ["rlog", "rlog.bz2", "rlog.zst"])
def test_recorded_start_day_not_receipt_or_segment_day_and_zero_segment_not_required(tmp_path, name):
  store, _ = store_fixture(tmp_path)
  write_log(store.log_root, "route-a--1", name)
  write_log(store.log_root, "route-a--2", name)
  job, _ = queue(store, {"segments": ["route-a--2", "route-a--1"]})
  info = job["routes"]["route-a"]
  assert info["dateStatus"] == "known" and info["driveDate"] == "2026-10-27"
  assert info["dateSource"] == "initData.wallTimeNanos" and isinstance(info["routeStartWallTimeNanos"], str)
  restarted = feature.PcLogStore(store.log_root, store.state_root)
  assert restarted.get_job(job["id"])["routes"] == job["routes"]


def test_kst_boundary_and_multiple_routes_are_separate(tmp_path):
  store, _ = store_fixture(tmp_path)
  write_log(store.log_root, "route-a--1", wall="2026-10-27T14:59:59+00:00")
  write_log(store.log_root, "route-b--1", wall="2026-10-27T15:00:00+00:00")
  job, _ = queue(store, {"segments": ["route-a--1", "route-b--1"]})
  assert job["routes"]["route-a"]["driveDate"] == "2026-10-27"
  assert job["routes"]["route-b"]["driveDate"] == "2026-10-28"


@pytest.mark.parametrize("kind,reason", [("zero", "invalid_wall_time"), ("corrupt", "unreadable_log"),
                                        ("conflict", "inconsistent_route_start")])
def test_unknown_date_is_explicit_and_never_uses_mtime(tmp_path, kind, reason):
  store, _ = store_fixture(tmp_path)
  path, _ = write_log(store.log_root, "route-a--1", wall=None if kind == "zero" else "2026-10-27T00:00:00+00:00")
  if kind == "corrupt":
    path.write_bytes(b"broken rlog")
  segments = ["route-a--1"]
  if kind == "conflict":
    write_log(store.log_root, "route-a--2", wall="2026-10-28T00:00:00+00:00")
    segments.append("route-a--2")
  os.utime(path, (1900000000, 1900000000))
  job, _ = queue(store, {"segments": segments})
  info = job["routes"]["route-a"]
  assert info["dateStatus"] == "unknown" and info["dateReason"] == reason
  assert info["driveDate"] is None and info["routeStartWallTimeNanos"] is None


def test_latest_closed_scope_reuses_order_and_deduplicates_selection(tmp_path):
  store, _ = store_fixture(tmp_path)
  older = "00000001--aaaaaaaaaa--0"
  latest = "00000002--bbbbbbbbbb--1"
  locked = "00000002--bbbbbbbbbb--2"
  write_log(store.log_root, older)
  write_log(store.log_root, latest)
  write_log(store.log_root, locked)
  (store.log_root / locked / "rlog.lock").touch()
  job, duplicate = queue(store, {"scope": "latest_route"})
  assert not duplicate and [item["segment"] for item in job["files"]] == [latest]
  same, duplicate = queue(store, {"segments": [latest, latest]})
  assert duplicate and same["id"] == job["id"]
  assert same["files"][0]["status"] == "pending"


@pytest.mark.parametrize("fault,code", [("missing", "source_missing"), ("locked", "source_locked"), ("modified", "source_modified")])
def test_changed_source_is_explicit_failure_without_false_ack(tmp_path, fault, code):
  store, _ = store_fixture(tmp_path)
  path, data = write_log(store.log_root)
  job, _ = queue(store, {"segments": ["route-a--1"]})
  if fault == "missing": path.unlink()
  elif fault == "locked": (path.parent / "rlog.lock").touch()
  else: path.write_bytes(data + b"changed")
  with pytest.raises(feature.TransferError, match=code):
    store.open_file(job["id"], job["files"][0]["id"])
  restarted = feature.PcLogStore(store.log_root, store.state_root)
  assert restarted.get_job(job["id"])["status"] == "failed"


def test_retry_unlock_cancel_and_traversal(tmp_path):
  store, _ = store_fixture(tmp_path)
  path, _ = write_log(store.log_root)
  job, _ = queue(store, {"segments": ["route-a--1"]})
  lock = path.parent / "rlog.lock"
  lock.touch()
  with pytest.raises(feature.TransferError): store.open_file(job["id"], job["files"][0]["id"])
  lock.unlink()
  assert store.retry(job["id"])["status"] == "queued"
  assert store.cancel(job["id"])["status"] == "canceled"
  with pytest.raises(feature.TransferError, match="job_not_transferable"):
    store.open_file(job["id"], job["files"][0]["id"])
  for segment in ["../route-a--1", "C:\\outside--1", "route/other--1", "..--1"]:
    with pytest.raises(feature.TransferError): queue(store, {"segments": [segment]})


def test_symlink_or_windows_junction_source_directory_is_rejected(tmp_path):
  store, _ = store_fixture(tmp_path)
  outside = tmp_path / "outside"
  outside.mkdir()
  (outside / "rlog").write_bytes(recorded_log())
  link = store.log_root / "route-link--1"
  if os.name == "nt":
    result = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(outside)], capture_output=True)
    assert result.returncode == 0
  else:
    link.symlink_to(outside, target_is_directory=True)
  with pytest.raises(feature.TransferError, match="unsafe_source_path"):
    queue(store, {"segments": [link.name]})


def test_full_terminal_history_prunes_only_metadata_not_source(tmp_path, monkeypatch):
  store, _ = store_fixture(tmp_path)
  monkeypatch.setattr(feature, "MAX_JOBS", 2)
  for index in range(3):
    segment = f"route-a--{index}"
    path, _ = write_log(store.log_root, segment)
    job, _ = queue(store, {"segments": [segment]})
    store.cancel(job["id"])
    assert path.exists()
  assert len(store.list_jobs()["jobs"]) == 2
  assert len(list(store.log_root.glob("*/rlog"))) == 3


@pytest.mark.parametrize("endpoint", ["http://pc.example.ts.net", "https://1.2.3.4", "https://pc.example.net",
                                       "https://pc.example.ts.net/path", "https://user@pc.example.ts.net",
                                       "https://pc.example.ts.net:444", "https://pc.example.ts.net?key=value"])
def test_destination_bounds_fail_closed(endpoint):
  with pytest.raises(feature.TransferError):
    feature.validate_endpoint(endpoint)
  with pytest.raises(feature.TransferError):
    feature.validate_endpoint("http://127.0.0.1:8766")
  assert feature.validate_endpoint(feature.RECEIVER_ENDPOINT) == feature.RECEIVER_ENDPOINT
  assert feature.validate_endpoint("http://127.0.0.1:8766", True) == "http://127.0.0.1:8766"


def test_phone_claim_fences_range_and_all_late_mutations(tmp_path, monkeypatch):
  store, _ = store_fixture(tmp_path)
  path, data = write_log(store.log_root)
  job, _ = queue(store, {"scope": "latest_route"})
  first = store.claim(job["id"], "a"*32)
  controls = {"owner": first["owner"], "epoch": first["epoch"]}
  assert first["key"] and first["deviceId"] is None and first["epoch"] == 1
  assert first["key"] not in json.dumps(store.list_jobs()) and "transport" not in json.dumps(store.list_jobs())
  with pytest.raises(feature.TransferError, match="transport_busy"):
    store.claim(job["id"], "b"*32)
  item = job["files"][0]
  part, _, offset = store.chunk(job["id"], {**controls, "fileId": item["id"], "offset": 7, "length": 12})
  assert part == data[7:19] and offset == 7
  with pytest.raises(feature.TransferError, match="invalid_range"):
    store.chunk(job["id"], {**controls, "fileId": item["id"], "offset": 0, "length": feature.CHUNK+1})
  due = store._load(job["id"])["transport"]["until"] + 1
  monkeypatch.setattr(feature.time, "time", lambda: due)
  second = store.claim(job["id"], "b"*32)
  assert second["epoch"] == 2
  before = store._path(job["id"]).read_bytes()
  for action in ["yield", "cancel", "retry", "released", "heartbeat"]:
    with pytest.raises(feature.TransferError, match="transport_stale"):
      store.transport_action(job["id"], controls, action)
  with pytest.raises(feature.TransferError, match="transport_stale"):
    store.chunk(job["id"], {**controls, "fileId": item["id"], "offset": 0, "length": 1})
  with pytest.raises(feature.TransferError, match="transport_stale"):
    store.transport_action(job["id"], {**controls, "fileId":item["id"], "size":item["size"], "sha256":item["sha256"], "receiptId":"old-receipt"}, "ack")
  assert store._path(job["id"]).read_bytes() == before and path.exists()
  restarted = feature.PcLogStore(store.log_root, store.state_root, allow_test_http=True, receiver_endpoint=store.endpoint)
  restarted.transport_action(job["id"], {"owner":"b"*32,"epoch":2}, "yield")
  assert restarted.claim(job["id"], "c"*32)["epoch"] == 3


def test_phone_bind_receipt_and_source_change_preserve_guards(tmp_path):
  store, _ = store_fixture(tmp_path)
  path, data = write_log(store.log_root)
  job, _ = queue(store, {"scope":"latest_route"})
  context = store.claim(job["id"], "a"*32)
  controls = {"owner":context["owner"],"epoch":context["epoch"]}
  bound = store.transport_action(job["id"], {**controls,"deviceId":"d"*32}, "bind")
  assert bound["deviceId"] == "d"*32
  item = job["files"][0]
  with pytest.raises(feature.TransferError, match="ack_digest_mismatch"):
    store.transport_action(job["id"], {**controls,"fileId":item["id"],"size":item["size"],"sha256":"0"*64,"receiptId":"wrong"}, "ack")
  path.write_bytes(data+b"changed")
  with pytest.raises(feature.TransferError, match="source_modified"):
    store.chunk(job["id"],{**controls,"fileId":item["id"],"offset":0,"length":1})
  failed=store.get_job(job["id"])
  assert failed["status"] == "failed" and failed["releasePending"]
  path.write_bytes(data)
  # Restored bytes have a new identity and cannot silently become the old snapshot.
  with pytest.raises(feature.TransferError, match="source_modified"):
    store.transport_action(job["id"], controls,"retry")


def test_phone_api_requires_same_origin_csrf_and_exposes_key_only_to_claim(tmp_path):
  store, _ = store_fixture(tmp_path)
  write_log(store.log_root)
  async def check():
    app=web.Application()
    assert feature.register(app,store) is store and not app.cleanup_ctx
    async with TestClient(TestServer(app)) as client:
      status=await (await client.get(feature.PREFIX+"/status")).json()
      headers={"Origin":str(client.make_url("/")).rstrip("/"),"X-Carrot-CSRF":status["csrfNonce"]}
      preview=await (await client.post(feature.PREFIX+"/preview",json={"scope":"latest_route"},headers=headers)).json()
      assert not (store.state_root/"sender.json").exists()
      response=await client.post(feature.PREFIX+"/jobs",json={"selection":{"scope":"latest_route"},"previewId":preview["previewId"],"confirmed":False},headers=headers)
      assert response.status==400 and not (store.state_root/"sender.json").exists()
      job,_=queue(store,{"scope":"latest_route"})
      url=feature.PREFIX+"/jobs/"+job["id"]+"/claim"
      for invalid in [{},{"Origin":headers["Origin"]},{**headers,"Origin":"https://foreign.invalid"}]:
        response=await client.post(url,json={"owner":"a"*32},headers=invalid)
        assert response.status==403
      claim=await (await client.post(url,json={"owner":"a"*32},headers=headers)).json()
      assert claim["key"] and claim["epoch"]==1
      public=await (await client.get(feature.PREFIX+"/status")).text()
      assert claim["key"] not in public and claim["owner"] not in public
      item=job["files"][0]
      response=await client.post(feature.PREFIX+"/jobs/"+job["id"]+"/chunk",json={"owner":claim["owner"],"epoch":claim["epoch"],"fileId":item["id"],"offset":0,"length":item["size"]},headers=headers)
      assert response.status==200 and len(await response.read())==item["size"]
      assert response.headers["X-Content-SHA256"]==item["sha256"]
  asyncio.run(check())
