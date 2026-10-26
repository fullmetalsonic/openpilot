"""Disk-backed comparison inputs and exact bounded-memory statistics.

The existing pure mask is evaluated with a proved finite overlap. Native input
bytes, timestamp order, all mask rows and exclusion intervals are retained.
"""
from array import array
from collections import Counter
from copy import deepcopy
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import uuid
import zlib

from .analysis import Observation, _error
from .candidate_report import _read
from .comparison_intervals import encode_exclusions, decode_exclusions
from .evidence import hash_file, _sync_directory

DISK_LIMIT = 512 * 1024 * 1024
PAGE_BYTES = 32 * 1024
MAX_PAGES = 128


def _json(value):
  return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _regular(path):
  path = Path(path)
  if not path.is_absolute() or any(part.is_symlink() or (hasattr(part, "is_junction") and part.is_junction()) for part in (path, *path.parents)):
    raise ValueError("comparison_disk_path_invalid")
  return path


def _unpack(blob):
  decoder = zlib.decompressobj()
  raw = decoder.decompress(blob, 256 * 1024 + 1)
  if len(raw) > 256 * 1024 or not decoder.eof or decoder.unused_data or decoder.unconsumed_tail:
    raise ValueError("comparison_disk_row_invalid")
  return json.loads(raw)


class InputWriter:
  def __init__(self, directory, *, kind="observations"):
    if kind not in {"observations", "events"}:
      raise ValueError("comparison_disk_kind_invalid")
    self.directory = _regular(directory)
    self.directory.mkdir(parents=True, exist_ok=True)
    self.path = self.directory / (kind + "-" + uuid.uuid4().hex + ".sqlite3")
    self.kind, self.count, self.last = kind, 0, -1
    self.connection = sqlite3.connect(self.path)
    self.connection.execute("PRAGMA cache_size=-2048")
    self.connection.execute("CREATE TABLE data(seq INTEGER PRIMARY KEY,stamp INTEGER,role TEXT,event_sequence INTEGER,digest TEXT UNIQUE,row BLOB)")
    self.connection.execute("CREATE INDEX times ON data(stamp,role,event_sequence)")
    self.connection.execute("CREATE INDEX role_times ON data(role,stamp,event_sequence)")

  def append(self, value):
    if self.connection is None:
      raise ValueError("comparison_disk_writer_closed")
    encoded = _json(value)
    if len(encoded) > 256 * 1024:
      raise ValueError("comparison_disk_row_limit")
    stamp = value["mono_ns"] if self.kind == "observations" else value["produced_mono_ns"]
    if self.kind == "observations" and (type(stamp) is not int or stamp <= self.last):
      raise ValueError("comparison_observation_order_or_axis_invalid")
    digest = hashlib.sha256(encoded).hexdigest()
    cursor = self.connection.execute("INSERT OR IGNORE INTO data(stamp,role,event_sequence,digest,row) VALUES(?,?,?,?,?)",
      (stamp, value.get("consumer_id", ""), value.get("sequence", 0), digest, zlib.compress(encoded)))
    if cursor.rowcount:
      self.count += 1
    self.last = stamp
    if cursor.rowcount and self.count % 2048 == 0:
      self.connection.commit()
      if self.path.stat().st_size > DISK_LIMIT:
        raise ValueError("comparison_disk_budget")

  def close(self):
    if self.connection is None:
      return
    self.connection.commit()
    self.connection.close()
    self.connection = None

  def finish(self):
    self.close()
    digest, size = hash_file(self.path, DISK_LIMIT)
    canonical = self.directory / (digest + ".sqlite3")
    if canonical == self.path:
      pass
    elif canonical.exists():
      if hash_file(canonical, DISK_LIMIT) != (digest, size):
        raise ValueError("comparison_disk_source_changed")
      self.path.unlink()
    else:
      self.path.rename(canonical)
      _sync_directory(self.directory)
    self.path = canonical
    return {"encoding": "sqlite-" + self.kind + "-v1", "path": str(self.path), "sha256": digest, "bytes": size, "count": self.count}


