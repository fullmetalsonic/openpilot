"""PC/offroad diagnostic CLI. No control process, Params access or network calls."""
from __future__ import annotations

import argparse
from dataclasses import fields
import json
import os
from pathlib import Path
import sys

from .analysis import AnalysisProfile, Observation, analyze
from .evidence import create_bundle, hash_file, verify_bundle
from .registry import load_registry, readiness

MAX_LINE_BYTES = 64 * 1024
DEFAULT_FILE_BUDGET = 512 * 1024 * 1024


def strict_json(text: str):
  def reject(value):
    raise ValueError(f"nonfinite_json:{value}")

  def unique(pairs):
    result = {}
    for key, value in pairs:
      if key in result:
        raise ValueError(f"duplicate_json_key:{key}")
      result[key] = value
    return result

  return json.loads(text, parse_constant=reject, object_pairs_hook=unique)


def read_profile(path: Path) -> tuple[AnalysisProfile, dict]:
  if path.stat().st_size > MAX_LINE_BYTES:
    raise ValueError("profile_size_limit")
  data = strict_json(path.read_text(encoding="utf-8"))
  if not isinstance(data, dict) or data.get("schema_version") != 1:
    raise ValueError("unsupported_profile_schema")
  if data.get("validation_status") not in ("synthetic_only", "not_validated"):
    raise ValueError("this_offline_tool_does_not_accept_qualified_vehicle_profiles")
  names = {field.name for field in fields(AnalysisProfile)}
  unknown = set(data) - names - {"schema_version", "validation_status", "max_age_ns"}
  if unknown:
    raise ValueError("unknown_profile_fields:" + ",".join(sorted(unknown)))
  return AnalysisProfile(**{key: value for key, value in data.items() if key in names}), data


def read_jsonl(path: Path, metadata: dict):
  keys = {field.name for field in fields(Observation)}
  with path.open("rb") as stream:
    line = stream.readline(MAX_LINE_BYTES + 1)
    if len(line) > MAX_LINE_BYTES:
      raise ValueError("input_line_limit")
    header = strict_json(line.decode("utf-8"))
    if not isinstance(header, dict) or header.get("type") != "session" or header.get("schema_version") != 1:
      raise ValueError("missing_session_header")
    if header.get("source_kind") not in ("synthetic", "normalized_rlog", "qlog", "live_capture"):
      raise ValueError("unknown_source_kind")
    settings = header.get("settings", {})
    if not isinstance(settings, dict):
      raise ValueError("invalid_settings_snapshot")
    allowed = {item["key"] for item in load_registry()["items"]}
    metadata.update({"source_kind": header["source_kind"], "declared_code_commit": header.get("code_commit"),
                     "settings_history": "unverified_declaration", "session_id": header.get("session_id"),
                     "settings": {key: value for key, value in settings.items() if key in allowed}})
    for key, value in metadata["settings"].items():
      if type(value) is not int:
        raise ValueError(f"raw_setting_must_be_integer:{key}")
    while True:
      line = stream.readline(MAX_LINE_BYTES + 1)
      if not line:
        break
      if len(line) > MAX_LINE_BYTES:
        raise ValueError("input_line_limit")
      row = strict_json(line.decode("utf-8"))
      if not isinstance(row, dict) or set(row) - keys:
        raise ValueError("unknown_observation_fields")
      yield Observation(**row)


