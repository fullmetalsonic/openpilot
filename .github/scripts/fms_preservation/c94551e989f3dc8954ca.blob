"""Recorded SCC output cache keeps source timestamps and invalidates boundaries."""
import unittest
from pathlib import Path
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

from openpilot.selfdrive.carrot.control_advisor.log_signals import SccOutputCache


class SccOutputCacheTests(unittest.TestCase):
  @staticmethod
  def no_params(*args, **kwargs):
    raise AssertionError("recorded DBC decoding must not read live Params")

  def setUp(self):
    self.params_patch = patch.dict("sys.modules", {"openpilot.common.params": SimpleNamespace(Params=self.no_params)})
    self.params_patch.start()
    self.addCleanup(self.params_patch.stop)

  @classmethod
  def setUpClass(cls):
    with patch.dict("sys.modules", {"openpilot.common.params": SimpleNamespace(Params=cls.no_params)}):
      from opendbc.car import Bus
      from opendbc.car.hyundai.values import CAR, DBC, HyundaiFlags
      from opendbc.can.packer import CANPacker
    cls.car = CAR.KIA_SORENTO_HEV_4TH_GEN
    cls.bus = Bus.pt
    cls.packer = CANPacker(DBC[cls.car][cls.bus])
    cls.flags = int(HyundaiFlags.CANFD)

  def cp(self, *, fingerprint=None):
    return {"state": "observed", "controller_context": {"brand": "hyundai", "openpilot_longitudinal": True,
      "flags": self.flags, "hyundai_camera_scc": 1, "safety_config_count": 1,
      "car_fingerprint": str(self.car if fingerprint is None else fingerprint)}}

  def event(self, stamp, *, value=.3, frames=True, valid=True, corrupt=False, unrelated=False):
    payload = {"aReqRaw": .5, "aReqValue": value, "StopReq": 1, "JerkUpperLimit": 2., "JerkLowerLimit": 1.}
    address, data, source = self.packer.make_can_msg("SCC_CONTROL", 0, payload)
    if corrupt:
      data = data[:-1]
    records = ([{"address": address + 1 if unrelated else address, "src": source, "dat": data.hex()}]
               if frames else [])
    return {"valid": valid, "mono_ns": stamp, "data": {"frames": records}}

  def test_real_dbc_sample_survives_non_scc_batch_without_retiming(self):
    cache, cp = SccOutputCache(100), self.cp()
    cache.feed(self.event(10), cp)
    self.assertAlmostEqual(.3, cache.observe(50)["scc_a_req_value"]["value"])
    cache.feed(self.event(60, unrelated=True), cp)
    sample = cache.observe(70)["scc_a_req_value"]
    self.assertEqual(10, sample["source_mono_ns"])
    self.assertEqual("recorded_sendcan", sample["source"])
    self.assertEqual({}, cache.observe(111))
    self.assertEqual({}, cache.observe(9))

  def test_invalid_batch_timestamp_regression_and_cp_change_clear_cache(self):
    cp, cache = self.cp(), SccOutputCache(100)
    cache.feed(self.event(20), cp)
    cache.feed(self.event(30, valid=False), cp)
    self.assertEqual({}, cache.observe(31))

    cache.feed(self.event(40), cp)
    cache.feed(self.event(41, corrupt=True), cp)
    self.assertEqual({}, cache.observe(41))

    cache.feed(self.event(50), cp)
    cache.feed(self.event(49), cp)
    self.assertEqual(49, cache.observe(50)["scc_a_req_value"]["source_mono_ns"])

    cache.feed(self.event(60), cp)
    cp["controller_context"]["car_fingerprint"] = "different-recorded-car"
    cache.feed(self.event(61, frames=False), cp)
    self.assertEqual({}, cache.observe(61))

  def test_native_rlog_keeps_malformed_scc_bytes_for_strict_cache_clear(self):
    import zstandard
    from openpilot.selfdrive.carrot.control_advisor.rlog import _schema, iter_events
    _, log = _schema()
    packed = self.packer.make_can_msg("SCC_CONTROL", 0,
      {"aReqRaw": .5, "aReqValue": .3, "StopReq": 1, "JerkUpperLimit": 2., "JerkLowerLimit": 1.})
    messages = []
    for stamp, payload in ((10, packed[1]), (20, packed[1][:-1])):
      message = log.Event.new_message(logMonoTime=stamp, valid=True)
      frame = message.init("sendcan", 1)[0]
      frame.address, frame.src, frame.dat = packed[0], packed[2], payload
      messages.append(message.to_bytes())
      message.clear_write_flag()
    with tempfile.TemporaryDirectory() as folder:
      path = Path(folder) / "native.rlog.zst"
      path.write_bytes(zstandard.ZstdCompressor().compress(b"".join(messages)))
      events = list(iter_events(path, services=("sendcan",)))
    self.assertEqual([32, 31], [len(bytes.fromhex(event["data"]["frames"][0]["dat"])) for event in events])
    cache, cp = SccOutputCache(100), self.cp()
    cache.feed(events[0], cp)
    self.assertTrue(cache.observe(11))
    cache.feed(events[1], cp)
    self.assertEqual({}, cache.observe(20))


if __name__ == "__main__":
  unittest.main()