class InputReader:
  def __init__(self, reference, *, kind):
    if (type(reference) is not dict or set(reference) != {"encoding", "path", "sha256", "bytes", "count"}
        or reference["encoding"] != "sqlite-" + kind + "-v1" or type(reference["count"]) is not int or reference["count"] < 1
        or type(reference["path"]) is not str or type(reference["bytes"]) is not int or not 1 <= reference["bytes"] <= DISK_LIMIT
        or type(reference["sha256"]) is not str or re.fullmatch(r"[a-f0-9]{64}", reference["sha256"]) is None):
      raise ValueError("comparison_disk_reference_invalid")
    self.path = _regular(reference["path"])
    self.reference = deepcopy(reference)
    if hash_file(self.path, DISK_LIMIT) != (reference["sha256"], reference["bytes"]):
      raise ValueError("comparison_disk_source_changed")
    self.connection = sqlite3.connect(self.path.as_uri() + "?mode=ro&immutable=1", uri=True)
    self.connection.execute("PRAGMA cache_size=-2048")
    if self.connection.execute("SELECT count(*) FROM data").fetchone()[0] != reference["count"]:
      raise ValueError("comparison_disk_count_changed")

  def rows(self):
    for (blob,) in self.connection.execute("SELECT row FROM data ORDER BY stamp,role,event_sequence"):
      yield _unpack(blob)

  def get(self, sequence):
    return _unpack(self.connection.execute("SELECT row FROM data WHERE seq=?", (sequence,)).fetchone()[0])

  def range(self, lower, upper, *, maximum=8192):
    predecessor = self.connection.execute("SELECT stamp FROM data WHERE stamp<? ORDER BY stamp DESC LIMIT 1", (lower,)).fetchone()
    if predecessor is not None:
      lower = predecessor[0]
    if self.connection.execute("SELECT count(*) FROM data WHERE stamp>=? AND stamp<=?", (lower, upper)).fetchone()[0] > maximum:
      raise ValueError("comparison_chunk_sample_budget")
    rows, size = [], 0
    for (blob,) in self.connection.execute("SELECT row FROM data WHERE stamp>=? AND stamp<=? ORDER BY stamp", (lower, upper)):
      row = _unpack(blob)
      size += len(_json(row))
      if size > 4 * 1024 * 1024:
        raise ValueError("comparison_chunk_byte_budget")
      rows.append(row)
    return rows

  def by_stamp(self, stamp):
    return _unpack(self.connection.execute("SELECT row FROM data WHERE stamp=?", (stamp,)).fetchone()[0])

  def close(self):
    self.connection.close()
    if hash_file(self.path, DISK_LIMIT) != (self.reference["sha256"], self.reference["bytes"]):
      raise ValueError("comparison_disk_source_changed")


class DiskTimeline:
  def __init__(self, reader, role):
    self.reader, self.role = reader, role
    self.count = reader.connection.execute("SELECT count(*) FROM data WHERE role=?", (role,)).fetchone()[0]
    self.stamps = array("q", (row[0] for row in reader.connection.execute("SELECT stamp FROM data WHERE role=? ORDER BY stamp,event_sequence", (role,))))
    if len(self.stamps) * self.stamps.itemsize > 4 * 1024 * 1024:
      raise ValueError("comparison_consumer_index_budget")
    self._cached = None

  def __len__(self):
    return self.count

  def __getitem__(self, index):
    if index < 0:
      index += self.count
    if not 0 <= index < self.count:
      raise IndexError(index)
    if self._cached is not None and self._cached[0] == index:
      return self._cached[1]
    row = self.reader.connection.execute("SELECT row FROM data WHERE role=? AND stamp=? ORDER BY event_sequence LIMIT 1", (self.role, self.stamps[index])).fetchone()
    if row is None:
      raise IndexError(index)
    event = _unpack(row[0])
    self._cached = (index, event)
    return event

  def __iter__(self):
    for (blob,) in self.reader.connection.execute("SELECT row FROM data WHERE role=? ORDER BY stamp,event_sequence", (self.role,)):
      yield _unpack(blob)


def _disk_metrics(connection, condition="1", parameters=()):
  count, scale = connection.execute("SELECT count(*),max(abs(error)) FROM stats WHERE error IS NOT NULL AND " + condition, parameters).fetchone()
  if not count:
    return {"count": 0, "bias": None, "rmse": None, "p95_absolute_error": None}
  def errors():
    return (row[0] for row in connection.execute("SELECT error FROM stats WHERE error IS NOT NULL AND " + condition + " ORDER BY stamp", parameters))
  bias = scale * (math.fsum(value / scale for value in errors()) / count) if scale else 0.
  rmse = scale * math.sqrt(math.fsum((value / scale) ** 2 for value in errors()) / count) if scale else 0.
  p95 = connection.execute("SELECT abs(error) FROM stats WHERE error IS NOT NULL AND " + condition + " ORDER BY abs(error),stamp LIMIT 1 OFFSET ?", (*parameters, math.ceil(count * .95) - 1)).fetchone()[0]
  return {"count": count, "bias": bias, "rmse": rmse, "p95_absolute_error": p95}


