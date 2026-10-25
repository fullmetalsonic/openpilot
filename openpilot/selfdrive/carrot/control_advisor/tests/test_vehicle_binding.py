from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from openpilot.selfdrive.carrot.control_advisor.vehicle_binding import VehicleBindings, load_bound_identity
from openpilot.selfdrive.carrot.control_advisor.vehicle_identity import describe_car_params, _schema


BOOT = "00000000-0000-0000-0000-000000000001"
NEXT_BOOT = "00000000-0000-0000-0000-000000000002"


def cp_bytes(vin="", *, steer_ratio=15.0, fingerprint="HYUNDAI_TEST"):
  message = _schema()[1].CarParams.new_message()
  message.brand, message.carFingerprint, message.carVin = "hyundai", fingerprint, vin
  message.steerRatio = steer_ratio
  return message.to_bytes()


class BindingTests(unittest.TestCase):
  def setUp(self):
    self.temp = tempfile.TemporaryDirectory()
    self.addCleanup(self.temp.cleanup)
    self.root = Path(self.temp.name) / "bindings"
    self.bindings = VehicleBindings(self.root, BOOT)
    self.addCleanup(self.bindings.close)

  def test_unknown_vin_requires_confirmation_each_boot_and_preserves_group(self):
    descriptor = describe_car_params(cp_bytes())
    self.assertIsNone(self.bindings.resolve(descriptor)["physical_group_id"])
    confirmed = self.bindings.confirm(descriptor)
    group = confirmed["physical_group_id"]
    self.assertEqual(load_bound_identity(descriptor, self.root, BOOT)["physical_group_id"], group)
    self.assertIsNone(load_bound_identity(descriptor, self.root, NEXT_BOOT)["physical_group_id"])
    self.bindings.close()
    reopened = VehicleBindings(self.root, NEXT_BOOT)
    self.addCleanup(reopened.close)
    with self.assertRaisesRegex(ValueError, "selection_required"):
      reopened.confirm(descriptor)
    self.assertEqual(len(reopened.groups()), 1)
    self.assertEqual(reopened.confirm(descriptor, group)["physical_group_id"], group)

  def test_config_change_keeps_physical_group_and_does_not_mutate_registry(self):
    old = self.bindings.confirm(describe_car_params(cp_bytes("KMH01234567890123")))
    data = self.bindings.path.read_bytes()
    new = self.bindings.resolve(describe_car_params(cp_bytes("KMH01234567890123", steer_ratio=16)))
    self.assertEqual(new["physical_group_id"], old["physical_group_id"])
    self.assertNotEqual(new["config_sha256"], old["config_sha256"])
    self.assertEqual(self.bindings.path.read_bytes(), data)
    self.assertEqual(load_bound_identity(new, self.root, NEXT_BOOT)["physical_group_id"], old["physical_group_id"])

  def test_new_vin_can_attach_to_existing_confirmed_group_without_new_baseline(self):
    old = self.bindings.confirm(describe_car_params(cp_bytes()))
    known = describe_car_params(cp_bytes("KMH01234567890123"))
    self.assertIsNone(self.bindings.resolve(known)["physical_group_id"])
    new = self.bindings.confirm(known, old["physical_group_id"])
    self.assertEqual(new["physical_group_id"], old["physical_group_id"])
    self.assertEqual(len(self.bindings.groups()), 1)
    self.assertNotIn("KMH01234567890123", self.bindings.path.read_text())

  def test_other_vin_and_fingerprint_cannot_reuse_group(self):
    old = self.bindings.confirm(describe_car_params(cp_bytes("KMH01234567890123")))
    for raw in (cp_bytes("KMH01234567890124"), cp_bytes("KMH01234567890123", fingerprint="OTHER")):
      with self.assertRaisesRegex(ValueError, "mismatch"):
        self.bindings.confirm(describe_car_params(raw), old["physical_group_id"])

  def test_tampered_registry_fails_instead_of_overwriting(self):
    self.bindings.path.write_text("{}")
    with self.assertRaises(ValueError):
      self.bindings.confirm(describe_car_params(cp_bytes()))
    self.assertEqual(self.bindings.path.read_text(), "{}")

  def test_uncertain_write_revokes_in_memory_binding(self):
    descriptor = describe_car_params(cp_bytes())
    with patch("openpilot.selfdrive.carrot.control_advisor.vehicle_binding._atomic", side_effect=OSError("fsync")):
      with self.assertRaises(OSError):
        self.bindings.confirm(descriptor)
    with self.assertRaisesRegex(ValueError, "closed"):
      self.bindings.resolve(descriptor)

  def test_read_missing_registry_does_not_create_directories(self):
    root = self.root.parent / "missing"
    self.assertIsNone(load_bound_identity(describe_car_params(cp_bytes()), root, BOOT)["physical_group_id"])
    self.assertFalse(root.exists())


if __name__ == "__main__":
  unittest.main()
