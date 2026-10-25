import errno
import multiprocessing
import os
import stat
import tempfile
import pytest

from openpilot.common.params import Params, UnknownKeyName


@pytest.fixture
def temp_params_dir():
  with tempfile.TemporaryDirectory() as tmpdir:
    yield tmpdir


def test_raw_distinction_missing_empty_binary(temp_params_dir):
  params = Params(temp_params_dir)

  # 1) Initially missing key -> None
  assert params.get_raw("DongleId") is None
  snapshot = params.get_many_raw(["DongleId", "CarBatteryCapacity"])
  assert snapshot == {"DongleId": None, "CarBatteryCapacity": None}

  # 2) CAS missing -> empty file (b"")
  res = params.compare_and_set_raw("DongleId", b"", {"DongleId": None})
  assert res == {"state": "stored", "errno": 0, "changed": True}

  raw_val = params.get_raw("DongleId")
  assert raw_val is not None
  assert raw_val == b""
  assert params.get_many_raw(["DongleId"]) == {"DongleId": b""}

  # 3) CAS empty -> binary data containing embedded NUL and arbitrary bytes
  binary_payload = b"\x00\x01\x02\xff\xfe\x00\x42\xaa\x55\x00"
  res = params.compare_and_set_raw("DongleId", binary_payload, {"DongleId": b""})
  assert res == {"state": "stored", "errno": 0, "changed": True}

  raw_val = params.get_raw("DongleId")
  assert raw_val == binary_payload
  assert params.get_many_raw(["DongleId"]) == {"DongleId": binary_payload}


def test_multi_key_snapshot(temp_params_dir):
  params = Params(temp_params_dir)

  # Prepare diverse key states:
  # DongleId: binary bytes
  # CarBatteryCapacity: empty bytes (b"")
  # CalibrationParams: absent (missing)
  res1 = params.compare_and_set_raw("DongleId", b"dongle_123\x00abc", {"DongleId": None})
  assert res1["state"] == "stored"
  res2 = params.compare_and_set_raw("CarBatteryCapacity", b"", {"CarBatteryCapacity": None})
  assert res2["state"] == "stored"

  keys = ["DongleId", "CarBatteryCapacity", "CalibrationParams"]
  snapshot = params.get_many_raw(keys)
  assert snapshot == {
    "DongleId": b"dongle_123\x00abc",
    "CarBatteryCapacity": b"",
    "CalibrationParams": None,
  }

  # Tuple keys input supported
  snapshot_tuple = params.get_many_raw(tuple(keys))
  assert snapshot_tuple == snapshot


def test_conditional_deletion(temp_params_dir):
  params = Params(temp_params_dir)

  # Create param
  res = params.compare_and_set_raw("DongleId", b"active_val", {"DongleId": None})
  assert res == {"state": "stored", "errno": 0, "changed": True}
  assert params.get_raw("DongleId") == b"active_val"

  # Conditional delete with value=None
  res_del = params.compare_and_set_raw("DongleId", None, {"DongleId": b"active_val"})
  assert res_del == {"state": "stored", "errno": 0, "changed": True}
  assert params.get_raw("DongleId") is None


def test_cas_no_op_stored_false(temp_params_dir):
  params = Params(temp_params_dir)

  # 1) Missing -> Missing no-op
  res_missing = params.compare_and_set_raw("DongleId", None, {"DongleId": None})
  assert res_missing == {"state": "stored", "errno": 0, "changed": False}
  assert params.get_raw("DongleId") is None

  # 2) Existing value -> Same value no-op
  res_init = params.compare_and_set_raw("DongleId", b"same_content", {"DongleId": None})
  assert res_init == {"state": "stored", "errno": 0, "changed": True}

  res_same = params.compare_and_set_raw("DongleId", b"same_content", {"DongleId": b"same_content"})
  assert res_same == {"state": "stored", "errno": 0, "changed": False}
  assert params.get_raw("DongleId") == b"same_content"