def validate_interval_reference(value):
  if (type(value) is not dict or set(value) != {"encoding", "path", "sha256", "interval_count", "page_count"}
      or value["encoding"] != "reason-indexed-pages-v1" or type(value["sha256"]) is not str
      or re.fullmatch(r"[a-f0-9]{64}", value["sha256"]) is None
      or value["path"] != "comparison-details/" + value["sha256"] + "/manifest.json"
      or type(value["interval_count"]) is not int or not 0 <= value["interval_count"] <= 2**53 - 1
      or type(value["page_count"]) is not int or not 0 <= value["page_count"] <= MAX_PAGES
      or (value["interval_count"] == 0) != (value["page_count"] == 0)):
    raise ValueError("comparison_interval_reference_invalid")
  return deepcopy(value)


def write_interval_pages(connection, output_root):
  root = _regular(output_root) / "comparison-details"
  root.mkdir(exist_ok=True)
  stage = root / ("stage-" + uuid.uuid4().hex)
  stage.mkdir()
  pages, rows, total = [], [], 0
  def write(path, encoded):
    with path.open("xb") as stream:
      if stream.write(encoded) != len(encoded):
        raise OSError("comparison_interval_short_write")
      stream.flush()
      os.fsync(stream.fileno())
  def write_page(values):
    nonlocal total
    encoded = _json(encode_exclusions(values))
    if len(encoded) > PAGE_BYTES and len(values) > 1:
      middle = len(values) // 2
      write_page(values[:middle])
      write_page(values[middle:])
      return
    if len(pages) >= MAX_PAGES or len(encoded) > PAGE_BYTES:
      raise ValueError("comparison_interval_page_budget")
    name = f"{len(pages):06d}.json"
    write(stage / name, encoded)
    pages.append({"index": len(pages), "path": name, "sha256": hashlib.sha256(encoded).hexdigest(), "bytes": len(encoded), "count": len(values)})
    total += len(values)

  def flush():
    nonlocal rows, total
    if not rows:
      return
    write_page(rows)
    rows = []
  for reason, start, end, count in connection.execute("SELECT reason,start,end,count FROM intervals ORDER BY start,reason"):
    row = {"reason": reason, "start_ns": start, "end_ns": end, "sample_count": count}
    if len(rows) >= 512:
      flush()
    rows.append(row)
  flush()
  manifest = {"schema_version": 1, "kind": "comparison_exclusion_pages", "interval_count": total, "pages": pages}
  encoded = _json(manifest)
  if len(encoded) > PAGE_BYTES or sum(page["bytes"] for page in pages) + len(encoded) > 4 * 1024 * 1024:
    raise ValueError("comparison_interval_manifest_budget")
  digest = hashlib.sha256(encoded).hexdigest()
  write(stage / "manifest.json", encoded)
  _sync_directory(stage)
  destination = root / digest
  if destination.exists():
    if (destination / "manifest.json").read_bytes() != encoded:
      raise ValueError("comparison_interval_manifest_changed")
    for path in stage.iterdir():
      path.unlink()
    stage.rmdir()
  else:
    stage.rename(destination)
    _sync_directory(root)
  return validate_interval_reference({"encoding": "reason-indexed-pages-v1", "path": "comparison-details/" + digest + "/manifest.json",
    "sha256": digest, "interval_count": total, "page_count": len(pages)})


