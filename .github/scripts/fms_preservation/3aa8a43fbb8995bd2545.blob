"""Producer bytes -> receiver -> journal -> session validation -> real HTTP."""

import asyncio
from copy import deepcopy
import importlib.util
from pathlib import Path
import tempfile
import time
import unittest

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from openpilot.selfdrive.carrot.control_advisor.consumer_collector import ConsumerCollector
from openpilot.selfdrive.carrot.control_advisor.live import LiveSession
from openpilot.selfdrive.carrot.control_advisor.recording import RecordingSession
from openpilot.selfdrive.carrot.control_advisor.session import SessionController
from openpilot.selfdrive.carrot.control_advisor.settings_history import KEYS, load_history
from openpilot.selfdrive.carrot.control_advisor.tests.test_consumer_recording import BOOT, produced_event


class ByteTransport:
  def __init__(self, packets):
    self.packets, self.closed = list(packets), False

  def receive(self, max_bytes):
    return self.packets.pop(0) if self.packets else None

  def close(self):
    self.closed = True


class ConsumerIntegrationTests(unittest.IsolatedAsyncioTestCase):
  async def test_persisted_observation_reaches_http_without_overwriting_stored_setting_or_claiming_ack(self):
    directory = tempfile.TemporaryDirectory()
    self.addCleanup(directory.cleanup)
    root = Path(directory.name)
    settings, logs, evidence = (root / name for name in ("settings", "logs", "evidence"))
    for path in (settings, logs, evidence):
      path.mkdir()
    for key in KEYS:
      (settings / key).write_text("120" if key == "SteerRatioRate" else "0", encoding="ascii")
    environment = {"CARROT_CONTROL_ADVISOR_DEMO_" + key: str(path) for key, path in zip(
      ("SETTINGS_DIR", "LOG_ROOT", "EVIDENCE_ROOT"), (settings, logs, evidence))}
    recording = RecordingSession("consumer-http", source_mode="synthetic", environment=environment,
                                 source_clock_ns=lambda: 3_000_000_000).start()
    self.addCleanup(recording.close)
    event, encoded = produced_event()
    transport = ByteTransport([encoded])
    collector = ConsumerCollector(source_mode="synthetic", transport=transport, expected_boot_id=BOOT,
                                  clock_ns=lambda: 2_100_000_000)
    self.addCleanup(collector.close)
    accepted = collector.poll()
    self.assertEqual([event], accepted)
    self.assertEqual(1, recording.submit_consumer_observations(accepted))
    deadline = time.monotonic() + 3
    while recording.snapshot()["consumer_history"]["recorded_events"] != 1:
      self.assertLess(time.monotonic(), deadline)
      await asyncio.sleep(.01)
    live = LiveSession("consumer-http", source_mode="synthetic", interval_seconds=180)
    live.recording_status = recording.snapshot()
    live.consumer_observations = {**collector.snapshot(), "persistence": live.recording_status["consumer_history"]}
    controller = SessionController()
    controller._snapshot = live.snapshot()
    controller._accept(live.snapshot(), controller._generation)
    feature_path = Path(__file__).parents[2] / "server/features/control_advisor.py"
    spec = importlib.util.spec_from_file_location("consumer_http_feature", feature_path)
    feature = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(feature)
    app = web.Application()
    feature.register(app, controller=controller, token="", live_enabled=False, synthetic_enabled=False)
    client = TestClient(TestServer(app))
    await client.start_server()
    self.addAsyncCleanup(client.close)
    response = await client.get("/api/control_advisor/status")
    self.assertEqual(200, response.status)
    payload = await response.json()
    observed = payload["session"]["consumer_observations"]
    self.assertEqual("synthetic", observed["source_mode"])
    self.assertFalse(observed["consumer_application_ack"])
    self.assertEqual(1, observed["persistence"]["recorded_events"])
    consumer = next(item for item in observed["consumers"] if item["consumer_id"] == "controls.vehicle_model")
    self.assertTrue(consumer["fresh"])
    self.assertEqual(1.0, consumer["latest_event"]["fields"]["SteerRatioRate"]["effective_value"])
    parameter = next(item for item in payload["parameters"] if item["key"] == "SteerRatioRate")
    self.assertEqual(120, parameter["current_raw"])
    self.assertEqual(["controls.vehicle_model"], parameter["consumer_ids"])
    self.assertTrue(all(item["consumer_ids"] for item in payload["parameters"]))
    self.assertFalse(payload["apply_enabled"])
    invalid = deepcopy(live.snapshot())
    invalid["consumer_observations"]["consumer_application_ack"] = True
    with self.assertRaises(ValueError):
      controller._accept(invalid, controller._generation)
    collector.close()
    recording.close(timeout=2)
    history = load_history(evidence / "consumer-http/settings.jsonl")
    self.assertEqual(1, history["consumer_events"])
    self.assertTrue(history["closed"])
    self.assertTrue(transport.closed)


if __name__ == "__main__":
  unittest.main()
