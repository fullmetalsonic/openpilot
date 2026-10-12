"""Serialize mode-4 gap requests without disk writes in the control loop."""
import logging
from queue import Empty, Full, Queue
from threading import Lock, Thread

from openpilot.selfdrive.carrot.cruise_gap import next_gap_personality


def effective_paddle_mode(active: int, requested: int) -> int:
  """Entering/leaving mode 4 requires a new control process."""
  return active if active == 4 or requested == 4 else requested


class PaddleGapWriter:
  """One worker owns read/modify/write ordering, including A->B->A requests.

  External UI writes retain normal last-writer semantics. A subsequent request
  reads the latest persisted value. Pending requests are not durable on process
  termination. A storage failure stops this writer until process restart.
  """
  def __init__(self, params_factory):
    self._queue = Queue(maxsize=64)
    self._lock = Lock()
    self.failed = False
    self._closed = False
    self._thread = Thread(target=self._run, args=(params_factory,), name="paddle-gap", daemon=True)
    self._thread.start()

  def request(self, operation: str, value: int) -> bool:
    if operation not in ("step", "cycle", "set"):
      raise ValueError(operation)
    with self._lock:
      if self.failed or self._closed:
        return False
      try:
        self._queue.put_nowait((operation, value))
      except Full:
        logging.error("Paddle gap queue full; request rejected")
        return False
    return True

  def _run(self, params_factory):
    try:
      params = params_factory()
      while True:
        request = self._queue.get()
        try:
          if request is None:
            return
          operation, value = request
          current = self._read_personality(params)
          if operation == "step":
            target = min(3, max(0, current + value))
          elif operation == "cycle":
            target = next_gap_personality(current, value)
          else:
            target = min(3, max(0, value))
          if target != current:
            result = params.put_int("LongitudinalPersonality", target)
            if result is not None and result < 0:
              raise OSError("LongitudinalPersonality write failed")
            # The native Python binding does not expose putInt's return code.
            # Fail closed on a failed write or a conflicting external write.
            if self._read_personality(params) != target:
              raise OSError("LongitudinalPersonality readback mismatch")
        finally:
          self._queue.task_done()
    except Exception:
      logging.exception("Paddle gap storage failed; restart required")
      with self._lock:
        self.failed = True
        while True:
          try:
            self._queue.get_nowait()
            self._queue.task_done()
          except Empty:
            break

  @staticmethod
  def _read_personality(params):
    # Use the typed safe getter: native get_int uses std::stoi without a
    # translated C++ exception on malformed storage.
    value = params.get("LongitudinalPersonality", return_default=False)
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 3:
      raise ValueError("Invalid stored LongitudinalPersonality")
    return value

  def close(self):
    """Drain successful requests; used when the owner shuts down cleanly."""
    with self._lock:
      self._closed = True
    if not self.failed and self._thread.is_alive():
      self._queue.put(None)
    self._thread.join()
