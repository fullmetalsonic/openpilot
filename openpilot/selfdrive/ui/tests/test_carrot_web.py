import pytest

from openpilot.selfdrive.ui.carrot_web import (
  CARROT_WEB_AUTO_CLOSE_SECONDS,
  CARROT_WEB_MIN_CLOSE_SECONDS,
  CarrotWebAddressWatcher,
  CarrotWebQrSession,
  build_carrot_web_url,
  fit_single_line_font_size,
)


class FakeParamsMemory:
  def __init__(self, value=None):
    self.value = value
    self.requested_keys = []

  def get(self, key):
    self.requested_keys.append(key)
    return self.value


class FakeQRCodeTexture:
  def __init__(self):
    self.values = []
    self.destroyed = False

  def set_data(self, value):
    self.values.append(value)

  def destroy(self):
    self.destroyed = True


@pytest.mark.parametrize(
  "address,expected",
  [
    (None, None),
    ("", None),
    ("0.0.0.0", None),
    ("127.0.0.1", None),
    ("not-an-ip", None),
    (" 192.168.43.10\n", "http://192.168.43.10:7000"),
    ("2001:db8::1", "http://[2001:db8::1]:7000"),
  ],
)
def test_build_carrot_web_url(address, expected):
  assert build_carrot_web_url(address) == expected


@pytest.mark.parametrize(
  "preferred_size,measured_width,max_width,expected",
  [
    (26, 250, 274, 26),
    (26, 300, 274, 23),
    (65, 750, 685, 59),
    (26, 300, 0, 1),
  ],
)
def test_fit_single_line_font_size(preferred_size, measured_width, max_width, expected):
  assert fit_single_line_font_size(preferred_size, measured_width, max_width) == expected


def test_address_watcher_polls_at_interval_and_reports_changes():
  params = FakeParamsMemory("192.168.0.2")
  watcher = CarrotWebAddressWatcher(params, refresh_interval=0.1)

  assert watcher.refresh(1.0)
  assert watcher.value.url == "http://192.168.0.2:7000"

  params.value = "192.168.0.3"
  assert not watcher.refresh(1.05)
  assert watcher.value.url == "http://192.168.0.2:7000"

  assert watcher.refresh(1.1)
  assert watcher.value.url == "http://192.168.0.3:7000"
  assert params.requested_keys == ["NetworkAddress", "NetworkAddress"]


def test_qr_session_tracks_address_changes_and_clears_stale_qr():
  params = FakeParamsMemory("192.168.43.1")
  qr = FakeQRCodeTexture()
  timestamps = iter(["16:43:27", "16:44:02"])
  session = CarrotWebQrSession(params, qr_texture=qr, timestamp_factory=lambda: next(timestamps), connection_url_factory=lambda origin: origin)

  session.open(10.0)
  assert session.url == "http://192.168.43.1:7000"
  assert qr.values == ["http://192.168.43.1:7000"]
  assert session.updated_time == "16:43:27"

  params.value = "10.0.0.25"
  assert not session.update(10.05)
  assert qr.values == ["http://192.168.43.1:7000"]

  assert not session.update(10.1)
  assert session.url == "http://10.0.0.25:7000"
  assert qr.values[-1] == "http://10.0.0.25:7000"
  assert session.updated_time == "16:44:02"

  params.value = "0.0.0.0"
  assert not session.update(10.2)
  assert session.url is None
  assert qr.values[-1] is None
  assert session.updated_time is None


def test_qr_session_close_timing_and_cleanup():
  qr = FakeQRCodeTexture()
  session = CarrotWebQrSession(FakeParamsMemory("192.168.0.2"), qr_texture=qr, connection_url_factory=lambda origin: origin)
  session.open(5.0)

  assert session.seconds_until_close(5.0) == 30
  assert session.seconds_until_close(5.1) == 30
  assert session.seconds_until_close(6.0) == 29
  assert session.seconds_until_close(35.0) == 0
  assert not session.can_close(5.0 + CARROT_WEB_MIN_CLOSE_SECONDS - 0.01)
  assert session.can_close(5.0 + CARROT_WEB_MIN_CLOSE_SECONDS + 0.001)
  assert not session.update(5.0 + CARROT_WEB_AUTO_CLOSE_SECONDS - 0.01)
  assert session.update(5.0 + CARROT_WEB_AUTO_CLOSE_SECONDS)

  session.destroy()
  assert qr.destroyed


@pytest.mark.parametrize("address", ["100.64.2.3", "8.8.8.8"])
def test_native_url_preserves_cgnat_and_public_unicast(address):
  assert build_carrot_web_url(address) == f"http://{address}:7000"


def test_real_grants_only_change_qr_fragment_on_open_or_address_change(tmp_path):
  from openpilot.selfdrive.carrot.control_advisor.native_access import issue_local_grant, connection_url
  params, qr, calls = FakeParamsMemory("100.64.1.2"), FakeQRCodeTexture(), []
  def connect(origin):
    calls.append(origin)
    return connection_url(origin, issue_local_grant(origin, root=tmp_path / "access", boot_id="ui-fixture-boot"))
  session = CarrotWebQrSession(params, qr_texture=qr, connection_url_factory=connect)
  session.open(10.)
  assert session.url == "http://100.64.1.2:7000"
  assert session.connection_url.startswith(session.url + "#ca_grant=")
  assert qr.values == [session.connection_url]
  assert "ca_grant" not in session.url
  session.update(10.1)
  session.update(10.2)
  assert len(calls) == 1
  params.value = "8.8.8.8"
  session.update(10.3)
  assert session.url == "http://8.8.8.8:7000" and len(calls) == 2
  session.open(11.)
  assert len(calls) == 3
  params.value = "0.0.0.0"
  session.update(11.2)
  assert session.url is None and session.connection_url is None and qr.values[-1] is None
  assert len(calls) == 3


def test_optional_feature_error_falls_back_to_plain_qr(monkeypatch):
  from openpilot.selfdrive.ui import carrot_web
  from openpilot.selfdrive.carrot.control_advisor import native_access
  monkeypatch.setattr(carrot_web.sys, "platform", "linux")
  calls = []
  def unavailable(origin):
    calls.append(origin)
    raise OSError("fixture optional feature unavailable")
  monkeypatch.setattr(native_access, "issue_local_grant", unavailable)
  qr = FakeQRCodeTexture()
  session = CarrotWebQrSession(FakeParamsMemory("192.168.0.2"), qr_texture=qr)
  session.open(1.)
  assert qr.values == [session.url] and session.connection_url == session.url
  session.update(1.2)
  assert len(calls) == 1


@pytest.mark.parametrize("candidate", [None, "http://8.8.8.8:7000?ca_grant=invalid"])
def test_optional_feature_cannot_expand_qr_origin_or_clear_valid_address(candidate):
  qr = FakeQRCodeTexture()
  session = CarrotWebQrSession(FakeParamsMemory("192.168.0.2"), qr_texture=qr, connection_url_factory=lambda _: candidate)
  session.open(1.)
  assert session.url == session.connection_url == "http://192.168.0.2:7000"
  assert qr.values == [session.url]