def run_diagnostic(args) -> dict:
  sources = [args.input, args.profile]
  package = Path(__file__).resolve().parent
  analyzer_files = ("analysis.py", "rlog.py", "normalizer.py", "registry.py", "parameters.json", "evidence.py", "__main__.py")
  analyzer_hashes = {name: hash_file(package / name, 4 * 1024 * 1024)[0] for name in analyzer_files}
  # Bind the analysis to the exact original bytes, not a later changed file.
  hashes = [hash_file(path, args.byte_limit)[0] for path in sources]
  profile, profile_data = read_profile(args.profile)
  metadata = {}
  if args.command == "analyze-jsonl":
    observations = read_jsonl(args.input, metadata)
  else:
    from .rlog import iter_observations
    age = profile_data.get("max_age_ns")
    if type(age) is not int or age <= 0:
      raise ValueError("rlog_requires_explicit_max_age_ns")
    metadata = {"source_kind": "synthetic" if args.synthetic else args.log_kind, "settings_history": "unknown",
                "settings": {}, "declared_code_commit": None, "recording_schema_match": "unverified"}
    observations = (Observation(**row) for row in iter_observations(
      args.input, epoch=args.epoch, max_age_ns=age, max_decompressed_bytes=args.byte_limit))
  diagnostic = analyze(observations, profile)
  if any(hash_file(package / name, 4 * 1024 * 1024)[0] != digest for name, digest in analyzer_hashes.items()):
    raise ValueError("analyzer_changed_during_analysis")
  eligible = sum(epoch["eligible_count"] for axis in diagnostic["axes"].values() for epoch in axis["epochs"].values())
  result = {"schema_version": 1, "scope": "offline_diagnostic", "source": metadata,
            "analysis_status": "diagnostic_only" if eligible else "insufficient_eligible_data",
            "analyzer_sha256": analyzer_hashes,
            "profile": profile_data, "diagnostic": diagnostic,
            "recommendations_enabled": False, "apply_enabled": False, "runtime_connected": False,
            "parameter_readiness": readiness(metadata.get("settings")),
            "limitations": ["vehicle_profiles_not_validated", "settings_history_not_verified",
                            "consumer_transition_not_observed", "no_independent_vehicle_response_validation",
                            "device_resource_budget_not_measured", "no_numeric_recommendation_or_setting_write"],
            "input_sha256": hashes[0], "profile_sha256": hashes[1]}
  if metadata.get("source_kind") == "qlog":
    result["limitations"].append("qlog_not_precision_tuning_evidence")
  if args.command == "analyze-rlog":
    root = package.parents[3]
    schema_paths = ("openpilot/cereal/log.capnp", "openpilot/cereal/custom.capnp", "openpilot/cereal/deprecated.capnp",
                    "opendbc_repo/opendbc/car/car.capnp")
    result["decoder_schema_sha256"] = {path: hash_file(root / path, 4 * 1024 * 1024)[0] for path in schema_paths}
    result["limitations"].extend(["recording_schema_match_unverified", "lateral_response_model_derived",
                                  "sample_hold_is_not_independent_measurement", "recovery_state_only_unvalidated",
                                  "single_segment_epoch_label_is_not_settings_history"])
  manifest = create_bundle(args.output, sources, result, byte_limit=args.byte_limit, expected_sha256=hashes)
  verify_bundle(args.output, byte_limit=args.byte_limit)
  return {"state": manifest["state"], "output": str(args.output), "apply_enabled": False,
          "scope": "offline_diagnostic", "parameter_count": len(result["parameter_readiness"])}


