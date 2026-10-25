"""Read-only status and explicitly enabled diagnostic-session controls."""
import asyncio
from copy import deepcopy
import hashlib
import hmac
import json
import math
import os
from pathlib import Path
import re
import secrets
import stat
import threading
import time
import uuid

from aiohttp import web

from openpilot.selfdrive.carrot.control_advisor.registry import readiness
from openpilot.selfdrive.carrot.control_advisor.session import SessionConflict, SessionController, validate_start


MAX_BODY_BYTES = 4096
MAX_REVIEW_BODY_BYTES = 128 * 1024
CONTROLLER_KEY = web.AppKey("control_advisor_controller", SessionController)
EXPORT_STATES = {"queued", "running", "cancelling", "ready", "cancelled", "timed_out", "interrupted", "failed"}
EXPORT_BODY = {"request_id", "summary_id", "summary_revision", "include_raw"}
EXPORT_BUDGET = {"max_bytes": 512 * 1024 * 1024, "max_files": 512, "timeout_seconds": 120}


def _failure(error, status):
  return web.json_response({"ok": False, "error": error, "apply_enabled": False}, status=status, headers={"Cache-Control": "no-store"})


def _request_root(root, protected):
  path = Path(root)
  if not path.is_absolute() or path == Path(path.anchor):
    raise ValueError("request_root_invalid")
  for candidate in (path, *path.parents):
    if candidate.is_symlink() or (hasattr(candidate, "is_junction") and candidate.is_junction()):
      raise ValueError("request_root_invalid")
  path = path.resolve()
  for value in filter(None, protected):
    other = Path(value)
    if not other.is_absolute():
      raise ValueError("request_root_invalid")
    other = other.resolve()
    if path == other or path in other.parents or other in path.parents:
      raise ValueError("request_root_overlap")
  return path


def _export_path(path):
  from openpilot.selfdrive.carrot.control_advisor.review_export import _fs_path
  return _fs_path(path)


class _ReviewJobs:
  """Thread-owned export receipts. HTTP sees only separately published copies."""
  def __init__(self, root, verifier, budget, *, total_bytes=1024 * 1024 * 1024):
    from openpilot.selfdrive.carrot.control_advisor.periodic import _atomic
    from openpilot.selfdrive.carrot.control_advisor.recording import _RootLease
    self.root, self.verifier, self.budget, self.total_bytes = Path(root), verifier, budget, total_bytes
    self.records, self.failed, self._lease, self._atomic = {}, False, None, _atomic
    self._verified_downloads = {}
    _export_path(self.root).mkdir(mode=0o700, exist_ok=True)
    marker = _export_path(self.root / "export-owner.json")
    expected = {"schema_version": 1, "kind": "control_advisor_review_exports"}
    if marker.exists():
      if self._read(marker) != expected:
        raise ValueError("export_root_not_owned")
    elif any(_export_path(self.root).iterdir()):
      raise ValueError("export_root_not_empty")
    self._lease = _RootLease(_export_path(self.root))
    try:
      if not marker.exists():
        _atomic(marker, expected, durable=True)
      self._usage()
      for path in _export_path(self.root).iterdir():
        if path.name in {"export-owner.json", ".control-advisor.lock"}:
          continue
        if re.fullmatch(r"[a-f0-9]{32}", path.name) is None or not path.is_dir() or len(self.records) >= 32:
          raise ValueError("export_root_structure_invalid")
        record = self._read(path / "job.json")
        self._validate(record, path.name)
        self.records[path.name] = record
        if record["state"] == "ready":
          try:
            self._verified(path.name)
          except Exception:
            self.update(path.name, "failed", "review_integrity_failed")
        elif record["state"] in {"queued", "running", "cancelling"}:
          self.update(path.name, "interrupted", "export_server_restarted")
    except BaseException:
      self.close()
      raise

  @staticmethod
  def _read(path):
    path = _export_path(path)
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or path.is_symlink() or info.st_size > 64 * 1024:
      raise ValueError("export_status_invalid")
    def unique(pairs):
      result = {}
      for key, value in pairs:
        if key in result:
          raise ValueError("export_status_invalid")
        result[key] = value
      return result
    return json.loads(path.read_bytes(), object_pairs_hook=unique,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError("export_status_invalid")))

  @staticmethod
  def _validate(record, job_id):
    fields = {"schema_version", "job_id", "request_id", "request_sha256", "summary_id", "summary_revision", "include_raw",
              "state", "reason", "files", "bytes", "zip_bytes", "zip_sha256", "manifest_sha256"}
    if (type(record) is not dict or set(record) != fields or record["schema_version"] != 1 or record["job_id"] != job_id
        or record["state"] not in EXPORT_STATES or type(record["include_raw"]) is not bool
        or type(record["summary_revision"]) is not int or record["summary_revision"] < 1):
      raise ValueError("export_status_invalid")
    for key in ("request_id", "summary_id"):
      if type(record[key]) is not str or re.fullmatch(r"[A-Za-z0-9_-]{1,128}", record[key]) is None:
        raise ValueError("export_status_invalid")
    for key in ("request_sha256", "zip_sha256", "manifest_sha256"):
      if record[key] is not None and (type(record[key]) is not str or re.fullmatch(r"[a-f0-9]{64}", record[key]) is None):
        raise ValueError("export_status_invalid")
    for key in ("files", "bytes", "zip_bytes"):
      if type(record[key]) is not int or record[key] < 0:
        raise ValueError("export_status_invalid")
    if record["reason"] is not None and (type(record["reason"]) is not str or re.fullmatch(r"[a-z0-9_]{1,128}", record["reason"]) is None):
      raise ValueError("export_status_invalid")

  def _usage(self):
    total = entries = 0
    def fail(error):
      raise error
    for directory, folders, files in os.walk(_export_path(self.root), followlinks=False, onerror=fail):
      for name in [*folders, *files]:
        path = Path(directory) / name
        info = path.lstat()
        entries += 1
        if entries > 20_000 or path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()) or not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)):
          raise ValueError("export_root_structure_invalid")
        if stat.S_ISREG(info.st_mode):
          total += info.st_size
        if total > self.total_bytes:
          raise ValueError("export_total_quota")
    return total

  def find(self, body):
    digest = hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    for record in self.records.values():
      if record["request_id"] == body["request_id"]:
        if record["request_sha256"] != digest:
          raise ValueError("export_request_conflict")
        return self.public(record["job_id"])
    return None

  def reserve(self, body):
    if self.failed or len(self.records) >= 32 or self._usage() + self.budget["max_bytes"] > self.total_bytes:
      raise ValueError("export_total_quota")
    job_id = uuid.uuid4().hex
    _export_path(self.root / job_id).mkdir(mode=0o700)
    record = {"schema_version": 1, "job_id": job_id, "request_id": body["request_id"],
      "request_sha256": hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
      "summary_id": body["summary_id"], "summary_revision": body["summary_revision"], "include_raw": body["include_raw"],
      "state": "queued", "reason": None, "files": 0, "bytes": 0, "zip_bytes": 0, "zip_sha256": None, "manifest_sha256": None}
    self.records[job_id] = record
    return self.update(job_id, "queued")

  def update(self, job_id, state, reason=None, result=None):
    record = {**self.records[job_id], "state": state, "reason": reason}
    if result is not None:
      record.update({key: result[key] for key in ("files", "bytes", "zip_bytes", "zip_sha256", "manifest_sha256")})
    self._validate(record, job_id)
    try:
      self._atomic(_export_path(self.root / job_id / "job.json"), record, durable=True)
    except Exception:
      self.failed = True
      record.update(state="failed", reason="export_storage_unconfirmed")
      self.records[job_id] = record
      raise
    self.records[job_id] = record
    return self.public(job_id)

  def public(self, job_id):
    record = self.records[job_id]
    return {key: record[key] for key in ("job_id", "request_id", "summary_id", "summary_revision", "include_raw", "state", "reason", "files", "bytes", "zip_bytes")} | {"download_ready": record["state"] == "ready" and not self.failed}

  def list_jobs(self):
    return [self.public(job_id) for job_id in reversed(list(self.records))]

  def _verified(self, job_id):
    record = self.records[job_id]
    result = self.verifier(self.root / job_id / "pack", budget=self.budget)
    if result.get("state") != "verified" or any(result.get(key) != record[key] for key in ("summary_id", "summary_revision", "zip_sha256", "manifest_sha256", "bytes", "zip_bytes")) or result.get("include_private_raw") != record["include_raw"]:
      raise ValueError("review_integrity_failed")
    path = self.root / job_id / "pack" / "review.zip"
    self._verified_downloads[job_id] = self._fingerprint(path)
    return path

  @staticmethod
  def _fingerprint(path):
    path = _export_path(path)
    info = path.lstat()
    if path.is_symlink() or not stat.S_ISREG(info.st_mode):
      raise ValueError("review_integrity_failed")
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)

  def download(self, job_id):
    if self.failed or self.records[job_id]["state"] != "ready":
      raise ValueError("export_not_ready")
    path = self.root / job_id / "pack" / "review.zip"
    return path if self._verified_downloads.get(job_id) == self._fingerprint(path) else self._verified(job_id)

  def close(self):
    if self._lease is not None:
      self._lease.close()
      self._lease = None


