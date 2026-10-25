from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from ipaddress import ip_address
from math import ceil, floor
import sys

from openpilot.selfdrive.ui.widgets.qr_code import QRCodeTexture


CARROT_WEB_PORT = 7000
CARROT_WEB_ADDRESS_REFRESH_SECONDS = 0.1
CARROT_WEB_AUTO_CLOSE_SECONDS = 30.0
CARROT_WEB_MIN_CLOSE_SECONDS = 0.35


def _native_connection_url(origin: str) -> str:
  """Optional feature: only a physical native QR opening issues a grant."""
  if not sys.platform.startswith("linux"):
    return origin
  try:
    from openpilot.selfdrive.carrot.control_advisor.native_access import issue_local_grant, connection_url
    return connection_url(origin, issue_local_grant(origin))
  except Exception:
    return origin


def fit_single_line_font_size(preferred_size: int, measured_width: float, max_width: float) -> int:
  if preferred_size <= 1 or measured_width <= max_width:
    return max(1, preferred_size)
  if measured_width <= 0 or max_width <= 0:
    return 1
  return max(1, floor(preferred_size * max_width / measured_width))


def build_carrot_web_url(address: str | None) -> str | None:
  text = str(address or "").strip()
  if not text:
    return None

  try:
    parsed = ip_address(text)
  except ValueError:
    return None

  if parsed.is_unspecified or parsed.is_loopback or parsed.is_multicast:
    return None

  host = f"[{parsed.compressed}]" if parsed.version == 6 else parsed.compressed
  return f"http://{host}:{CARROT_WEB_PORT}"


@dataclass(frozen=True)
class CarrotWebAddress:
  address: str = ""
  url: str | None = None


class CarrotWebAddressWatcher:
  def __init__(self, params_memory, refresh_interval: float = CARROT_WEB_ADDRESS_REFRESH_SECONDS):
    self._params_memory = params_memory
    self._refresh_interval = refresh_interval
    self._next_refresh_time = 0.0
    self._value = CarrotWebAddress()

  @property
  def value(self) -> CarrotWebAddress:
    return self._value

  def refresh(self, now: float, force: bool = False) -> bool:
    if not force and now < self._next_refresh_time:
      return False

    self._next_refresh_time = now + self._refresh_interval
    try:
      address = str(self._params_memory.get("NetworkAddress") or "").strip()
    except Exception:
      address = ""

    url = build_carrot_web_url(address)
    next_value = CarrotWebAddress(address=address if url else "", url=url)
    changed = next_value != self._value
    self._value = next_value
    return changed


class CarrotWebQrSession:
  def __init__(self, params_memory, qr_texture: QRCodeTexture | None = None,
               timestamp_factory: Callable[[], str] | None = None,
               connection_url_factory: Callable[[str], str] | None = None):
    self._address = CarrotWebAddressWatcher(params_memory)
    self._qr = qr_texture or QRCodeTexture()
    self._timestamp_factory = timestamp_factory or (lambda: datetime.now().strftime("%H:%M:%S"))
    self._opened_at = 0.0
    self._updated_time: str | None = None
    self._connection_url: str | None = None
    self._connection_url_factory = connection_url_factory or _native_connection_url

  @property
  def url(self) -> str | None:
    return self._address.value.url

  @property
  def qr(self) -> QRCodeTexture:
    return self._qr

  @property
  def connection_url(self) -> str | None:
    return self._connection_url

  @property
  def updated_time(self) -> str | None:
    return self._updated_time

  def _refresh_qr(self) -> None:
    self._connection_url = self.url
    if self.url:
      try:
        candidate = self._connection_url_factory(self.url)
        if type(candidate) is str and (candidate == self.url or candidate.startswith(self.url + "#ca_grant=")):
          self._connection_url = candidate
      except Exception:
        pass
    self._qr.set_data(self.connection_url)
    self._updated_time = self._timestamp_factory() if self.url else None

  def open(self, now: float) -> None:
    self._opened_at = now
    self._address.refresh(now, force=True)
    self._refresh_qr()

  def update(self, now: float) -> bool:
    if self._address.refresh(now):
      self._refresh_qr()
    return now - self._opened_at >= CARROT_WEB_AUTO_CLOSE_SECONDS

  def can_close(self, now: float) -> bool:
    return now - self._opened_at >= CARROT_WEB_MIN_CLOSE_SECONDS

  def seconds_until_close(self, now: float) -> int:
    return max(0, ceil(CARROT_WEB_AUTO_CLOSE_SECONDS - (now - self._opened_at)))

  def destroy(self) -> None:
    self._qr.destroy()
