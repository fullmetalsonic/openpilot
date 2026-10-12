from types import SimpleNamespace
import sys

import numpy as np
import pytest

from openpilot.selfdrive.modeld import signal_neural_shadow as worker


def test_neural_dispatch_requires_explicit_opt_in(monkeypatch, tmp_path):
  from openpilot.selfdrive.modeld import signal_color_shadow, signal_tracking_shadow
  calls = []
  monkeypatch.setattr(worker, 'run', lambda *a: calls.append('neural'))
  monkeypatch.setattr(signal_tracking_shadow, 'run', lambda *a: calls.append('old'))
  (tmp_path / 'tracking_enabled').write_text('1')
  signal_color_shadow.run(tmp_path, 1)
  (tmp_path / 'neural_enabled').write_text('1')
  signal_color_shadow.run(tmp_path, 1)
  assert calls == ['old', 'neural']
  assert worker.backend_requested(tmp_path) == 'cpu'
  (tmp_path / 'neural_backend').write_text('automatic')
  with pytest.raises(ValueError): worker.backend_requested(tmp_path)


@pytest.mark.parametrize('label,score,expected', [('green', .9, 'green'), ('left,red', .8, 'red'),
                                               ('green', .4, 'unknown'), ('yellow', .9, 'unknown')])
def test_current_color_is_display_only(label, score, expected):
  result = worker.display_tracks([dict(id=1, box=[1,2,3,4], label=label, score=score,
                                       source='tracked', neural_age=.1)])[0]
  assert result['evidence']['raw'] == expected
  assert result['state'] == 'unknown'


@pytest.mark.parametrize('reused', [False, True])
@pytest.mark.parametrize('start_enabled', [False, True])
def test_live_camera_worker_separates_start_guard_from_old_assist(monkeypatch, tmp_path, reused, start_enabled):
  from tools.signal_analysis import neural_signal
  from openpilot.selfdrive.modeld import signal_tracking_shadow
  from openpilot.selfdrive.carrot import signal_display, signal_assist_runtime
  from openpilot.selfdrive.carrot import signal_start_guard
  for flag in ['enabled', 'neural_enabled', 'display_enabled', 'assist_enabled']:
    (tmp_path / flag).write_text('1')
  events, displays = [], []
  start_path = tmp_path/'start_observation.json'
  if start_enabled:
    (tmp_path/'start_guard_enabled').write_text('1')
  publisher = signal_start_guard.publish_start_observation
  monkeypatch.setattr(signal_start_guard, 'publish_start_observation',
                      lambda record, directory: publisher(record, directory, start_path))
  class Network:
    DETECTOR_SHA = CLASSIFIER_SHA = 'test'
    def __init__(self, *a, **kw): self.detector = self.classifier = self
    def run(self, *a): return []
    def detect(self, rgb, rect): return [dict(box=[40,30,60,40],score=.9)]
    def classify(self, rgb, box): return dict(label='green', score=.9)
  class Camera:
    frame_id = 4
    def __init__(self, *a): pass
    def is_connected(self): return True
    def recv(self, **kw):
      self.timestamp_eof = worker.time.monotonic_ns()
      (tmp_path / 'neural_enabled').write_text('0')
      return SimpleNamespace(frame_id=5 if reused else 4)
  monkeypatch.setitem(sys.modules, 'fcntl', SimpleNamespace(LOCK_EX=1, LOCK_NB=2, flock=lambda *a: None))
  monkeypatch.setitem(sys.modules, 'msgq.visionipc', SimpleNamespace(
    VisionIpcClient=Camera, VisionStreamType=SimpleNamespace(VISION_STREAM_ROAD=0)))
  monkeypatch.setitem(sys.modules, 'openpilot.common.swaglog', SimpleNamespace(cloudlog=SimpleNamespace(
    event=lambda n, **r: events.append((n,r)))))
  monkeypatch.setattr(worker, 'configure_worker_scheduling', lambda: None)
  monkeypatch.setattr(worker.time, 'sleep', lambda _: None)
  monkeypatch.setattr(neural_signal, 'NeuralSignal', Network)
  monkeypatch.setattr(signal_tracking_shadow, 'copy_nv12_rgb', lambda _: np.zeros((100,160,3),np.uint8))
  monkeypatch.setattr(signal_display, 'publish_display', displays.append)
  monkeypatch.setattr(signal_assist_runtime, 'publish_observation', lambda *a: pytest.fail('control transport used'))
  worker.run(tmp_path)
  assert len(displays) == (0 if reused else 1)
  assert start_path.exists() == (start_enabled and not reused)
  if not reused:
    assert displays[0]['tracks'][0]['evidence']['raw'] == 'green'
    assert displays[0]['neural'] and displays[0]['backend'] == 'cpu'
    assert displays[0]['start_guard_transport'] == start_enabled
  assert all(not r['control_permission'] and not r['assist_transport'] for _,r in events)


