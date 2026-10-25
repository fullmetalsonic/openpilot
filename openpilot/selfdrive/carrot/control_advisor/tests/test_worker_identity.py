"""Actual captured CP payloads; explicit generated data and no device Params."""
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from openpilot.selfdrive.carrot.control_advisor import consumer_observer as producer
from openpilot.selfdrive.carrot.control_advisor.vehicle_identity import _schema, describe_car_params
from openpilot.selfdrive.carrot.control_advisor.vehicle_binding import VehicleBindings, binding_root
from openpilot.selfdrive.carrot.control_advisor.worker import _recording_identity_provider
from openpilot.selfdrive.carrot.control_advisor.tests.test_vehicle_identity import car_params_fixture


BOOT = "00000000-0000-0000-0000-000000000001"


class WorkerIdentityTests(unittest.TestCase):
  def test_worker_current_capture_reuses_explicit_physical_binding(self):
    with tempfile.TemporaryDirectory() as directory:
      request_root = Path(directory) / "requests"
      raw = car_params_fixture(vin="1HGCM82633A004352")
      binding = VehicleBindings(binding_root(request_root), BOOT)
      self.addCleanup(binding.close)
      confirmed = binding.confirm(describe_car_params(raw))
      class Params:
        def get_many_raw(self, keys):
          if keys != ["CarParams"]:
            raise AssertionError("unexpected read")
          return {"CarParams": raw}
      provider = _recording_identity_provider(environment={"CARROT_CONTROL_ADVISOR_REQUEST_ROOT": str(request_root)}, params=Params(), boot_id=BOOT)
      current = provider()
      self.assertEqual(current, confirmed)
      self.assertEqual(current["raw_sha256"], describe_car_params(raw)["raw_sha256"])
      binding.close()

  def test_non_native_off_has_no_parameter_construction(self):
    with patch.object(producer, "_native_runtime", return_value=False), patch.dict("sys.modules", {"openpilot.common.params": None}):
      self.assertIsNone(_recording_identity_provider(environment={}))
    with patch.dict("os.environ", {"CARROT_CONTROL_ADVISOR_CONSUMER_OBSERVATIONS": "0"}), \
         patch.object(producer, "_read_boot_id", side_effect=AssertionError("OFF identity probe")):
      self.assertIsNone(producer.create_observer("controls.torque", car_params=object()))

  def test_used_cp_is_captured_once_before_later_mutation_and_actual_wire_validates(self):
    _, schema, _ = _schema()
    cp = schema.CarParams.new_message()
    cp.carFingerprint, cp.mass = "GENERATED_USED_CP_FIXTURE", 1500.0
    expected = describe_car_params(cp.as_reader().as_builder().to_bytes())
    sent = []
    class Sender:
      def send(self, raw):
        sent.append(raw)
      def close(self):
        pass
    paths = {producer.OBSERVER_PATH: "a" * 64, producer.CONSUMER_PATHS["controls.torque"]: "b" * 64}
    pump = producer._Pump(sender=Sender(), identity={"boot_id": BOOT, "pid": 1, "process_instance_id": "c" * 32}, hash_provider=lambda _: paths)
    try:
      observer = producer._Observer("controls.torque", pump, clock_ns=lambda: 10_000_000_000, car_params=cp)
      cp.mass = 1800.0
      producer.observe(observer, lambda: {"fields": {"LateralTorqueFriction": producer.field(.3, read_value=.3, source="stored")}, "context": {"active": True}})
      deadline = time.monotonic() + 2
      while not sent and time.monotonic() < deadline:
        time.sleep(.005)
      self.assertTrue(sent, "actual background sender did not publish")
      event = producer.validate_event(json.loads(sent[0]))
      self.assertEqual(event["vehicle_identity"], expected)
      self.assertNotEqual(event["vehicle_identity"]["config_sha256"], describe_car_params(cp.as_reader().as_builder().to_bytes())["config_sha256"])
      self.assertIsNone(event["vehicle_identity"]["physical_group_id"])
      self.assertNotIn("carVin", json.dumps(event))
    finally:
      pump.close()


if __name__ == "__main__":
  unittest.main()
