from copy import deepcopy
import math
from types import SimpleNamespace
import unittest

import numpy as np

from opendbc.car.vehicle_model import VehicleModel
from openpilot.common.transformations.orientation import rot_from_euler
from openpilot.selfdrive.locationd.helpers import Measurement, Pose, PoseCalibrator
from openpilot.selfdrive.carrot.control_advisor.lateral_observations import LateralObservationBuilder


NS = 1_000_000_000
CP = {"mass": 1600., "rotationalInertia": 2700., "wheelbase": 2.8, "centerToFront": 1.2,
      "steerRatioRear": .03, "tireStiffnessFront": 85000., "tireStiffnessRear": 90000., "steerRatio": 15.5}
RPY = [.12, -.17, .31]
ANGULAR = [.03, -.02, .2]
RESPONSE = 5_050_000_000
ANCHOR = 5_100_000_000


def event(service, time, data, *, valid=True, ordered=True):
  return {"service": service, "mono_ns": time, "valid": valid, "service_time_ordered": ordered, "data": data}


def source_cp():
  return {"schema_version": 1, "source_id": "synthetic-log-sha", "epoch_id": "synthetic-epoch",
          "config_sha256": "a" * 64, "mono_ns": 0, "values": dict(CP)}


def fixture_events():
  result = []
  for tick in range(20, 52):
    time = tick * 100_000_000
    seconds = time / NS
    result.extend((event("carControl", time, {"latActive": True}),
                   event("carState", time, {"vEgo": 10 + seconds, "steeringAngleDeg": 5 + .4 * seconds, "steeringPressed": False}),
                   event("carOutput", time, {"actuatorsOutput": {"torque": .01 * seconds}})))
  result.extend((event("liveCalibration", 5 * NS, {"calStatus": "calibrated", "rpyCalib": list(RPY)}),
                 event("liveDelay", 5 * NS, {"status": "estimated", "validBlocks": 10, "lateralDelay": .2}),
                 event("liveParameters", 5 * NS, {"valid": True, "sensorValid": True, "posenetValid": True,
                       "steerRatio": 16., "stiffnessFactor": 1.15, "angleOffsetDeg": .3, "roll": .06,
                       "angleOffsetValid": True, "steerRatioValid": True, "stiffnessFactorValid": True}),
                 event("livePose", ANCHOR, {"timestamp": RESPONSE, "inputsOK": True, "sensorsOK": True, "posenetOK": True,
                       "angularVelocityDevice": dict(zip(("x", "y", "z", "valid"), (*ANGULAR, True))),
                       "orientationNED": {"x": .04, "y": -.02, "z": .01, "valid": True}})))
  return result


def builder(events=None, *, buffer=1000):
  result = LateralObservationBuilder(source_cp(), max_age_ns=500_000_000,
                                     max_alignment_ns=500_000_000, max_buffer_events=buffer)
  for item in fixture_events() if events is None else events:
    result.feed(item)
  return result