@pytest.mark.parametrize('selection', ['auto', 'qcom'])
@pytest.mark.parametrize('change_dm', [False, True])
def test_auto_switch_releases_previous_process_and_final_child(monkeypatch, tmp_path, selection, change_dm):
  import multiprocessing
  from openpilot.common import params
  modes = [False, False, True, True, False]
  position = [0]
  children = []
  class Child:
    exitcode = None
    pid = 123
    def __init__(self, *, target, args, daemon):
      self.args, self.alive, self.closed = args, False, False
      assert target == worker.run_selected and daemon
      children.append(self)
    def start(self):
      assert all(not c.alive for c in children if c is not self)
      self.alive = True
    def is_alive(self): return self.alive
    def terminate(self): self.alive = False
    def join(self, timeout): pass
    def close(self): self.closed = True
  def value(key):
    if key == 'UsbGpuActive': return True if change_dm else modes[position[0]]
    return False
  monkeypatch.setattr(params, 'Params', lambda: SimpleNamespace(
    get_bool=value, get_int=lambda key: {'SignalAssistMode': 1,
      'DisableDM': int(modes[position[0]]) if change_dm else 1}[key]))
  monkeypatch.setattr(worker, 'requested', lambda _: position[0] < len(modes))
  monkeypatch.setattr(worker, 'neural_requested', lambda _: True)
  monkeypatch.setattr(worker, 'backend_requested', lambda _: selection)
  monkeypatch.setattr(worker.time, 'sleep', lambda _: position.__setitem__(0, position[0]+1))
  monkeypatch.setattr(multiprocessing, 'get_context', lambda _: SimpleNamespace(Process=Child))
  monkeypatch.setitem(sys.modules, 'openpilot.common.swaglog', SimpleNamespace(cloudlog=SimpleNamespace(event=lambda *a, **k: None)))
  worker.run_auto(tmp_path)
  assert [c.args[1] for c in children] == ['tracking_cpu', 'qcom', 'tracking_cpu']
  assert all(c.closed and not c.alive for c in children)


@pytest.mark.parametrize('selection', ['auto', 'qcom'])
def test_explicit_qcom_also_passes_through_dm_guard(monkeypatch, tmp_path, selection):
  for name in ['enabled', 'neural_enabled']: (tmp_path/name).write_text('1')
  (tmp_path/'neural_backend').write_text(selection)
  calls = []
  monkeypatch.setattr(worker, 'run_auto', lambda *a: calls.append(a))
  worker.run(tmp_path, duration=1)
  assert calls == [(tmp_path, 1)]


@pytest.mark.parametrize('external', [False, True])
@pytest.mark.parametrize('disable_dm', [0, 1, 2])
@pytest.mark.parametrize('preview', [False, True])
@pytest.mark.parametrize('signal_mode', [0, 1, 2])
def test_gpu_selection_reserves_legacy_dm(external, disable_dm, preview, signal_mode):
  values = dict(UsbGpuActive=external, IsDriverViewEnabled=preview,
                DisableDM=disable_dm, SignalAssistMode=signal_mode)
  selected = worker.automatic_backend(SimpleNamespace(get_bool=values.__getitem__, get_int=values.__getitem__))
  assert selected == ('qcom' if signal_mode != 2 and external and disable_dm != 0 else 'tracking_cpu')