def read_interval_page(output_root, reference, page_index):
  reference = validate_interval_reference(reference)
  if type(page_index) is not int or not 0 <= page_index < reference["page_count"]:
    raise ValueError("comparison_interval_page_invalid")
  path = _regular(Path(output_root) / reference["path"])
  manifest, digest = _read(path, PAGE_BYTES)
  if (type(manifest) is not dict or set(manifest) != {"schema_version", "kind", "interval_count", "pages"}
      or digest != reference["sha256"] or type(manifest["schema_version"]) is not int or manifest["schema_version"] != 1 or manifest.get("kind") != "comparison_exclusion_pages"
      or manifest.get("interval_count") != reference["interval_count"] or len(manifest.get("pages", [])) != reference["page_count"]):
    raise ValueError("comparison_interval_manifest_changed")
  total_bytes = total_count = 0
  for index, page in enumerate(manifest["pages"]):
    if (type(page) is not dict or set(page) != {"index", "path", "sha256", "bytes", "count"}
        or type(page["index"]) is not int or page["index"] != index or page["path"] != f"{index:06d}.json"
        or type(page["sha256"]) is not str or re.fullmatch(r"[a-f0-9]{64}", page["sha256"]) is None
        or type(page["bytes"]) is not int or not 1 <= page["bytes"] <= PAGE_BYTES
        or type(page["count"]) is not int or not 1 <= page["count"] <= 512):
      raise ValueError("comparison_interval_page_invalid")
    total_bytes += page["bytes"]
    total_count += page["count"]
  if total_count != reference["interval_count"] or total_bytes + path.stat().st_size > 4 * 1024 * 1024:
    raise ValueError("comparison_interval_manifest_changed")
  entry = manifest["pages"][page_index]
  if entry.get("index") != page_index or entry.get("path") != f"{page_index:06d}.json":
    raise ValueError("comparison_interval_page_invalid")
  payload, digest = _read(_regular(path.parent / entry["path"]), PAGE_BYTES)
  if digest != entry.get("sha256") or hash_file(path.parent / entry["path"], PAGE_BYTES)[1] != entry.get("bytes") or len(decode_exclusions(payload, 2)) != entry.get("count"):
    raise ValueError("comparison_interval_page_changed")
  return payload


class DiskResult:
  def __init__(self, reader):
    self.reader = reader
    self.path = reader.path.with_name("stats-" + uuid.uuid4().hex + ".sqlite3")
    self.connection = sqlite3.connect(self.path)
    self.connection.execute("PRAGMA cache_size=-2048")
    self.connection.execute("CREATE TABLE stats(stamp INTEGER PRIMARY KEY,error REAL,eligible INTEGER,speed_bin INTEGER,target_bin INTEGER)")
    self.connection.execute("CREATE INDEX selected_bins ON stats(eligible,speed_bin,target_bin)")
    self.connection.execute("CREATE TABLE intervals(reason TEXT,start INTEGER,end INTEGER,count INTEGER)")
    self.closed = False

  def close(self):
    if not self.closed:
      self.closed = True
      self.connection.close()
      self.reader.close()
      # Only this uniquely created, unreferenced scratch is removed. Original
      # native data, immutable inputs and published interval pages remain.
      self.path.unlink()


class DiskRows:
  def __init__(self, owner, *, eligible):
    self.owner, self.eligible = owner, eligible

  def __iter__(self):
    condition = " WHERE eligible=1" if self.eligible else ""
    for (stamp,) in self.owner.connection.execute("SELECT stamp FROM stats" + condition + " ORDER BY stamp"):
      item = Observation(**self.owner.reader.by_stamp(stamp))
      yield item if self.eligible else (item, set())


