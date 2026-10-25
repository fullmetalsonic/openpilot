"""Parent receipt aging and lifecycle handling for parking observations."""
import asyncio
from copy import deepcopy
import unittest

from openpilot.selfdrive.carrot.control_advisor.session import SessionController
from openpilot.selfdrive.carrot.control_advisor.parking import PROFILE_ID
from openpilot.selfdrive.carrot.control_advisor.tests.test_session import FakeProcess


PARK = {"state": "park_observed", "reason": "parking_park_observed", "observed_mono_ns": 42,
        "offroad_state": "onroad_observed", "source_kind": "native_event", "profile_id": PROFILE_ID}


class ParkingSessionTests(unittest.IsolatedAsyncioTestCase):
  async def controller(self, close_exits=True):
    self.now, self.processes = 100., []
    async def spawn(*_, **__):
      process = FakeProcess(close_exits=close_exits)
      self.processes.append(process)
      return process
    async def stopper(process): process.finish(-9)
    controller = SessionController(clock=lambda: self.now, spawn=spawn, stopper=stopper, graceful_timeout=.01)
    self.addAsyncCleanup(controller.close)
    return controller

  def accept(self, controller, session, **changes):
    payload = {"schema_version": 1, "session_id": session["session_id"], "source_mode": session["source_mode"],
      "state": "running", "parking": deepcopy(PARK), "last_park_observation": deepcopy(PARK), "parking_confirmed": True,
      "history": [], **changes}
    controller._accept(payload, controller._generation)
    return controller.status()

  async def test_repeated_heartbeat_not_fresh_parking_and_duplicate_cache_ages(self):
    controller = await self.controller()
    session = await controller.start(mode="live", interval_seconds=180, request_id="parking")
    self.assertTrue(self.accept(controller, session)["parking_confirmed"])
    self.now += 2.01
    self.accept(controller, session)
    current = controller.status()
    self.assertFalse(current["parking_confirmed"])
    self.assertEqual("park_observed", current["last_park_observation"]["state"])
    duplicate = await controller.start(mode="live", interval_seconds=180, request_id="parking")
    self.assertFalse(duplicate["parking_confirmed"])
    fresh = {**PARK, "observed_mono_ns": 43}
    self.assertTrue(self.accept(controller, session, parking=fresh, last_park_observation=fresh)["parking_confirmed"])

  async def test_eof_and_forced_stop_clear_current_but_preserve_historical(self):
    for forced in (False, True):
      controller = await self.controller(close_exits=not forced)
      session = await controller.start(mode="live", interval_seconds=180, request_id="forced" if forced else "eof")
      self.accept(controller, session)
      if forced:
        await controller.stop(session["session_id"])
      else:
        self.processes[-1].finish(1)
        for _ in range(100):
          if controller.status()["state"] == "failed": break
          await asyncio.sleep(.001)
      result = controller.status()
      self.assertFalse(result["parking_confirmed"])
      self.assertEqual("unknown", result["parking"]["state"])
      self.assertEqual(PARK, result["last_park_observation"])
      duplicate = await controller.start(mode="live", interval_seconds=180, request_id="forced" if forced else "eof")
      self.assertFalse(duplicate["parking_confirmed"])

  async def test_old_request_cannot_be_current_in_new_session(self):
    controller = await self.controller()
    first = await controller.start(mode="live", interval_seconds=180, request_id="first")
    self.accept(controller, first)
    await controller.stop(first["session_id"])
    await controller.start(mode="live", interval_seconds=180, request_id="second")
    old = await controller.start(mode="live", interval_seconds=180, request_id="first")
    self.assertFalse(old["parking_confirmed"])
    self.assertEqual(PARK, old["last_park_observation"])

  async def test_unbound_forged_or_wrong_source_claims_rejected_legacy_false_is_unknown(self):
    controller = await self.controller()
    session = await controller.start(mode="live", interval_seconds=180, request_id="invalid")
    for changes in ({"parking": None}, {"parking": {**PARK, "source_kind": "synthetic_event"}},
                    {"parking_confirmed": False}, {"state": "ended"}):
      with self.assertRaises(ValueError): self.accept(controller, session, **changes)
    legacy = {"schema_version": 1, "session_id": session["session_id"], "source_mode": "live", "state": "running"}
    controller._accept(legacy, controller._generation)
    self.assertFalse(controller.status()["parking_confirmed"])
    self.assertEqual("unknown", controller.status()["parking"]["state"])


if __name__ == "__main__": unittest.main()