@pytest.mark.parametrize('modes,backends', [
  ([1, 1, 2, 2, 1, 0], ['qcom', 'tracking_cpu', 'qcom']),
  ([1, 2, 0], ['qcom', 'tracking_cpu']),
])
def test_mode_change_releases_previous_backend_before_starting_next(monkeypatch, tmp_path, modes, backends):
  import multiprocessing
  from openpilot.common import params
  position = [0]
  children = []
  actions = []
  class Child:
    pid = 123
    exitcode = None
    def __init__(self, *, target, args, daemon):
      self.args, self.alive = args, False
      assert target == worker.run_selected and daemon
      children.append(self)
    def start(self):
      assert all(not child.alive for child in children if child is not self)
      self.alive = True
      actions.append(('start', self.args[1]))
    def is_alive(self): return self.alive
    def terminate(self):
      self.alive = False
      actions.append(('stop', self.args[1]))
    def join(self, timeout): pass
    def close(self): actions.append(('close', self.args[1]))
  def integers(key):
    return {'DisableDM': 1, 'SignalAssistMode': modes[position[0]]}[key]
  monkeypatch.setattr(params, 'Params', lambda: SimpleNamespace(get_bool=lambda key: key == 'UsbGpuActive', get_int=integers))
  monkeypatch.setattr(worker, 'DIRECTORY', tmp_path)
  monkeypatch.setattr(worker, 'requested', lambda _: modes[position[0]] != 0)
  monkeypatch.setattr(worker, 'neural_requested', lambda _: True)
  monkeypatch.setattr(worker, 'backend_requested', lambda _: 'auto')
  monkeypatch.setattr(worker.time, 'sleep', lambda _: position.__setitem__(0, position[0]+1))
  monkeypatch.setattr(multiprocessing, 'get_context', lambda _: SimpleNamespace(Process=Child))
  monkeypatch.setitem(sys.modules, 'openpilot.common.swaglog', SimpleNamespace(cloudlog=SimpleNamespace(event=lambda *a, **k: None)))
  worker.run_auto(tmp_path)
  assert actions == [(action, backend) for backend in backends for action in ('start', 'stop', 'close')]


def test_production_off_uses_real_bounded_enable_cache_and_closes_child(monkeypatch, tmp_path):
  import multiprocessing
  from openpilot.common import params
  from openpilot.selfdrive.carrot import signal_mode
  ticks = [0]
  children, actions = [], []
  def current_mode():
    if ticks[0] < 2: return 1
    if ticks[0] < 6: return 2
    if ticks[0] < 8: return 1
    return 0
  param = SimpleNamespace(get_bool=lambda key: key == 'UsbGpuActive',
                          get_int=lambda key: {'DisableDM': 1, 'SignalAssistMode': current_mode()}[key])
  class Child:
    pid = 123
    exitcode = None
    def __init__(self, *, target, args, daemon):
      self.backend, self.alive = args[1], False
      children.append(self)
    def start(self):
      assert all(not c.alive for c in children if c is not self)
      self.alive = True
      actions.append(('start', self.backend, ticks[0] / 10))
    def is_alive(self): return self.alive
    def terminate(self):
      self.alive = False
      actions.append(('stop', self.backend, ticks[0] / 10))
    def join(self, timeout): pass
    def close(self): actions.append(('close', self.backend, ticks[0] / 10))
  monkeypatch.setattr(params, 'Params', lambda: param)
  monkeypatch.setattr(worker, 'DIRECTORY', tmp_path)
  monkeypatch.setattr(signal_mode, 'DIRECTORY', tmp_path)
  monkeypatch.setattr(signal_mode, '_params', param)
  monkeypatch.setattr(signal_mode, '_mode', 0)
  monkeypatch.setattr(signal_mode, '_next_read', 0.)
  monkeypatch.setattr(worker, 'neural_requested', lambda _: True)
  monkeypatch.setattr(worker, 'backend_requested', lambda _: 'auto')
  monkeypatch.setattr(worker.time, 'monotonic', lambda: ticks[0] / 10)
  def advance(_):
    ticks[0] += 1
    assert ticks[0] <= 12, 'OFF must exit within the real enable-cache window'
  monkeypatch.setattr(worker.time, 'sleep', advance)
  monkeypatch.setattr(multiprocessing, 'get_context', lambda _: SimpleNamespace(Process=Child))
  monkeypatch.setitem(sys.modules, 'openpilot.common.swaglog', SimpleNamespace(cloudlog=SimpleNamespace(event=lambda *a, **k: None)))
  worker.run_auto(tmp_path)
  assert [c.backend for c in children] == ['qcom', 'tracking_cpu', 'qcom']
  assert all(not c.alive for c in children)
  assert actions[-1] == ('close', 'qcom', 1.0)
  assert actions[-1][2] - 0.8 <= 0.5


def test_auto_cpu_is_comparison_only_and_gpu_requires_cache(monkeypatch, tmp_path):
  from openpilot.selfdrive.modeld import signal_tracking_shadow
  calls = []
  monkeypatch.setattr(signal_tracking_shadow, 'run', lambda *a, **k: calls.append(k))
  worker.run_selected(tmp_path, 'tracking_cpu')
  assert calls == [dict(comparison_override=True)]
  with pytest.raises(RuntimeError, match='requires locally validated'):
    worker.run_selected(tmp_path, 'qcom')