def streaming_side(side, axis, profile, mask, timelines, expected, key, source_floor, plain_side, *, chunk_rows=1024):
  from .comparison import _bin
  reader = InputReader(side["observations"], kind="observations")
  result = DiskResult(reader)
  try:
    window, connection = side["window"], result.connection
    lower, upper, boundary = (window[name] for name in ("start_ns", "end_ns", "finalized_through_ns"))
    pending = reader.connection.execute("SELECT count(*) FROM data WHERE stamp>=? AND stamp<=?", (lower, upper)).fetchone()[0]
    if not pending:
      raise ValueError("comparison_window_empty")
    # There are two mask passes. Held-source dependency gating can trigger the
    # second recovery after the first raw recovery; one recovery is insufficient.
    guard = 2 * (mask.pre_margin_ns + mask.recovery_ns + mask.dependency_ns + mask.max_gap_ns)
    counts, active_intervals = Counter(), {}
    limit_counts = dict.fromkeys(("saturated_known_count", "output_limited_known_count", "fully_known_count", "unknown_count", "limited_count"), 0)
    sample_count = excluded = provisional = observed_duration = eligible_duration = 0
    previous = None
    previous_eligible = False
    while pending:
      take = chunk_rows if pending > 2 * chunk_rows else pending
      stamps = [row[0] for row in reader.connection.execute("SELECT stamp FROM data WHERE stamp>=? AND stamp<=? ORDER BY stamp LIMIT ?", (lower, upper, take))]
      lo, hi = stamps[0], stamps[-1]
      local = {**side, "observations": reader.range(max(0, lo - guard), hi + guard, maximum=min(mask.max_samples, 8192)),
               "window": {"start_ns": lo, "end_ns": hi, "finalized_through_ns": min(boundary, hi)}}
      # The last batch is merged into its predecessor so the original safe
      # boundary stays inside its window, including the provisional tail.
      if local["window"]["finalized_through_ns"] < lo:
        raise ValueError("comparison_chunk_finalization_invalid")
      _, _, records = plain_side(local, axis, profile, mask, timelines, expected, key, source_floor)
      if len(records) != len(stamps):
        raise ValueError("comparison_chunk_coverage_changed")
      for item, reasons in records:
        eligible = item.mono_ns <= boundary and not reasons
        error, _ = _error(item)
        speed = item.context.get("v_ego")
        speed_bin = _bin(speed, profile["speed_bins_mps"]) if type(speed) in (int, float) and math.isfinite(speed) else None
        target_bin = _bin(item.target, profile["target_bins"]) if error is not None else None
        connection.execute("INSERT INTO stats VALUES(?,?,?,?,?)", (item.mono_ns, error, eligible, speed_bin, target_bin))
        sample_count += 1
        counts.update(reasons)
        saturated = type(item.context.get("saturated")) is bool
        limited = type(item.context.get("output_limited")) is bool
        limit_counts["saturated_known_count"] += saturated
        limit_counts["output_limited_known_count"] += limited
        limit_counts["fully_known_count"] += saturated and limited
        limit_counts["unknown_count"] += not (saturated and limited)
        limit_counts["limited_count"] += item.context.get("saturated") is True or item.context.get("output_limited") is True
        if previous is not None and item.mono_ns - previous <= mask.max_gap_ns:
          observed_duration += item.mono_ns - previous
          if previous_eligible and eligible:
            eligible_duration += item.mono_ns - previous
        current = reasons if item.mono_ns <= boundary else set()
        provisional += item.mono_ns > boundary
        excluded += item.mono_ns <= boundary and bool(reasons)
        for reason in list(active_intervals):
          if reason not in current:
            start, end, count = active_intervals.pop(reason)
            connection.execute("INSERT INTO intervals VALUES(?,?,?,?)", (reason, start, end, count))
        for reason in sorted(current):
          if reason not in active_intervals:
            active_intervals[reason] = [item.mono_ns, item.mono_ns, 0]
          active_intervals[reason][1] = item.mono_ns
          active_intervals[reason][2] += 1
        previous, previous_eligible = item.mono_ns, eligible
      pending -= take
      lower = hi + 1
      connection.commit()
      if result.path.stat().st_size + reader.reference["bytes"] > DISK_LIMIT:
        raise ValueError("comparison_disk_budget")
    for reason, (start, end, count) in active_intervals.items():
      connection.execute("INSERT INTO intervals VALUES(?,?,?,?)", (reason, start, end, count))
    connection.commit()
    eligible_metrics = _disk_metrics(connection, "eligible=1")
    summary = {"window_start_ns": window["start_ns"], "window_end_ns": window["end_ns"], "raw_metrics": _disk_metrics(connection),
      "eligible_metrics": eligible_metrics, "sample_count": sample_count, "eligible_count": eligible_metrics["count"],
      "excluded_count": excluded, "provisional_count": provisional, "observed_duration_ns": observed_duration,
      "eligible_duration_ns": eligible_duration, "output_limit_observation": limit_counts,
      "scope_limitations": ["output_limit_state_unknown"] if limit_counts["unknown_count"] else [],
      "exclusion_counts": dict(sorted(counts.items())), "exclusions": write_interval_pages(connection, side["comparison_output_root"])}
    return summary, DiskRows(result, eligible=True), DiskRows(result, eligible=False)
  except BaseException:
    result.close()
    raise