class TestLateralObservations(unittest.TestCase):
  def test_native_math_parity_and_distinct_timestamps(self):
    result = builder().observe(ANCHOR)
    self.assertTrue(all(value["available"] for value in result["availability"].values()), result)
    signal = result["signals"]
    calibrator = PoseCalibrator()
    calibrator.calib_valid = True
    calibrator.calib_from_device = rot_from_euler(RPY).T
    measurement = lambda xyz: Measurement(np.array(xyz), np.zeros(3))
    pose = Pose(measurement([.04, -.02, .01]), measurement([0., 0., 0.]), measurement([0., 0., 0.]), measurement(ANGULAR))
    yaw = float(calibrator.build_calibrated_pose(pose).angular_velocity.yaw)
    self.assertAlmostEqual(signal["yaw_rate_calibrated"]["value"], yaw, places=14)
    self.assertAlmostEqual(signal["sent_torque"]["value"], -.0485)
    self.assertAlmostEqual(signal["lateral_accel_independent"]["value"], 14.85 * yaw - math.sin(.04) * 9.81)
    self.assertAlmostEqual(signal["curvature_independent"]["value"], yaw / 15.05)
    model = VehicleModel(SimpleNamespace(**CP))
    model.update_params(1.15, 16.)
    self.assertAlmostEqual(signal["vm_curvature_factor"]["value"], model.curvature_factor(15.05), places=14)
    self.assertAlmostEqual(signal["roll_curvature"]["value"], model.roll_compensation(.06, 15.05), places=14)
    self.assertAlmostEqual(signal["steering_angle_corrected"]["value"], math.radians(5 + .4 * 5.05 - .3))
    self.assertEqual(signal["sent_torque"]["source_mono_ns"], 4_900_000_000)
    self.assertEqual(signal["lateral_accel_independent"]["source_mono_ns"], RESPONSE)
    self.assertEqual(result["provenance"]["pose_event_mono_ns"], ANCHOR)
    self.assertEqual(result["provenance"]["response_mono_ns"], RESPONSE)
    self.assertEqual(result["provenance"]["alignment"], {"kind": "recorded_lateral_delay", "delay_ns": 200_000_000})
    self.assertNotIn("lateral_accel_offset", signal)
    self.assertFalse(result["provenance"]["consumer_mapping_verified"])
    self.assertFalse(result["provenance"]["parameter_application_ack"])

  def test_unavailable_families_do_not_suppress_other_measurements(self):
    for omitted, unavailable, available in (("liveDelay", "torque_response", "geometry"),
                                            ("liveParameters", "geometry", "torque_response")):
      result = builder([item for item in fixture_events() if item["service"] != omitted]).observe(ANCHOR)
      self.assertFalse(result["availability"][unavailable]["available"])
      self.assertTrue(result["availability"][available]["available"])
    items = fixture_events()
    items[-1]["data"]["angularVelocityDevice"]["valid"] = False
    self.assertFalse(any(part["available"] for part in builder(items).observe(ANCHOR)["availability"].values()))

  def test_no_future_support_or_endpoint_extrapolation(self):
    items = [item for item in fixture_events() if not (item["service"] == "carOutput" and item["mono_ns"] >= 4_900_000_000)]
    result = builder(items).observe(ANCHOR)
    self.assertFalse(result["availability"]["torque_response"]["available"])
    self.assertIn("carOutput_alignment_support_missing", result["reason_codes"])
    self.assertTrue(result["availability"]["geometry"]["available"])
    # Right speed/angle support exists in the buffer, but its native time is beyond this anchor.
    items = fixture_events()
    items[-1]["mono_ns"] = RESPONSE
    result = builder(items).observe(RESPONSE)
    self.assertFalse(result["availability"]["geometry"]["available"])
    self.assertTrue(result["availability"]["torque_response"]["available"])

  def test_exact_alignment_endpoints(self):
    items = fixture_events()
    items[-3]["data"]["lateralDelay"] = .25
    result = builder(items).observe(ANCHOR)
    self.assertTrue(result["availability"]["torque_response"]["available"])
    self.assertAlmostEqual(result["signals"]["sent_torque"]["value"], -.048)
    self.assertEqual(result["signals"]["sent_torque"]["source_mono_ns"], 4_800_000_000)

  def test_invalid_events_and_regressions_are_not_bridged(self):
    for mode in ("invalid", "regression", "nan", "bool"):
      items = fixture_events()
      target = next(item for item in items if item["service"] == "carOutput" and item["mono_ns"] == 4_800_000_000)
      if mode == "invalid":
        target["valid"] = False
      elif mode == "regression":
        target["service_time_ordered"] = False
      else:
        target["data"]["actuatorsOutput"]["torque"] = float("nan") if mode == "nan" else True
      with self.subTest(mode=mode):
        result = builder(items).observe(ANCHOR)
        self.assertFalse(result["availability"]["torque_response"]["available"])
        self.assertNotIn("sent_torque", result["signals"])
        self.assertTrue(result["availability"]["geometry"]["available"])

  def test_engagement_override_gap_and_insufficient_recovery(self):
    for mode in ("override", "inactive", "missing", "invalid", "gap", "lag_margin"):
      items = fixture_events()
      if mode == "missing":
        items = [item for item in items if item["mono_ns"] >= 4 * NS]
      elif mode == "gap":
        items = [item for item in items if not (item["service"] == "carControl" and 3 * NS < item["mono_ns"] < 4 * NS)]
      else:
        service = "carState" if mode == "override" else "carControl"
        stamp = 2_800_000_000 if mode == "lag_margin" else 4 * NS
        target = next(item for item in items if item["service"] == service and item["mono_ns"] == stamp)
        if mode == "invalid":
          target["valid"] = False
        else:
          target["data"]["steeringPressed" if mode == "override" else "latActive"] = mode == "override"
      with self.subTest(mode=mode):
        self.assertFalse(builder(items).observe(ANCHOR)["availability"]["torque_response"]["available"])

  def test_delay_validation(self):
    for field, value in (("status", "unestimated"), ("validBlocks", 0), ("lateralDelay", -.1),
                         ("lateralDelay", 2.), ("lateralDelay", float("inf")), ("lateralDelay", True)):
      items = fixture_events()
      items[-3]["data"][field] = value
      with self.subTest(field=field, value=value):
        result = builder(items).observe(ANCHOR)
        self.assertFalse(result["availability"]["torque_response"]["available"])
        self.assertTrue(result["availability"]["geometry"]["available"])

  def test_pose_calibration_freshness_and_shape(self):
    for mode in ("missing", "uncalibrated", "shape", "nan", "stale", "future_pose", "inputs"):
      items = fixture_events()
      if mode == "missing":
        items = [item for item in items if item["service"] != "liveCalibration"]
      elif mode == "uncalibrated":
        items[-4]["data"]["calStatus"] = "uncalibrated"
      elif mode == "shape":
        items[-4]["data"]["rpyCalib"] = [0., 0.]
      elif mode == "nan":
        items[-4]["data"]["rpyCalib"][1] = float("nan")
      elif mode == "stale":
        items[-4]["mono_ns"] = NS
      elif mode == "future_pose":
        items[-1]["data"]["timestamp"] = ANCHOR + 1
      else:
        items[-1]["data"]["sensorsOK"] = False
      with self.subTest(mode=mode):
        self.assertFalse(builder(items).observe(ANCHOR)["availability"]["pose"]["available"])
    self.assertFalse(builder().observe(ANCHOR + NS)["availability"]["pose"]["available"])

  def test_cp_binding_change_historical_anchor_and_input_isolation(self):
    supplied, items = source_cp(), fixture_events()
    original = deepcopy(items)
    result = LateralObservationBuilder(supplied, max_age_ns=500_000_000, max_alignment_ns=500_000_000, max_buffer_events=1000)
    for item in items:
      result.feed(item)
    expected = result.observe(ANCHOR)
    supplied["values"]["mass"] = 9999
    items[-1]["data"]["orientationNED"]["x"] = 3
    self.assertEqual(result.observe(ANCHOR), expected)
    self.assertEqual(original[-1]["data"]["orientationNED"]["x"], .04)
    result.feed(event("carParams", ANCHOR + 1, dict(CP)))
    self.assertEqual(result.observe(ANCHOR), expected)
    changed = {**CP, "mass": CP["mass"] + 1}
    result.feed(event("carParams", ANCHOR + 2, changed))
    self.assertEqual(result.observe(ANCHOR), expected)
    self.assertIn("recorded_cp_changed", result.observe(ANCHOR + 2)["reason_codes"])
    result.feed(event("livePose", ANCHOR + 3, {}, valid=False))
    self.assertEqual(result.observe(ANCHOR), expected)

  def test_bounded_stream_does_not_permanently_reject_new_data(self):
    result = builder([], buffer=120)
    for index in range(1000):
      result.feed(event("carOutput", index, {"actuatorsOutput": {"torque": .1}}))
    self.assertEqual(result.buffer_count, 120)
    for item in fixture_events():
      result.feed(item)
    self.assertEqual(result.buffer_count, 120)
    self.assertTrue(result.observe(ANCHOR)["availability"]["torque_response"]["available"])
    self.assertIn("livePose_missing", result.observe(1000)["reason_codes"])

  def test_constructor_and_nonfinite_geometry(self):
    for change in ({"values": {}}, {"config_sha256": "not-a-hash"}, {"values": {**CP, "wheelbase": 0}},
                   {"values": {**CP, "mass": True}}):
      with self.subTest(change=change), self.assertRaises(ValueError):
        LateralObservationBuilder({**source_cp(), **change}, max_age_ns=NS, max_alignment_ns=NS, max_buffer_events=100)
    for key, value in (("stiffnessFactor", 0), ("steerRatio", True), ("roll", float("nan")), ("angleOffsetValid", False)):
      items = fixture_events()
      items[-2]["data"][key] = value
      with self.subTest(key=key):
        result = builder(items).observe(ANCHOR)
        self.assertFalse(result["availability"]["geometry"]["available"])
        self.assertTrue(result["availability"]["torque_response"]["available"])


if __name__ == "__main__":
  unittest.main()