def test_target_and_dependency_conflicts(temp_params_dir):
  params = Params(temp_params_dir)

  # Target conflict: actual != expected
  params.compare_and_set_raw("DongleId", b"actual_dongle", {"DongleId": None})
  res_conflict = params.compare_and_set_raw("DongleId", b"new_dongle", {"DongleId": b"wrong_expected"})
  assert res_conflict == {"state": "conflict", "errno": 0, "changed": False}
  assert params.get_raw("DongleId") == b"actual_dongle"

  # Dependency conflict: target expected matches, but multi-key dependency mismatches
  params.compare_and_set_raw("CarBatteryCapacity", b"85", {"CarBatteryCapacity": None})

  expected_dep_conflict = {
    "DongleId": b"actual_dongle",
    "CarBatteryCapacity": b"999",  # Actual is 85
  }
  res_dep = params.compare_and_set_raw("DongleId", b"mutated", expected_dep_conflict)
  assert res_dep == {"state": "conflict", "errno": 0, "changed": False}
  assert params.get_raw("DongleId") == b"actual_dongle"

  # Missing key conflict: key absent on disk but expected bytes
  res_absent_conflict = params.compare_and_set_raw("CalibrationParams", b"val", {"CalibrationParams": b"present"})
  assert res_absent_conflict == {"state": "conflict", "errno": 0, "changed": False}
  assert params.get_raw("CalibrationParams") is None


def test_validation_zero_writes(temp_params_dir):
  params = Params(temp_params_dir)

  # 1) Non-string key
  with pytest.raises(TypeError):
    params.compare_and_set_raw(12345, b"data", {12345: None})
  with pytest.raises(TypeError):
    params.get_raw(12345)
  with pytest.raises(TypeError):
    params.get_many_raw([12345])
  with pytest.raises(TypeError):
    params.get_many_raw("DongleId")  # string instead of list/tuple

  # 2) Non-bytes value
  with pytest.raises(TypeError):
    params.compare_and_set_raw("DongleId", "string_value", {"DongleId": None})
  with pytest.raises(TypeError):
    params.compare_and_set_raw("DongleId", 123, {"DongleId": None})
  with pytest.raises(TypeError):
    params.compare_and_set_raw("DongleId", bytearray(b"data"), {"DongleId": None})

  # 3) Bad expected structure
  with pytest.raises(TypeError):
    params.compare_and_set_raw("DongleId", b"data", ["DongleId"])
  with pytest.raises(ValueError):
    params.compare_and_set_raw("DongleId", b"data", {"CarBatteryCapacity": None})

  # 4) Bad types in expected mapping
  with pytest.raises(TypeError):
    params.compare_and_set_raw("DongleId", b"data", {"DongleId": "string_exp"})
  with pytest.raises(TypeError):
    params.compare_and_set_raw("DongleId", b"data", {"DongleId": None, 42: None})
  with pytest.raises(TypeError):
    params.compare_and_set_raw("DongleId", b"data", {"DongleId": bytearray(b"old")})

  # 5) Unknown key rejection
  with pytest.raises(UnknownKeyName):
    params.compare_and_set_raw("UnknownUnregisteredKeyXYZ", b"val", {"UnknownUnregisteredKeyXYZ": None})
  with pytest.raises(UnknownKeyName):
    params.compare_and_set_raw("DongleId", b"val", {"DongleId": None, "UnknownKeyXYZ": None})
  with pytest.raises(UnknownKeyName):
    params.get_raw("UnknownUnregisteredKeyXYZ")
  with pytest.raises(UnknownKeyName):
    params.get_many_raw(["DongleId", "UnknownUnregisteredKeyXYZ"])

  # 6) Embedded NUL and path escape rejection via key registry check
  with pytest.raises(UnknownKeyName):
    params.compare_and_set_raw("DongleId\0escape", b"val", {"DongleId\0escape": None})
  with pytest.raises(UnknownKeyName):
    params.get_raw("DongleId\0escape")
  with pytest.raises(UnknownKeyName):
    params.get_many_raw(["DongleId\0escape"])
  with pytest.raises(UnknownKeyName):
    params.compare_and_set_raw("../etc/passwd", b"val", {"../etc/passwd": None})

  # Verify zero writes occurred
  assert params.get_raw("DongleId") is None


