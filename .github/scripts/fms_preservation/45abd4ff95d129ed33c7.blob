"""Synthetic host sizing, not a device or driving qualification benchmark.

Run from the repository root with `python -m
openpilot.selfdrive.carrot.control_advisor.tests.benchmark_host`.
"""
import json
import time
import tracemalloc

from openpilot.selfdrive.carrot.control_advisor.analysis import AnalysisProfile, Observation, analyze


def run():
  profile = AnalysisProfile("synthetic-benchmark-not-vehicle-validated", 100_000_000, 200_000_000,
                            200_000_000, 100_000_000, max_samples=100_000)
  results = []
  for seconds in (180, 300):
    def rows():
      for index in range(seconds * 100):
        for axis in ("lateral", "longitudinal"):
          yield Observation(index * 10_000_000, axis, 1.0, 0.9, True, True,
                            2000 <= index % 6000 < 2030, True, "synthetic-A",
                            {"saturated": False, "output_limited": False})

    started = time.perf_counter()
    tracemalloc.start()
    result = analyze(rows(), profile)
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    elapsed = time.perf_counter() - started
    results.append({"simulated_seconds": seconds, "observations": seconds * 200,
                    "wall_seconds_with_tracemalloc": elapsed, "python_allocated_peak_bytes": peak,
                    "eligible_count": sum(axis["epochs"]["synthetic-A"]["eligible_count"] for axis in result["axes"].values()),
                    "result_json_bytes": len(json.dumps(result, allow_nan=False).encode("utf-8"))})
  return {"scope": "synthetic_host_analysis_only", "not_measured": ["device_cpu", "rss", "thermal", "control_latency",
                                                                       "rlog_io", "disk_copy", "candidate_fit"], "runs": results}


if __name__ == "__main__":
  print(json.dumps(run(), indent=2, allow_nan=False))