def streaming_matched(before, after, profile, mask):
  sides = [before.owner.connection, after.owner.connection]
  bins = [{(a, b): count for a, b, count in db.execute("SELECT speed_bin,target_bin,count(*) FROM stats WHERE eligible=1 AND speed_bin IS NOT NULL AND target_bin IS NOT NULL GROUP BY speed_bin,target_bin")} for db in sides]
  common = sorted(set(bins[0]) & set(bins[1]))
  if not common:
    raise ValueError("comparison_matched_context_unavailable")
  masses = {key: min(bins[0][key], bins[1][key]) for key in common}
  total = sum(masses.values())
  metrics, durations = [], []
  for db, counts in zip(sides, bins):
    biases, rmses = [], []
    db.execute("CREATE TEMP TABLE IF NOT EXISTS weights(speed_bin INTEGER,target_bin INTEGER,weight REAL,PRIMARY KEY(speed_bin,target_bin))")
    db.execute("DELETE FROM weights")
    for key in common:
      mass = masses[key] / total
      value = _disk_metrics(db, "eligible=1 AND speed_bin=? AND target_bin=?", key)
      biases.append(mass * value["bias"])
      rmses.append((mass, value["rmse"]))
      db.execute("INSERT INTO weights VALUES(?,?,?)", (*key, mass / counts[key]))
    cumulative, p95 = 0., None
    for error, weight in db.execute("SELECT abs(error),weight FROM stats JOIN weights USING(speed_bin,target_bin) WHERE eligible=1 ORDER BY abs(error),speed_bin,target_bin,stamp"):
      p95 = error
      cumulative += weight
      if cumulative >= .95:
        break
    scale = max(value for _, value in rmses)
    metrics.append({"count": sum(counts[key] for key in common), "bias": math.fsum(biases),
      "rmse": scale * math.sqrt(math.fsum(mass * (value / scale) ** 2 for mass, value in rmses)) if scale else 0., "p95_absolute_error": p95})
    previous, previous_selected, duration = None, False, 0
    for stamp, eligible, speed_bin, target_bin in db.execute("SELECT stamp,eligible,speed_bin,target_bin FROM stats ORDER BY stamp"):
      selected = eligible and (speed_bin, target_bin) in masses
      if previous is not None and stamp - previous <= mask.max_gap_ns and previous_selected and selected:
        duration += stamp - previous
      previous, previous_selected = stamp, selected
    durations.append(duration)
  return {"weighting": "common_stratum_minimum_count", "common_bin_count": len(common),
    "bins": [{"speed_bin": key[0], "target_bin": key[1], "before_count": bins[0][key], "after_count": bins[1][key], "weight": masses[key] / total} for key in common],
    "before": metrics[0], "after": metrics[1], "before_duration_ns": durations[0], "after_duration_ns": durations[1],
    "after_minus_before": {key: metrics[1][key] - metrics[0][key] for key in ("bias", "rmse", "p95_absolute_error")}}


def streaming_consumers(application, cohort, item, required, validate_event, field_matches):
  reader = InputReader(cohort["consumer_events"], kind="events")
  try:
    scale = 1 if item["key"] == "SpeedTFFactor" else item["raw_scale"]
    old, new = application["old_raw"] * scale, application["candidate_raw"] * scale
    previous, boundaries, baseline = {}, {}, set()
    for event in reader.rows():
      validate_event(event)
      role = event["consumer_id"]
      if role not in required:
        continue
      if event["boot_id"] != cohort["boot_id"] or any(digest is None or cohort["before"]["code_hashes"].get(path) != digest for path, digest in event["source_hashes"].items()):
        raise ValueError("comparison_consumer_identity_or_code_changed")
      prior = previous.get(role)
      if prior is not None and (event["produced_mono_ns"] <= prior["produced_mono_ns"] or event["sequence"] != prior["sequence"] + 1
          or event["dropped_events"] != prior["dropped_events"] or any(event[key] != prior[key] for key in ("pid", "process_instance_id", "consumer_instance_id"))):
        raise ValueError("comparison_consumer_gap_or_restart")
      if prior is None and event["dropped_events"]:
        raise ValueError("comparison_consumer_gap_or_restart")
      previous[role] = event
      matches_new = field_matches(event, item["key"], new)
      if role not in boundaries and event["produced_mono_ns"] >= application["stored_mono_ns"] and matches_new:
        boundaries[role] = event["produced_mono_ns"]
      if role not in boundaries and field_matches(event, item["key"], old):
        baseline.add(role)
      if role in boundaries and not matches_new:
        raise ValueError("comparison_consumer_baseline_or_reversion")
    if set(boundaries) != set(required):
      raise ValueError("required_consumer_observation_missing")
    if baseline != set(required):
      raise ValueError("comparison_consumer_baseline_or_reversion")
    timelines = {role: DiskTimeline(reader, role) for role in required}
    if sum(len(timeline.stamps) * timeline.stamps.itemsize for timeline in timelines.values()) > 4 * 1024 * 1024:
      raise ValueError("comparison_consumer_index_budget")
    return timelines, max(boundaries.values()), old, new
  except BaseException:
    reader.close()
    raise