def test_symlink_and_nonregular_rejection(temp_params_dir):
  params = Params(temp_params_dir)
  param_path = params.get_param_path("DongleId")
  target_file = os.path.join(temp_params_dir, "symlink_target_file")
  with open(target_file, "wb") as f:
    f.write(b"target_content")

  # Key path as symlink
  os.symlink(target_file, param_path)
  try:
    # get_raw must NOT report missing (None); it must preserve errno and raise OSError (ELOOP)
    with pytest.raises(OSError) as exc_info:
      params.get_raw("DongleId")
    assert exc_info.value.errno in (errno.ELOOP, errno.EINVAL, errno.EPERM)

    # CAS must fail before mutation
    res = params.compare_and_set_raw("DongleId", b"new_val", {"DongleId": None})
    assert res["state"] == "failed"
    assert res["changed"] is False
    assert res["errno"] > 0
  finally:
    os.unlink(param_path)

  # Key path as directory (non-regular)
  os.mkdir(param_path)
  try:
    with pytest.raises(OSError) as exc_info:
      params.get_raw("DongleId")
    assert exc_info.value.errno in (errno.EISDIR, errno.EINVAL)

    res = params.compare_and_set_raw("DongleId", b"new_val", {"DongleId": None})
    assert res["state"] == "failed"
    assert res["changed"] is False
    assert res["errno"] > 0
  finally:
    os.rmdir(param_path)

  # A FIFO must be rejected before a blocking open could wait for a writer.
  os.mkfifo(param_path)
  try:
    with pytest.raises(OSError) as exc_info:
      params.get_raw("DongleId")
    assert exc_info.value.errno == errno.EINVAL
    assert params.compare_and_set_raw("DongleId", b"new", {"DongleId": None}) == {
      "state": "failed", "errno": errno.EINVAL, "changed": False}
  finally:
    os.unlink(param_path)


def test_lock_failure_handling(temp_params_dir):
  params = Params(temp_params_dir)
  lock_path = os.path.join(temp_params_dir, ".lock")

  # Create .lock as a directory to force open(O_CREAT) failure with EISDIR
  os.mkdir(lock_path)
  try:
    res = params.compare_and_set_raw("DongleId", b"val", {"DongleId": None})
    assert res["state"] == "failed"
    assert res["changed"] is False
    assert res["errno"] in (errno.EISDIR, errno.EACCES)

    with pytest.raises(OSError) as exc_info:
      params.get_raw("DongleId")
    assert exc_info.value.errno in (errno.EISDIR, errno.EACCES)
  finally:
    os.rmdir(lock_path)


def _concurrency_put_worker(params_dir, key, val, barrier, res_queue):
  p = Params(params_dir)
  barrier.wait()
  p.put(key, val)
  res_queue.put(("put", val))


def _concurrency_cas_worker(params_dir, key, new_val, expected_val, barrier, res_queue):
  p = Params(params_dir)
  barrier.wait()
  res = p.compare_and_set_raw(key, new_val, {key: expected_val})
  res_queue.put(("cas", res))


def test_process_concurrency_with_put(temp_params_dir):
  # Verify separate process concurrency with existing Params.put to prove .lock participation
  # and guarantee no stale overwrite without relying solely on arbitrary sleeps.
  key = "CarBatteryCapacity"
  params = Params(temp_params_dir)
  params.put(key, 10)
  assert params.get_raw(key) == b"10"

  barrier = multiprocessing.Barrier(2)
  res_queue = multiprocessing.Queue()

  proc_put = multiprocessing.Process(
    target=_concurrency_put_worker,
    args=(temp_params_dir, key, 20, barrier, res_queue)
  )
  proc_cas = multiprocessing.Process(
    target=_concurrency_cas_worker,
    args=(temp_params_dir, key, b"30", b"10", barrier, res_queue)
  )

  proc_put.start()
  proc_cas.start()
  proc_put.join(timeout=10)
  proc_cas.join(timeout=10)

  assert not proc_put.is_alive()
  assert not proc_cas.is_alive()

  assert proc_put.exitcode == 0 and proc_cas.exitcode == 0
  results = {}
  for _ in range(2):
    tag, val = res_queue.get(timeout=5)
    results[tag] = val

  cas_res = results["cas"]
  final_val = params.get_raw(key)

  # Both operations participated in .lock:
  # - If CAS won the lock race, CAS stored 30, and then put wrote 20 -> final_val is b"20"
  # - If put won the lock race, put wrote 20, and CAS saw expected mismatch (10 != 20) -> CAS conflict, final_val is b"20"
  # In no scenario may a stale overwrite leave final_val as b"10" or corrupted.
  if cas_res["state"] == "stored":
    assert cas_res["changed"] is True
    assert final_val == b"20"
  else:
    assert cas_res["state"] == "conflict"
    assert cas_res["changed"] is False
    assert final_val == b"20"

  # Multi-cycle linearizability validation with concurrent CAS updates
  for i in range(5):
    cur = params.get_raw(key)
    nxt = str(100 + i).encode()
    res = params.compare_and_set_raw(key, nxt, {key: cur})
    assert res == {"state": "stored", "errno": 0, "changed": True}
    assert params.get_raw(key) == nxt