def register(app: web.Application, *, controller=None, token=None, live_enabled=None, synthetic_enabled=None, report_directory=None,
             auto_output_root=None, periodic_reader=None, refresh_interval=5.0, request_root=None,
             request_resolver=None, request_target_id="current_vehicle", pc_store=None, comparison_provider=None,
             summary_service=None, review_builder=None, export_root=None, export_budget=None,
             access_sessions=None, runtime_config=None, native_params=None, native_comma=None, native_runtime=None):
  controller_supplied = controller is not None
  live_override = live_enabled is not None or "CARROT_CONTROL_ADVISOR_LIVE_ENABLED" in os.environ
  controller = controller or SessionController()
  token = os.environ.get("CARROT_CONTROL_ADVISOR_TOKEN", "") if token is None else token
  live_enabled = os.environ.get("CARROT_CONTROL_ADVISOR_LIVE_ENABLED") == "1" if live_enabled is None else live_enabled
  synthetic_enabled = os.environ.get("CARROT_CONTROL_ADVISOR_ALLOW_DEMO") == "1" if synthetic_enabled is None else synthetic_enabled
  manual_configured = isinstance(token, str) and 32 <= len(token) <= 256 and token.isascii() and all(33 <= ord(char) <= 126 for char in token)
  configured = manual_configured or access_sessions is not None
  capabilities = {"control_configured": configured, "live_enabled": bool(configured and live_enabled),
                  "synthetic_enabled": bool(configured and synthetic_enabled), "request_registration_enabled": False,
                  "pc_simulation_enabled": False, "review_export_enabled": False}
  app[CONTROLLER_KEY] = controller
  report_directory = os.environ.get("CARROT_CONTROL_ADVISOR_CANDIDATE_REPORT_DIR") if report_directory is None else report_directory
  auto_output_root = os.environ.get("CARROT_CONTROL_ADVISOR_AUTO_OUTPUT_ROOT") if auto_output_root is None else auto_output_root
  candidate_report = {"state": "loading" if report_directory else "disabled", "reason": None, "report": None}
  refresh_task = None
  closed = False
  report_cache = {}
  request_root = os.environ.get("CARROT_CONTROL_ADVISOR_REQUEST_ROOT") if request_root is None else request_root
  request_actor = None
  request_lock = asyncio.Lock()
  request_refresh_task = None
  request_tasks = set()
  request_summaries = {}
  request_public = {"state": "disabled", "reason": "request_registration_disabled", "items": [], "context": None}
  summary_public = {"state": "waiting", "reason": "summary_not_ready", "summary": None}
  export_public = {"state": "disabled", "reason": "export_not_configured", "jobs": []}
  export_root = os.environ.get("CARROT_CONTROL_ADVISOR_EXPORT_ROOT") if export_root is None else export_root
  export_store, summary_task, export_active = None, None, None
  export_lock, download_lock = asyncio.Lock(), asyncio.Lock()
  download_grants, download_paths = {}, {}
  invalid_downloads = set()
  review_metadata = {}
  budget = {**EXPORT_BUDGET, **(export_budget or {})}
  native_public = {"state": "disabled", "reason": "native_runtime_required"}

  async def load_native_runtime(application):
    nonlocal controller, native_params, runtime_config, access_sessions, configured, auto_output_root, request_root, export_root, live_enabled, native_public
    try:
      from openpilot.selfdrive.carrot.control_advisor.runtime_config import resolve_runtime_config
      actual_native = os.name == "posix" and Path("/TICI").exists() if native_comma is None else native_comma
      if runtime_config is None:
        def resolve():
          params = native_params
          if actual_native and params is None:
            try:
              from openpilot.common.params import Params
              params = Params()
            except (ImportError, OSError, RuntimeError):
              params = None
          return params, resolve_runtime_config(params=params, native_comma=actual_native)
        native_params, runtime_config = await request_thread(resolve)
      if runtime_config.get("runtime_kind") != "native_comma":
        return
      paths = runtime_config["paths"]
      native_public = {"state": "ready" if runtime_config["capabilities"].get("collection_ready") else "partial", "reason": None,
        "collection_ready": runtime_config["capabilities"].get("collection_ready") is True,
        "analysis_configured": runtime_config["capabilities"].get("analysis_configured") is True,
        "blockers": deepcopy(runtime_config.get("blockers", {}))}
      if auto_output_root is None and not report_directory:
        auto_output_root = paths.get("candidates")
      if request_root is None:
        request_root = paths.get("requests")
      if export_root is None:
        export_root = paths.get("exports")
      if access_sessions is None:
        from openpilot.selfdrive.carrot.control_advisor.native_access import AccessSessions
        access_sessions = await request_thread(AccessSessions)
      configured = manual_configured or access_sessions is not None
      if not live_override:
        live_enabled = native_public["collection_ready"]
      capabilities.update(control_configured=configured, live_enabled=bool(configured and live_enabled), synthetic_enabled=bool(configured and synthetic_enabled))
      if not controller_supplied:
        from openpilot.selfdrive.carrot.control_advisor.native_runtime import live_environment
        environment = live_environment(runtime_config, os.environ)
        controller = SessionController(live_environment=environment)
        app[CONTROLLER_KEY] = controller
    except (ValueError, TypeError, OSError, RuntimeError, ImportError, KeyError):
      native_public = {"state": "unavailable", "reason": "native_runtime_unavailable"}
      if not live_override:
        capabilities["live_enabled"] = False

  def evidence_directory():
    return os.environ.get("CARROT_CONTROL_ADVISOR_EVIDENCE_ROOT") or (runtime_config or {}).get("paths", {}).get("evidence")

  async def refresh_native_snapshot():
    nonlocal native_public
    if native_runtime is None:
      return
    try:
      value = await request_thread(native_runtime.public_status)
      vehicle = value.get("vehicle", {})
      groups = [{"group_id": item["group_id"], "car_fingerprint": str(item.get("car_fingerprint", ""))[:256], "vin_known": item.get("vin_known") is True}
        for item in value.get("groups", [])[:64] if type(item) is dict and re.fullmatch(r"[a-f0-9]{32}", item.get("group_id", ""))]
      native_public = {**native_public, "state": "ready", "vehicle": {"state": vehicle.get("state"), "car_fingerprint": vehicle.get("car_fingerprint"),
        "physical_group_id": vehicle.get("physical_group_id"), "physical_group_basis": vehicle.get("physical_group_basis"), "vin_known": vehicle.get("vin_sha256") is not None},
        "groups": groups, "vehicle_confirmed": value.get("vehicle_confirmed") is True, "writer_available": value.get("writer_available") is True,
        "offroad_restore_available": value.get("offroad_restore_available") is True,
        "reason": value.get("reason"), "analysis_blockers": value.get("analysis_blockers", [])}
    except (ValueError, OSError, RuntimeError, KeyError):
      native_public = {**native_public, "state": "unavailable", "reason": "native_status_unavailable", "writer_available": False}

  async def publish_exports():
    if export_store is not None and not closed:
      jobs = await request_thread(export_store.list_jobs)
      if not closed:
        jobs = [{**job, **review_metadata.get(job["job_id"], {"review_count": 0, "latest_review_revision": None, "review_state": "unreviewed"})} for job in jobs]
        jobs = [{**job, "state": "failed", "reason": "review_integrity_failed", "download_ready": False} if job["job_id"] in invalid_downloads else job for job in jobs]
        export_public.update(jobs=jobs, state="failed" if export_store.failed else "ready",
                             reason="export_storage_unconfirmed" if export_store.failed else None)
        capabilities["review_export_enabled"] = configured and not export_store.failed

  async def refresh_summary():
    while not closed:
      try:
        session = controller.status()
        snapshot = {"session": session, "candidate_report": deepcopy(candidate_report),
          "apply_requests": deepcopy(request_public["items"]), "request_state": {"state": request_public["state"], "reason": request_public["reason"]}}
        if request_actor is not None:
          snapshot["request_export"] = await request_operation(request_actor.export_snapshot)
        parking = session.get("parking", {})
        trigger = "stop" if session.get("state") in {"stopped", "ended", "failed", "unavailable"} else "parking" if parking.get("state") == "park_observed" else "offroad" if parking.get("offroad_state") == "offroad_observed" else "manual"
        summary = await request_thread(summary_service.freeze_cached, snapshot, trigger)
        if not closed:
          summary_public.update(state="ready", reason=None, summary=summary)
      except asyncio.CancelledError:
        raise
      except Exception:
        if not closed:
          summary_public.update(state="failed", reason="summary_unavailable")
      await asyncio.sleep(min(refresh_interval, 2.0))

  async def load_review(application):
    nonlocal summary_service, review_builder, summary_task, export_store
    roots = {key: value for key, value in {"evidence": evidence_directory(),
      "automatic": auto_output_root or report_directory, "requests": request_root if request_actor is not None else None}.items() if value}
    try:
      if summary_service is None:
        from openpilot.selfdrive.carrot.control_advisor.parking_summary import ParkingSummaryService
        summary_service = await asyncio.to_thread(ParkingSummaryService, roots)
      summary_task = asyncio.create_task(refresh_summary(), name="control-advisor-summary-refresh")
    except Exception:
      summary_public.update(state="unavailable", reason="summary_unavailable")
    if not export_root or not configured or summary_service is None:
      return
    try:
      from openpilot.selfdrive.carrot.control_advisor.review_export import ReviewPackBuilder, verify_review_pack
      if set(budget) != set(EXPORT_BUDGET) or any(type(budget[key]) is not int or not 0 < budget[key] <= upper
          for key, upper in (("max_bytes", 1024 * 1024 * 1024), ("max_files", 1024), ("timeout_seconds", 600))):
        raise ValueError("export_budget_invalid")
      protected = [*roots.values(), report_directory, auto_output_root, request_root, getattr(pc_store, "root", None)]
      protected.extend(os.environ.get("CARROT_CONTROL_ADVISOR_" + prefix + suffix) for prefix in ("", "DEMO_")
                       for suffix in ("SETTINGS_DIR", "LOG_ROOT", "EVIDENCE_ROOT"))
      protected.extend(getattr(review_builder, "roots", {}).values())
      root = await asyncio.to_thread(_request_root, export_root, protected)
      export_store = await asyncio.to_thread(_ReviewJobs, root, verify_review_pack, budget)
      if review_builder is None:
        review_builder = await asyncio.to_thread(ReviewPackBuilder, roots, export_root=root)
      elif Path(review_builder.export_root).resolve() != root:
        raise ValueError("export_builder_root_mismatch")
      from openpilot.selfdrive.carrot.control_advisor.review_results import ReviewResultStore
      for job_id in export_store.records:
        try:
          store = await request_thread(ReviewResultStore, root / job_id / "reviews", None)
          entries = await request_thread(store.list_reviews)
          review_metadata[job_id] = review_meta(entries)
        except (ValueError, OSError):
          review_metadata[job_id] = {"review_count": None, "latest_review_revision": None, "review_state": "unavailable"}
      await publish_exports()
    except Exception:
      if export_store is not None:
        await asyncio.to_thread(export_store.close)
      export_store = None
      export_public.update(state="disabled", reason="export_configuration_invalid")

  async def request_thread(method, *args, **kwargs):
    work = asyncio.create_task(asyncio.to_thread(method, *args, **kwargs))
    try:
      return await asyncio.shield(work)
    except asyncio.CancelledError:
      # Keep the single-flight lease until the owned blocking operation settles.
      try:
        await work
      except Exception:
        pass
      raise

  async def request_operation(method, *args, **kwargs):
    async with request_lock:
      return await request_thread(method, *args, **kwargs)

  async def refresh_request_snapshot():
    if request_actor is None or closed:
      return
    async with request_lock:
      actor_capabilities = await request_thread(request_actor.capabilities)
      capabilities.update(request_registration_enabled=configured and actor_capabilities.get("request_registration_enabled") is True,
                          pc_simulation_enabled=actor_capabilities.get("pc_simulation_enabled") is True,
                          vehicle_write_available=actor_capabilities.get("apply_enabled") is True)
      await refresh_native_snapshot()
      summaries = await request_thread(request_actor.list_requests, limit=128)
      for item in summaries[:16]:
        if item.get("state") in {"stored", "consumer_observed", "comparison_ready"}:
          await request_thread(request_actor.review, item["request_id"])
      summaries = await request_thread(request_actor.list_requests, limit=128)
      try:
        if capabilities["request_registration_enabled"]:
          context = await request_thread(request_resolver.candidate_context, request_target_id)
          reason = None
        else:
          context, reason = None, "request_registration_disabled"
      except Exception:
        context, reason = None, "request_evidence_unavailable"
      if not closed:
        request_summaries.clear()
        request_summaries.update((item["request_id"], item) for item in summaries)
        request_public.update(state="ready", reason=reason, items=summaries[:16], context=context)

  async def refresh_requests():
    while not closed:
      try:
        await refresh_request_snapshot()
      except asyncio.CancelledError:
        raise
      except Exception:
        if not closed:
          request_public.update(state="failed", reason="request_status_unavailable", context=None)
      await asyncio.sleep(refresh_interval)

  async def load_requests(application):
    nonlocal request_actor, request_resolver, request_refresh_task, native_runtime, comparison_provider
    if not request_root or not configured:
      await refresh_native_snapshot()
      return
    try:
      from openpilot.selfdrive.carrot.control_advisor.apply_requests import ApplyRequestController
      from openpilot.selfdrive.carrot.control_advisor.request_evidence import CandidateEvidenceResolver
      loop = asyncio.get_running_loop()

      async def capture_binding():
        binding = controller.request_binding()
        if closed:
          binding.update(settings_observation_fresh=False, settings_revision=None, settings_raw={}, observed_mono_ns=None)
        return binding

      async def capture_status():
        return controller.status()

      def capture_current(method):
        pending = asyncio.run_coroutine_threadsafe(method(), loop)
        try:
          return pending.result(timeout=1.0)
        except Exception:
          pending.cancel()
          raise ValueError("request_context_unavailable") from None

      if native_runtime is None and (runtime_config or {}).get("runtime_kind") == "native_comma":
        from openpilot.selfdrive.carrot.control_advisor.native_runtime import NativeRuntime
        creation = asyncio.create_task(asyncio.to_thread(NativeRuntime, runtime_config, native_params,
          session_status=lambda: capture_current(capture_status), session_binding=lambda: capture_current(capture_binding)))
        try:
          native_runtime = await asyncio.shield(creation)
        except asyncio.CancelledError:
          try:
            unfinished = await creation
            await asyncio.to_thread(unfinished.close)
          finally:
            raise
      await refresh_native_snapshot()
      protected = [report_directory, auto_output_root, getattr(pc_store, "root", None)]
      protected.extend(os.environ.get("CARROT_CONTROL_ADVISOR_" + prefix + suffix) for prefix in ("", "DEMO_")
                       for suffix in ("SETTINGS_DIR", "LOG_ROOT", "EVIDENCE_ROOT"))
      root = _request_root(request_root, protected)
      if request_resolver is None:
        evidence_root = evidence_directory()
        if pc_store is not None or not auto_output_root or not evidence_root:
          raise ValueError("request_evidence_unavailable")
        def current_context(target_id):
          if target_id != request_target_id:
            raise ValueError("request_target_mismatch")
          return native_runtime.current_context(target_id) if native_runtime is not None else capture_current(capture_binding)

        request_resolver = CandidateEvidenceResolver({request_target_id: {"scope": "vehicle", "kind": "periodic",
          "directory": Path(auto_output_root), "session_root": Path(evidence_root)}}, current_context)
      if native_runtime is not None and comparison_provider is None:
        from openpilot.selfdrive.carrot.control_advisor.native_runtime import comparison_profile
        from openpilot.selfdrive.carrot.control_advisor.request_evidence import make_comparison_provider
        comparison_provider = make_comparison_provider(request_resolver, comparison_profile(), source_kind="recorded")
      owned_store = pc_store if pc_store is not None else native_runtime
      clock_options = {"clock_ns": owned_store.clock_ns} if owned_store is not None else {}
      creation = asyncio.create_task(asyncio.to_thread(ApplyRequestController, root, request_resolver, pc_store=pc_store,
                                                       vehicle_store=native_runtime.store if native_runtime is not None else None,
                                                       enabled=True, comparison_provider=comparison_provider, **clock_options))
      try:
        request_actor = await asyncio.shield(creation)
      except asyncio.CancelledError:
        try:
          unfinished = await creation
          await asyncio.to_thread(unfinished.close)
        finally:
          raise
      capabilities.update(request_registration_enabled=True, pc_simulation_enabled=pc_store is not None)
      request_public.update(state="waiting", reason="request_status_waiting")
      request_refresh_task = asyncio.create_task(refresh_requests(), name="control-advisor-request-refresh")
    except Exception as failure:
      request_public.update(state="disabled", reason="request_configuration_invalid", context=None)
      if (runtime_config or {}).get("runtime_kind") == "native_comma" and native_runtime is None:
        reason = str(failure)
        native_public.update(state="unavailable", reason=reason if re.fullmatch(r"native_[a-z0-9_]{1,112}", reason) else "native_runtime_unavailable", writer_available=False)

  async def refresh_reports():
    nonlocal candidate_report
    reader = periodic_reader
    while not closed:
      try:
        if reader is None:
          from openpilot.selfdrive.carrot.control_advisor.periodic import read_periodic_result
          reader = read_periodic_result
        result = await asyncio.to_thread(reader, auto_output_root, cache=report_cache)
        if not isinstance(result, dict):
          raise ValueError("invalid_periodic_result")
        result = {**result, "apply_enabled": False, "recommendations_enabled": False, "device_qualified": False}
        if result.get("state") != "ready" or result.get("stale") is True:
          result["report"] = None
          if result.get("stale") is True and result.get("state") == "ready":
            result["state"] = "stale"
        if not closed:
          candidate_report = result
      except asyncio.CancelledError:
        raise
      except Exception:
        if not closed:
          candidate_report = {"state": "invalid", "reason": "periodic_result_invalid", "report": None,
                              "attempt": None, "last_success": None, "stale": False,
                              "apply_enabled": False, "recommendations_enabled": False, "device_qualified": False}
      await asyncio.sleep(refresh_interval)

  async def load_report(application):
    nonlocal candidate_report, refresh_task
    if auto_output_root:
      candidate_report = {"state": "waiting", "reason": "periodic_result_waiting", "report": None,
                          "attempt": None, "last_success": None, "stale": False}
      if report_directory:
        candidate_report.update(state="invalid", reason="candidate_report_configuration_conflict")
      elif not Path(auto_output_root).is_absolute():
        candidate_report.update(state="invalid", reason="periodic_result_requires_absolute_directory")
      else:
        refresh_task = asyncio.create_task(refresh_reports(), name="control-advisor-report-refresh")
      return
    if not report_directory:
      return
    try:
      from openpilot.selfdrive.carrot.control_advisor.candidate_report import load_candidate_bundle
      if not Path(report_directory).is_absolute():
        raise ValueError("candidate_report_requires_absolute_directory")
      candidate_report = await asyncio.to_thread(load_candidate_bundle, report_directory)
    except (ValueError, TypeError, OSError, RuntimeError, ImportError):
      candidate_report = {"state": "invalid", "reason": "candidate_report_invalid", "report": None}

  app.on_startup.append(load_native_runtime)
  app.on_startup.append(load_report)
  app.on_startup.append(load_requests)
  app.on_startup.append(load_review)

  def response(session=None, *, request=None):
    session = controller.status() if session is None else session
    observed = session.get("recording", {}).get("settings", {})
    values = observed.get("values", {}) if observed.get("state") in ("observed", "partial") else {}
    result = {"ok": True, "capabilities": capabilities, "session": session, "native_runtime": deepcopy(native_public),
                              "parameters": readiness(values), "candidate_report": candidate_report,
                              "apply_requests": deepcopy(request_public["items"]),
                              "request_state": {"state": request_public["state"], "reason": request_public["reason"]},
                              "current_request_context": deepcopy(request_public["context"]),
                              "parking_summary": deepcopy(summary_public), "review_exports": deepcopy(export_public["jobs"]),
                              "review_export_state": {"state": export_public["state"], "reason": export_public["reason"]},
                              "apply_enabled": False, "recommendations_enabled": False}
    if request is not None:
      authorized = cookie_authorized(request)
      result["auth"] = {"authorized": authorized, "method": "native_cookie" if authorized else "none",
                        "manual_available": manual_configured, "native_available": access_sessions is not None}
    return web.json_response(result, headers={"Cache-Control": "no-store"})

  async def status(request):
    return response(request=request)

  def cookie_authorized(request):
    if access_sessions is None:
      return False
    origin = f"{request.scheme}://{request.host}"
    if len(request.headers.getall("Origin", [])) > 1 or request.headers.get("Origin", origin) != origin:
      return False
    if request.headers.get("Sec-Fetch-Site") in ("cross-site", "same-site"):
      return False
    incoming = request.cookies.get("ca_access", "")
    try:
      return bool(len(incoming) <= 256 and access_sessions.authorized(incoming, origin))
    except (ValueError, OSError, RuntimeError):
      return False

  def authorize_origin(request):
    if len(request.headers.getall("Origin", [])) != 1 or request.headers.get("Origin") != f"{request.scheme}://{request.host}":
      return _failure("origin_not_allowed", 403)
    if request.query:
      return _failure("request_query_not_allowed", 400)
    return None

  def authorize_control(request):
    error = authorize_origin(request)
    if error is not None:
      return error
    if not configured or len(request.headers.getall("X-Control-Advisor-Token", [])) > 1:
      return _failure("control_not_authorized", 403)
    incoming = request.headers.get("X-Control-Advisor-Token", "")
    manual = manual_configured and len(incoming) <= 256 and hmac.compare_digest(incoming.encode("utf-8"), token.encode("utf-8"))
    if not manual and not cookie_authorized(request):
      return _failure("control_not_authorized", 403)
    return None

  async def read_control(request, *, max_bytes=MAX_BODY_BYTES, grant_redeem=False):
    error = authorize_origin(request) if grant_redeem else authorize_control(request)
    if error is not None:
      return None, error
    if request.content_type != "application/json":
      return None, _failure("json_body_required", 400)
    if request.content_length is not None and request.content_length > max_bytes:
      return None, _failure("request_too_large", 413)

    async def bounded_body():
      chunks, size = [], 0
      while True:
        chunk = await request.content.read(min(1024, max_bytes + 1 - size))
        if not chunk:
          return b"".join(chunks)
        size += len(chunk)
        if size > max_bytes:
          raise OverflowError
        chunks.append(chunk)

    def unique(pairs):
      result = {}
      for key, value in pairs:
        if key in result:
          raise ValueError("duplicate_key")
        result[key] = value
      return result

    try:
      raw = await asyncio.wait_for(bounded_body(), 5.0)
      def finite_float(value):
        number = float(value)
        if not math.isfinite(number):
          raise ValueError("nonfinite_json")
        return number
      body = json.loads(raw.decode("utf-8"), object_pairs_hook=unique,
                        parse_float=finite_float,
                        parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite_json")))
      if not isinstance(body, dict):
        raise ValueError("invalid_body")
      return body, None
    except OverflowError:
      return None, _failure("request_too_large", 413)
    except (ValueError, UnicodeError, TimeoutError, RecursionError):
      return None, _failure("invalid_json", 400)

  async def start(request):
    body, error = await read_control(request)
    if error is not None:
      return error
    if set(body) != {"mode", "interval_seconds", "request_id"}:
      return _failure("invalid_start_fields", 400)
    try:
      validate_start(body["mode"], body["interval_seconds"], body["request_id"])
      if not capabilities["live_enabled" if body["mode"] == "live" else "synthetic_enabled"]:
        return _failure("mode_disabled", 409)
      session = await controller.start(**body)
      return response(session)
    except SessionConflict as exc:
      return _failure(str(exc), 409)
    except (ValueError, TypeError):
      return _failure("invalid_start_request", 400)

  async def redeem_access(request):
    body, error = await read_control(request, grant_redeem=True)
    if error is not None:
      return error
    if access_sessions is None:
      return _failure("native_access_unavailable", 403)
    if (set(body) != {"grant_id", "secret"} or type(body["grant_id"]) is not str or type(body["secret"]) is not str
        or re.fullmatch(r"[A-Za-z0-9_-]{1,128}", body["grant_id"]) is None
        or re.fullmatch(r"[A-Za-z0-9_-]{16,256}", body["secret"]) is None):
      return _failure("invalid_access_grant", 400)
    try:
      secret = await request_thread(access_sessions.redeem, body["grant_id"], body["secret"], f"{request.scheme}://{request.host}")
      result = web.json_response({"ok": True, "auth": {"authorized": True, "method": "native_cookie", "native_available": True,
                                                      "manual_available": manual_configured}}, headers={"Cache-Control": "no-store"})
      result.set_cookie("ca_access", secret, max_age=28800, httponly=True, secure=request.scheme == "https", samesite="Strict", path="/api/control_advisor")
      return result
    except (ValueError, OSError, RuntimeError):
      return _failure("access_grant_rejected", 403)

  async def stop(request):
    body, error = await read_control(request)
    if error is not None:
      return error
    if set(body) != {"session_id"} or not isinstance(body["session_id"], str) or not 1 <= len(body["session_id"]) <= 80:
      return _failure("invalid_stop_fields", 400)
    try:
      session = await controller.stop(body["session_id"])
      return response(session)
    except SessionConflict as exc:
      return _failure(str(exc), 409)

  async def native_operation(request, operation):
    body, error = await read_control(request)
    if error is not None:
      return error
    if native_runtime is None:
      return _failure("native_runtime_unavailable", 409)
    if operation == "confirm":
      if set(body) != {"group_id"} or body["group_id"] not in (None, "new") and (type(body["group_id"]) is not str or re.fullmatch(r"[a-f0-9]{32}", body["group_id"]) is None):
        return _failure("invalid_vehicle_group", 400)
      method, args = native_runtime.confirm_vehicle, (body["group_id"],)
    elif operation == "restore":
      if (set(body) not in ({"request_id", "restore_id"}, {"request_id", "restore_id", "mode"}) or body.get("mode", "live") not in ("live", "offroad")
          or any(type(body[key]) is not str or re.fullmatch(r"[A-Za-z0-9_-]{1,128}", body[key]) is None for key in ("request_id", "restore_id"))):
        return _failure("invalid_restore_request", 400)
      method, args = native_runtime.restore_request, (body["request_id"], body["restore_id"], body.get("mode", "live"))
    else:
      if body:
        return _failure("invalid_backup_request", 400)
      method, args = native_runtime.backup, ()
    try:
      value = await request_operation(method, *args)
      await refresh_native_snapshot()
      if operation == "backup":
        raw = json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")
        if len(raw) > 128 * 1024:
          raise ValueError("native_backup_limit")
        return web.json_response({"ok": True, "backup": value}, headers={"Cache-Control": "no-store"})
      return web.json_response({"ok": True, "native_runtime": deepcopy(native_public), **({"restore": value} if operation == "restore" else {})}, headers={"Cache-Control": "no-store"})
    except (ValueError, OSError, RuntimeError) as failure:
      reason = str(failure)
      if re.fullmatch(r"(?:native|vehicle)_[a-z0-9_]{1,112}", reason) is None:
        reason = "native_operation_unavailable"
      return _failure(reason, 409)

  async def confirm_vehicle(request):
    return await native_operation(request, "confirm")

  async def backup_vehicle(request):
    return await native_operation(request, "backup")

  async def restore_vehicle(request):
    return await native_operation(request, "restore")

  def remember_request(summary):
    if closed:
      return
    request_summaries[summary["request_id"]] = deepcopy(summary)
    items = [summary, *(item for item in request_public["items"] if item["request_id"] != summary["request_id"])]
    request_public["items"] = items[:16]

  def launch_request(operation):
    task = asyncio.create_task(operation)
    request_tasks.add(task)
    def finished(result):
      request_tasks.discard(result)
      if not result.cancelled():
        result.exception()
    task.add_done_callback(finished)
    return task

  async def execute_request(request_id):
    summary = await request_operation(request_actor.execute, request_id)
    remember_request(summary)
    await refresh_request_snapshot()

  async def register_request(body):
    summary = await request_operation(request_actor.register, body)
    remember_request(summary)
    if not closed and summary.get("state") == "registered" and (summary.get("scope") == "pc_simulation"
        or summary.get("scope") == "vehicle" and summary.get("apply_enabled") is True):
      launch_request(execute_request(summary["request_id"]))
    return summary

  async def cancel_request(request_id):
    summary = await request_operation(request_actor.cancel, request_id)
    remember_request(summary)
    return summary

  def request_failure(error):
    status = {"RequestConflict": 409, "RequestDisabled": 403, "RequestNotFound": 404}.get(type(error).__name__, 400)
    if re.fullmatch(r"(?:native|vehicle)_[a-z0-9_]{1,112}", str(error)):
      return _failure(str(error), status)
    return _failure({409: "request_conflict", 403: "request_registration_disabled", 404: "request_not_found"}.get(status, "invalid_apply_request"), status)

  async def create_apply_request(request):
    body, error = await read_control(request)
    if error is not None:
      return error
    if not capabilities["request_registration_enabled"] or request_actor is None:
      return _failure("request_registration_disabled", 403)
    if body.get("target_id") != request_target_id:
      return _failure("request_target_mismatch", 409)
    try:
      summary = await asyncio.shield(launch_request(register_request(body)))
      return web.json_response({"ok": True, "request": summary, "apply_enabled": False}, status=202, headers={"Cache-Control": "no-store"})
    except (ValueError, TypeError, OSError, RuntimeError, LookupError) as exc:
      return request_failure(exc)

  async def get_apply_request(request):
    request_id = request.match_info["request_id"]
    if re.fullmatch(r"[A-Za-z0-9_-]{1,128}", request_id) is None:
      return _failure("invalid_request_id", 400)
    summary = request_summaries.get(request_id)
    if summary is None:
      return _failure("request_not_found", 404)
    return web.json_response({"ok": True, "request": deepcopy(summary), "request_state": {"state": request_public["state"], "reason": request_public["reason"]},
                              "apply_enabled": False}, headers={"Cache-Control": "no-store"})

  async def cancel_apply_request(request):
    body, error = await read_control(request)
    if error is not None:
      return error
    request_id = request.match_info["request_id"]
    if body or re.fullmatch(r"[A-Za-z0-9_-]{1,128}", request_id) is None:
      return _failure("invalid_cancel_request", 400)
    if not capabilities["request_registration_enabled"] or request_actor is None:
      return _failure("request_registration_disabled", 403)
    try:
      summary = await asyncio.shield(launch_request(cancel_request(request_id)))
      return web.json_response({"ok": True, "request": summary, "apply_enabled": False}, headers={"Cache-Control": "no-store"})
    except (ValueError, TypeError, OSError, RuntimeError, LookupError) as exc:
      return request_failure(exc)

  def export_job(job_id):
    return next((item for item in export_public["jobs"] if item["job_id"] == job_id), None)

  def valid_job_id(job_id):
    return re.fullmatch(r"[a-f0-9]{32}", job_id) is not None

  def export_error(error):
    reason = str(error)
    allowed = {"export_busy", "export_total_quota", "export_request_conflict", "export_not_ready", "review_integrity_failed"}
    return _failure(reason if reason in allowed else "review_export_unavailable", 409 if reason in allowed else 400)

  def build_export(job_id, selection, cancelled, deadline):
    from openpilot.selfdrive.carrot.control_advisor.review_export import verify_review_pack
    remaining = deadline - time.monotonic()
    if remaining <= 0:
      raise TimeoutError("review_timeout")
    destination = export_store.root / job_id / "pack"
    record = export_store.records[job_id]
    result = review_builder.create_review_pack(destination, selection, include_private_raw=record["include_raw"], budget={**budget, "timeout_seconds": max(1, int(remaining))}, cancelled=cancelled.is_set)
    remaining = deadline - time.monotonic()
    if remaining <= 0:
      raise TimeoutError("review_timeout")
    verified = verify_review_pack(destination, budget={**budget, "timeout_seconds": max(1, int(remaining))}, cancelled=cancelled.is_set)
    if verified.get("state") != "verified" or any(verified.get(key) != result.get(key) for key in ("summary_id", "summary_revision", "zip_sha256", "manifest_sha256", "bytes", "zip_bytes")):
      raise ValueError("review_integrity_failed")
    return result

  async def run_export(job_id, body, cancellation):
    nonlocal export_active
    async with export_lock:
      work = None
      state, reason, result = "ready", None, None
      deadline = time.monotonic() + budget["timeout_seconds"]
      try:
        await request_thread(export_store.update, job_id, "running")
        await publish_exports()
        selection = await request_thread(summary_service.selection, body["summary_id"], body["summary_revision"], cancelled=cancellation.is_set, deadline=deadline)
        work = asyncio.create_task(asyncio.to_thread(build_export, job_id, selection, cancellation, deadline))
        result = await asyncio.wait_for(asyncio.shield(work), max(.01, deadline - time.monotonic()) + 1)
      except asyncio.CancelledError:
        cancellation.set()
        try:
          if work is not None:
            await work
        except Exception:
          pass
        state, reason = "interrupted", "export_server_stopped"
      except TimeoutError:
        cancellation.set()
        try:
          if work is not None:
            await work
        except Exception:
          pass
        state, reason = "timed_out", "review_timeout"
      except InterruptedError:
        state, reason = "cancelled", "review_cancelled"
      except Exception as error:
        code = str(error)
        limits = {"review_byte_limit", "review_file_limit", "review_metadata_limit", "summary_selection_read_budget", "summary_selection_file_budget", "summary_selection_checkpoint_budget"}
        changed = {"review_source_size_changed", "review_source_changed", "review_source_hash_changed", "review_file_size_changed", "review_checkpoint_file_missing",
                   "summary_candidate_artifact_changed", "summary_checkpoint_reference_changed", "summary_archive_manifest_changed", "summary_archive_size_changed"}
        state, reason = "failed", "review_export_limit" if code in limits else "review_export_source_changed" if code in changed else "review_export_failed"
      try:
        await request_thread(export_store.update, job_id, state, reason, result if state == "ready" else None)
        if state == "ready":
          export_store._verified_downloads[job_id] = await request_thread(export_store._fingerprint, export_store.root / job_id / "pack" / "review.zip")
      finally:
        export_active = None
        await publish_exports()

  async def prepare_export(body):
    nonlocal export_active
    cached = next((job for job in export_public["jobs"] if job["request_id"] == body["request_id"]), None)
    if cached is not None:
      if any(cached[key] != body[key] for key in ("summary_id", "summary_revision", "include_raw")):
        raise ValueError("export_request_conflict")
      return cached
    if export_active is not None and export_active.get("body") != body:
      raise ValueError("export_busy")
    async with export_lock:
      previous = await request_thread(export_store.find, body)
      if previous is not None:
        return previous
      if export_active is not None:
        raise ValueError("export_busy")
      cancellation = threading.Event()
      export_active = {"job_id": None, "cancel": cancellation, "body": deepcopy(body)}
      try:
        job = await request_thread(export_store.reserve, body)
      except BaseException:
        export_active = None
        raise
      export_active["job_id"] = job["job_id"]
      export_active["task"] = launch_request(run_export(job["job_id"], body, cancellation))
      await publish_exports()
      return job

  async def create_export_job(request):
    body, error = await read_control(request)
    if error is not None:
      return error
    if not capabilities["review_export_enabled"] or export_store is None:
      return _failure("review_export_disabled", 403)
    if (set(body) != EXPORT_BODY or any(type(body[key]) is not str or re.fullmatch(r"[A-Za-z0-9_-]{1,128}", body[key]) is None for key in ("request_id", "summary_id"))
        or type(body["summary_revision"]) is not int or body["summary_revision"] < 1 or type(body["include_raw"]) is not bool):
      return _failure("invalid_export_request", 400)
    try:
      job = await asyncio.shield(launch_request(prepare_export(body)))
      return web.json_response({"ok": True, "job": job}, status=202, headers={"Cache-Control": "no-store"})
    except (ValueError, OSError, RuntimeError, LookupError) as error:
      if export_store.failed:
        await publish_exports()
      return export_error(error)

  async def get_export_job(request):
    error = authorize_control(request)
    if error is not None:
      return error
    job_id = request.match_info["job_id"]
    if not valid_job_id(job_id):
      return _failure("invalid_export_job", 400)
    job = export_job(job_id)
    if job is None:
      return _failure("export_job_not_found", 404)
    return web.json_response({"ok": True, "job": deepcopy(job)}, headers={"Cache-Control": "no-store"})

  async def cancel_export_job(request):
    body, error = await read_control(request)
    if error is not None:
      return error
    job_id = request.match_info["job_id"]
    if body or not valid_job_id(job_id):
      return _failure("invalid_export_job", 400)
    job = export_job(job_id)
    if job is None:
      return _failure("export_job_not_found", 404)
    if export_active is not None and export_active["job_id"] == job_id:
      export_active["cancel"].set()
      job = {**job, "state": "cancelling", "reason": "export_cancel_requested", "download_ready": False}
      export_public["jobs"] = [job if item["job_id"] == job_id else item for item in export_public["jobs"]]
    return web.json_response({"ok": True, "job": job}, headers={"Cache-Control": "no-store"})

  async def grant_export_download(request):
    body, error = await read_control(request)
    if error is not None:
      return error
    job_id = request.match_info["job_id"]
    if body or not valid_job_id(job_id):
      return _failure("invalid_export_job", 400)
    if not capabilities["review_export_enabled"] or export_store is None:
      return _failure("review_export_disabled", 403)
    job = export_job(job_id)
    if job is None:
      return _failure("export_job_not_found", 404)
    try:
      async with download_lock:
        path = await request_thread(export_store.download, job_id)
        fingerprint = await request_thread(export_store._fingerprint, path)
      secret = secrets.token_urlsafe(32)
      was_invalid = job_id in invalid_downloads
      invalid_downloads.discard(job_id)
      if was_invalid:
        await publish_exports()
      download_grants[job_id] = {"digest": hashlib.sha256(secret.encode()).digest(), "expires": time.monotonic() + 60, "fingerprint": fingerprint}
      download_paths[job_id] = path
      location = f"/api/control_advisor/export-jobs/{job_id}/download"
      response = web.json_response({"ok": True, "download_url": location, "expires_in_seconds": 60}, headers={"Cache-Control": "no-store"})
      response.set_cookie("ca_review_download", secret, max_age=60, httponly=True, secure=request.secure, samesite="Strict", path=location)
      return response
    except (ValueError, OSError, RuntimeError, LookupError) as error:
      download_grants.pop(job_id, None)
      if job["state"] == "ready" and str(error) != "export_not_ready":
        invalid_downloads.add(job_id)
        await publish_exports()
      return export_error(error)

  async def download_export(request):
    job_id = request.match_info["job_id"]
    grant = download_grants.get(job_id)
    secret = request.cookies.get("ca_review_download", "")
    job = export_job(job_id)
    if (request.query or not valid_job_id(job_id) or grant is None or time.monotonic() >= grant["expires"] or not job or not job["download_ready"]
        or re.fullmatch(r"[A-Za-z0-9_-]{43}", secret) is None or not hmac.compare_digest(hashlib.sha256(secret.encode()).digest(), grant["digest"])
        or request.headers.get("Sec-Fetch-Site") in {"cross-site", "same-site"}
        or (request.headers.get("Origin") is not None and request.headers["Origin"] != f"{request.scheme}://{request.host}")):
      return _failure("download_not_authorized", 403)
    path = download_paths[job_id]
    try:
      if await request_thread(export_store._fingerprint, path) != grant["fingerprint"]:
        raise ValueError("review_integrity_failed")
    except (OSError, ValueError):
      download_grants.pop(job_id, None)
      invalid_downloads.add(job_id)
      await publish_exports()
      return _failure("download_integrity_unconfirmed", 409)
    return web.FileResponse(_export_path(path), headers={"Content-Type": "application/zip", "Content-Disposition": f'attachment; filename="control-advisor-review-{job_id}.zip"',
      "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer"})

  async def get_summary(request):
    return web.json_response({"ok": True, "parking_summary": deepcopy(summary_public)}, headers={"Cache-Control": "no-store"})

  def review_meta(entries):
    return {"review_count": len(entries), "latest_review_revision": max((item["review"]["review_revision"] for item in entries), default=None),
            "review_state": "recorded" if entries else "unreviewed"}

  def process_reviews(job_id, body=None):
    from openpilot.selfdrive.carrot.control_advisor.review_export import review_binding
    from openpilot.selfdrive.carrot.control_advisor.review_results import ReviewResultStore, review_version_status
    record = export_store.records[job_id]
    binding = None
    try:
      if record["state"] != "ready":
        raise ValueError("review_source_unavailable")
      binding = review_binding(export_store.root / job_id / "pack", budget=budget)
      if any(binding.get(key) != record[key] for key in ("manifest_sha256", "zip_sha256", "summary_id", "summary_revision")):
        raise ValueError("review_source_unavailable")
    except (ValueError, OSError):
      if body is not None:
        raise ValueError("review_source_unavailable") from None
      binding = None
    store = ReviewResultStore(export_store.root / job_id / "reviews", binding)
    wrapper = None
    if body is not None:
      if store.get_request(body["request_id"]) is None and (export_store.failed or export_store._usage() + 512 * 1024 > export_store.total_bytes):
        raise ValueError("review_store_limit")
      wrapper = store.record(body["request_id"], body["review"])
    entries = store.list_reviews()
    versions = {item["review_hash"]: review_version_status(item["review"], store.binding, source_available=binding is not None) for item in entries}
    return {"job_id": job_id, "summary_id": record["summary_id"], "summary_revision": record["summary_revision"],
      "binding_state": store.binding_state, "reviews": entries, "review": wrapper, "review_versions": versions}

  async def review_operation(job_id, body=None):
    if export_active is not None:
      raise ValueError("review_import_busy")
    async with export_lock:
      if export_active is not None:
        raise ValueError("review_import_busy")
      result = await request_thread(process_reviews, job_id, body)
      if not closed:
        review_metadata[job_id] = review_meta(result["reviews"])
        await publish_exports()
      return result

  def review_failure(error):
    conflicts = {"review_import_busy", "review_request_conflict", "review_revision_conflict", "review_previous_hash_mismatch", "review_binding_mismatch",
      "review_source_unavailable", "review_store_limit", "review_store_incomplete", "review_store_corrupt"}
    reason = str(error)
    safe = reason if re.fullmatch(r"review_[a-z0-9_]{1,100}", reason) else "review_registration_invalid"
    return _failure(safe, 409 if reason in conflicts or isinstance(error, OSError) else 400)

  async def codex_reviews(request, *, query=False):
    body, error = await read_control(request, max_bytes=MAX_BODY_BYTES if query else MAX_REVIEW_BODY_BYTES)
    if error is not None:
      return error
    job_id = request.match_info["job_id"]
    if not valid_job_id(job_id):
      return _failure("invalid_export_job", 400)
    if export_store is None:
      return _failure("review_export_disabled", 403)
    if job_id not in export_store.records:
      return _failure("export_job_not_found", 404)
    if query:
      if body:
        return _failure("review_fields_invalid", 400)
    elif (set(body) != {"request_id", "review"} or type(body["request_id"]) is not str
          or re.fullmatch(r"[A-Za-z0-9_-]{1,128}", body["request_id"]) is None or type(body["review"]) is not dict):
      return _failure("review_fields_invalid", 400)
    elif not capabilities["review_export_enabled"]:
      return _failure("review_export_disabled", 403)
    try:
      result = await asyncio.shield(launch_request(review_operation(job_id, None if query else body)))
      return web.json_response({"ok": True, **result}, headers={"Cache-Control": "no-store"})
    except (ValueError, OSError, RuntimeError, LookupError) as error:
      return review_failure(error)

  async def register_codex_review(request):
    return await codex_reviews(request)

  async def query_codex_reviews(request):
    return await codex_reviews(request, query=True)

  async def cleanup(application):
    nonlocal closed
    closed = True
    if access_sessions is not None:
      access_sessions.close()
    if export_active is not None:
      export_active["cancel"].set()
    if summary_task is not None:
      summary_task.cancel()
    if refresh_task is not None:
      refresh_task.cancel()
      try:
        await refresh_task
      except asyncio.CancelledError:
        pass
    if request_refresh_task is not None:
      request_refresh_task.cancel()
    for task in list(request_tasks):
      task.cancel()
    await asyncio.gather(*(task for task in (request_refresh_task, summary_task, *request_tasks) if task is not None), return_exceptions=True)
    if export_store is not None:
      await request_thread(export_store.close)
    if request_actor is not None:
      await request_operation(request_actor.close)
    if native_runtime is not None:
      await request_operation(native_runtime.close)
    await controller.close()

  app.router.add_get("/api/control_advisor/status", status)
  app.router.add_post("/api/control_advisor/access/redeem", redeem_access)
  app.router.add_post("/api/control_advisor/vehicle/confirm", confirm_vehicle)
  app.router.add_post("/api/control_advisor/vehicle/backup", backup_vehicle)
  app.router.add_post("/api/control_advisor/vehicle/restore", restore_vehicle)
  app.router.add_post("/api/control_advisor/start", start)
  app.router.add_post("/api/control_advisor/stop", stop)
  app.router.add_post("/api/control_advisor/apply-requests", create_apply_request)
  app.router.add_get("/api/control_advisor/apply-requests/{request_id}", get_apply_request)
  app.router.add_post("/api/control_advisor/apply-requests/{request_id}/cancel", cancel_apply_request)
  app.router.add_get("/api/control_advisor/summary", get_summary)
  app.router.add_post("/api/control_advisor/export-jobs", create_export_job)
  app.router.add_get("/api/control_advisor/export-jobs/{job_id}", get_export_job)
  app.router.add_post("/api/control_advisor/export-jobs/{job_id}/cancel", cancel_export_job)
  app.router.add_post("/api/control_advisor/export-jobs/{job_id}/download", grant_export_download)
  app.router.add_get("/api/control_advisor/export-jobs/{job_id}/download", download_export)
  app.router.add_post("/api/control_advisor/export-jobs/{job_id}/reviews", register_codex_review)
  app.router.add_post("/api/control_advisor/export-jobs/{job_id}/reviews/query", query_codex_reviews)
  app.on_cleanup.append(cleanup)
  return controller