def main(argv=None) -> int:
  parser = argparse.ArgumentParser(description=__doc__)
  commands = parser.add_subparsers(dest="command", required=True)
  commands.add_parser("inventory", help="Display all planned parameters and implementation readiness")
  for name, help_text in (("remote-export", "On comma: freeze inputs for the PC using the existing authenticated file connection"),
                          ("remote-run", "On PC: run the existing analyzer on a frozen remote input directory"),
                          ("remote-publish", "On comma: validate a returned result; the live publisher seals it")):
    command = commands.add_parser(name, help=help_text)
    command.add_argument("directory", type=Path)
    command.add_argument("--output" if name != "remote-publish" else "--result", required=True, type=Path)
  verify = commands.add_parser("verify", help="Verify an existing local evidence bundle")
  verify.add_argument("directory", type=Path)
  verify.add_argument("--byte-limit", type=int, default=DEFAULT_FILE_BUDGET)
  review = commands.add_parser("verify-review", help="Verify a downloaded local review ZIP or directory without its original source paths")
  review.add_argument("path", type=Path)
  review.add_argument("--byte-limit", type=int, default=DEFAULT_FILE_BUDGET)
  template = commands.add_parser("review-template", help="Create an unfinished review JSON from a verified downloaded ZIP or completed directory")
  template.add_argument("path", type=Path)
  template.add_argument("--output", type=Path, help="Create a new draft file exclusively; never overwrite an existing file")
  template.add_argument("--byte-limit", type=int, default=DEFAULT_FILE_BUDGET)
  inspect = commands.add_parser("inspect-session", help="Read-only verification of persisted settings and original log copies")
  inspect.add_argument("directory", type=Path)
  inspect.add_argument("--byte-limit", type=int, default=DEFAULT_FILE_BUDGET)
  candidates = commands.add_parser("evaluate-candidates", help="Calculate unqualified offline candidates; never write settings")
  candidates.add_argument("input", type=Path)
  candidates.add_argument("--profile", required=True, type=Path)
  candidates.add_argument("--output", required=True, type=Path)
  logs = commands.add_parser("evaluate-logs", help="Extract recorded logs and optional saved observations, then evaluate offline candidates")
  logs.add_argument("inputs", nargs="+", type=Path)
  logs.add_argument("--profile", required=True, type=Path)
  logs.add_argument("--objectives", required=True, type=Path)
  logs.add_argument("--session", type=Path)
  logs.add_argument("--output", required=True, type=Path)
  originals = commands.add_parser("verify-log-sources", help="Recheck explicitly selected original files against a generated candidate collection")
  originals.add_argument("directory", type=Path)
  originals.add_argument("inputs", nargs="+", type=Path)
  for name in ("analyze-jsonl", "analyze-rlog"):
    command = commands.add_parser(name, help="Read-only diagnostics; creates a new evidence directory")
    command.add_argument("input", type=Path)
    command.add_argument("--profile", required=True, type=Path)
    command.add_argument("--output", required=True, type=Path)
    command.add_argument("--byte-limit", type=int, default=DEFAULT_FILE_BUDGET)
    if name == "analyze-rlog":
      command.add_argument("--log-kind", required=True, choices=("rlog", "qlog"), help="Declared recording kind; does not prove sampling completeness")
      command.add_argument("--epoch", required=True, help="Analysis partition label, not proof of unchanged settings")
      command.add_argument("--synthetic", action="store_true", help="Label generated test logs explicitly")
  args = parser.parse_args(argv)
  try:
    if args.command.startswith("remote-"):
      from .remote_analysis import export_input, run_input, publish_result
      if args.command == "remote-export":
        value = export_input(args.directory, args.output)
        result = {"state": value.get("state", "exported"), "files": len(value["files"]), "job_id": value["ticket"]["job_id"]}
      elif args.command == "remote-run":
        value = run_input(args.directory, args.output)
        result = {"state": "analyzed_on_pc", "files": len(value["files"]), "job_id": value["ticket"]["job_id"],
                  "result_directory": str(args.output / "result")}
      else:
        result = publish_result(args.directory, args.result)
    elif args.command == "inventory":
      result = {"schema_version": 1, "items": readiness(), "apply_enabled": False}
    elif args.command == "verify":
      result = verify_bundle(args.directory, byte_limit=args.byte_limit)
    elif args.command == "verify-review":
      from .review_export import verify_review_pack
      result = verify_review_pack(args.path, budget={"max_bytes": args.byte_limit, "max_files": 512, "timeout_seconds": 120})
    elif args.command == "review-template":
      from .review_export import _fs_path, _has_link, review_template
      result = review_template(args.path, budget={"max_bytes": args.byte_limit, "max_files": 512, "timeout_seconds": 120})
      if args.output is not None:
        if _has_link(args.output):
          raise ValueError("review_link_not_allowed")
        with _fs_path(args.output).open("x", encoding="utf-8", newline="\n") as stream:
          stream.write(json.dumps(result, ensure_ascii=False, separators=(",", ":"), allow_nan=False) + "\n")
          stream.flush()
          os.fsync(stream.fileno())
        result = {"state": "draft_created", "document_state": "draft", "output": str(args.output)}
    elif args.command == "inspect-session":
      from .inspection import inspect_session
      result = inspect_session(args.directory, byte_limit=args.byte_limit)
    elif args.command == "evaluate-candidates":
      from .candidate_report import calculate_bundle
      result = calculate_bundle(args.input, args.profile, args.output)
    elif args.command == "evaluate-logs":
      from .log_candidates import calculate_log_candidates
      result = calculate_log_candidates(args.inputs, args.profile, args.objectives, args.output, session_directory=args.session)
    elif args.command == "verify-log-sources":
      from .candidate_report import verify_log_sources
      result = verify_log_sources(args.directory, args.inputs)
    else:
      result = run_diagnostic(args)
    print(json.dumps(result, ensure_ascii=args.command == "review-template", allow_nan=False,
                     separators=(",", ":") if args.command == "review-template" else None))
    if args.command == "inspect-session" and result["state"] != "verified":
      return 2
    return 0
  except (ValueError, TypeError, OSError, RuntimeError, ImportError) as exc:
    print(f"control-advisor: {exc}", file=sys.stderr)
    return 2
  except KeyboardInterrupt:
    print("control-advisor: cancelled; incomplete outputs are not valid bundles", file=sys.stderr)
    return 130


if __name__ == "__main__":
  raise SystemExit(main())
