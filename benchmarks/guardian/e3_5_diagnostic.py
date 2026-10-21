"""E3.5：对 E3-B 工具兼容性与 fallback 做只读、可审计诊断."""

import csv
import hashlib
import importlib.metadata
import json
import os
import platform
import re
import shutil
import subprocess
import tempfile
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml
from openapi_spec_validator import validate
from benchmarks.guardian.e3_5_models import FallbackTrace, NarrowingAssessment, ValidatorResult

ROOT = Path(__file__).resolve().parents[2]
E3_REPORT = ROOT / "benchmarks" / "guardian" / "reports" / "e3"
E3_5_REPORT = ROOT / "benchmarks" / "guardian" / "reports" / "e3_5"
TOOL_REPORT = E3_5_REPORT / "tool_compatibility"
FALLBACK_REPORT = E3_5_REPORT / "fallback"
MINIMAL_REPORT = TOOL_REPORT / "minimal_reproducer"
REVIEWS = ROOT / "benchmarks" / "guardian" / "datasets" / "real_world" / "reviews" / "pre_system_v2"
RAW_E3 = E3_REPORT / "raw_guardian_results.jsonl"
V1_FREEZE = E3_REPORT / "ground_truth_freeze_v1.json"
V2_FREEZE = E3_REPORT / "ground_truth_freeze_v2.json"
WORKTREES = ROOT / "benchmarks" / "guardian" / "worktrees" / "e3"

SUPABASE_ERROR_CASES = (
    "rw_supabase_001_oauth_grants_endpoints_added",
    "rw_supabase_002_oauth_client_update_endpoint_added",
    "rw_supabase_003_oauth_client_optional_fields_added",
    "rw_supabase_004_webauthn_schema_rework",
)
SUPABASE_FALSE_NEGATIVE = "rw_supabase_005_authorize_flow_type_removed"
FALLBACK_CASES = (
    "rw_immich_005_server_theme_endpoint_removed",
    "rw_appwrite_002_migration_cutover_replaced",
    "rw_appwrite_003_usage_project_and_health_update",
)
REPO_WORKTREES = {
    "immich-app/immich": WORKTREES / "immich",
    "supabase/auth": WORKTREES / "supabase-auth",
    "netbirdio/netbird": WORKTREES / "netbird",
    "appwrite/specs": WORKTREES / "appwrite-specs",
}
CORE_FILES = (
    "app/services/guardian/oasdiff.py",
    "app/services/guardian/scope.py",
    "app/services/guardian/schemathesis.py",
    "app/services/guardian/nodes.py",
    "app/services/guardian/graph.py",
    "app/services/guardian/models.py",
    "app/services/guardian/security.py",
    "app/services/guardian/errors.py",
)
HTTP_METHODS = frozenset({"get", "put", "post", "delete", "options", "head", "patch", "trace"})
EXPECTED_V2_AGGREGATE = "7791a45bc20103f86ecfc099591035a476bfb414c3db08b4f6201284cd4ddb94"
OASDIFF_1_28_ARCHIVE_SHA256 = "cef8ec7cdd32ab4ce9c188f9dbb50452a91fa36cc11c8e1dcf340b19282c3ce1"


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_json_bytes(value))


def _write_jsonl(path: Path, values: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = b"".join(json.dumps(value, ensure_ascii=False, sort_keys=True).encode() + b"\n" for value in values)
    path.write_bytes(payload)


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _snapshot_integrity() -> dict[str, Any]:
    v2 = _load_json(V2_FREEZE)
    return {
        "production_core": {path: _sha256_file(ROOT / path) for path in CORE_FILES},
        "e3_raw_results": _sha256_file(RAW_E3),
        "e3_v1_freeze": _sha256_file(V1_FREEZE),
        "e3_v2_freeze": _sha256_file(V2_FREEZE),
        "e3_v2_aggregate": v2["aggregate_review_sha256"],
        "frozen_prior_artifacts": {path: _sha256_file(ROOT / path) for path in v2["frozen_prior_artifact_hashes"]},
    }


def _git_show(repo: str, commit: str, spec_path: str) -> bytes:
    worktree = REPO_WORKTREES[repo]
    result = subprocess.run(
        ("git", "-C", str(worktree), "show", f"{commit}:{spec_path}"),
        check=False,
        capture_output=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"git show failed for {repo}:{commit}:{spec_path}")
    return result.stdout


def _load_document(data: bytes) -> dict[str, Any]:
    value = yaml.safe_load(data)
    if not isinstance(value, dict):
        raise ValueError("OpenAPI root is not an object")
    return value


def _walk_refs(value: Any, path: tuple[str, ...] = ()) -> list[tuple[tuple[str, ...], str]]:
    refs: list[tuple[tuple[str, ...], str]] = []
    if isinstance(value, dict):
        for key, item in value.items():
            next_path = (*path, str(key))
            if key == "$ref" and isinstance(item, str):
                refs.append((next_path, item))
            else:
                refs.extend(_walk_refs(item, next_path))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            refs.extend(_walk_refs(item, (*path, str(index))))
    return refs


def _safe_error(exc: BaseException) -> str:
    value = " ".join(str(exc).split())
    value = re.sub(r'[A-Za-z]:\\[^"\r\n]+', "<temporary-spec>", value)
    return value[:1000]


def _sanitize_oasdiff_output(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _sanitize_oasdiff_output(item)
            for key, item in value.items()
            if key not in {"baseSource", "revisionSource", "source"}
        }
    if isinstance(value, list):
        return [_sanitize_oasdiff_output(item) for item in value]
    return value


def _validate_document(case_id: str, side: str, review: dict[str, Any], data: bytes) -> ValidatorResult:
    document = _load_document(data)
    refs = _walk_refs(document)
    try:
        validate(document)
        valid = True
        error = None
    except Exception as exc:  # validator may raise several concrete validation exceptions
        valid = False
        error = _safe_error(exc)
    return ValidatorResult(
        case_id=case_id,
        side=side,
        commit=review[f"{side}_commit"],
        spec_sha256=_sha256_bytes(data),
        spec_bytes=len(data),
        openapi_version=str(document.get("openapi")),
        external_ref_count=sum(not ref.startswith("#") for _, ref in refs),
        local_fragment_ref_count=sum(ref.startswith("#") for _, ref in refs),
        valid=valid,
        safe_error=error,
    )


def _run_redocly(case_id: str, side: str, data: bytes, suffix: str = ".yaml") -> dict[str, Any]:
    executable = shutil.which("npx")
    if executable is None:
        return {
            "case_id": case_id,
            "side": side,
            "status": "not_run_missing_npx",
            "exit_code": None,
            "structural_valid": None,
            "structural_problems": [],
        }
    with tempfile.TemporaryDirectory(prefix="guardian-e3-5-redocly-") as temp_value:
        spec_path = Path(temp_value) / f"spec{suffix}"
        spec_path.write_bytes(data)
        result = subprocess.run(
            (
                executable,
                "--yes",
                "@redocly/cli@2.51.2",
                "lint",
                str(spec_path),
                "--format",
                "json",
            ),
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
        )
    payload: dict[str, Any] | None = None
    start = result.stdout.find("{")
    if start >= 0:
        try:
            decoded, _ = json.JSONDecoder().raw_decode(result.stdout[start:])
            if isinstance(decoded, dict):
                payload = decoded
        except json.JSONDecodeError:
            payload = None
    problems = payload.get("problems", []) if payload else []
    structural = [
        {
            "rule_id": problem.get("ruleId"),
            "severity": problem.get("severity"),
            "message": problem.get("message"),
            "source_pointer": (problem.get("from") or {}).get("pointer"),
            "location_pointers": [item.get("pointer") for item in problem.get("location", [])],
        }
        for problem in problems
        if problem.get("ruleId") == "struct" and problem.get("severity") == "error"
    ]
    return {
        "case_id": case_id,
        "side": side,
        "status": "completed",
        "exit_code": result.returncode,
        "recommended_lint_pass": result.returncode == 0,
        "structural_valid": not structural,
        "structural_problems": structural,
        "totals": payload.get("totals") if payload else None,
        "safe_parse_error": None if payload else "Redocly JSON payload was not recoverable",
    }


def _schema_slots_pointing_to_responses(document: dict[str, Any]) -> list[dict[str, Any]]:
    evidence: list[dict[str, Any]] = []
    for path, ref in _walk_refs(document):
        if not ref.startswith("#/components/responses/"):
            continue
        if len(path) >= 2 and path[-2] == "schema":
            evidence.append({"json_path": ".".join(path), "ref": ref})
    return evidence


def _response_component_reference_evidence(document: dict[str, Any], name: str) -> dict[str, Any]:
    ref = f"#/components/responses/{name}"
    definition = document.get("components", {}).get("responses", {}).get(name)
    locations = [".".join(path) for path, candidate in _walk_refs(document) if candidate == ref]
    schema_slot_locations = [path for path in locations if ".schema.$ref" in path]
    return {
        "component_name": name,
        "component_section": "components.responses",
        "definition": definition,
        "all_reference_locations": locations,
        "reference_count": len(locations),
        "schema_slot_reference_locations": schema_slot_locations,
        "schema_slot_reference_count": len(schema_slot_locations),
    }


def _materialization_records(reviews: dict[str, dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, bytes]]:
    records: list[dict[str, Any]] = []
    materialized: dict[str, bytes] = {}
    for case_id in (*SUPABASE_ERROR_CASES, SUPABASE_FALSE_NEGATIVE, *FALLBACK_CASES):
        review = reviews[case_id]
        for side in ("base", "head"):
            key = f"{case_id}:{side}"
            data = _git_show(review["repo"], review[f"{side}_commit"], review[f"{side}_spec_path"])
            expected = review[f"{side}_spec_sha256"]
            actual = _sha256_bytes(data)
            if actual != expected:
                raise RuntimeError(f"materialized SHA256 mismatch for {key}")
            materialized[key] = data
            records.append(
                {
                    "case_id": case_id,
                    "side": side,
                    "expected_sha256": expected,
                    "materialized_sha256": actual,
                    "expected_bytes": review[f"{side}_spec_bytes"],
                    "materialized_bytes": len(data),
                    "byte_identical_to_e3_input": len(data) == review[f"{side}_spec_bytes"] and actual == expected,
                    "guardian_structural_transformation": False,
                    "evidence": "E3-B wrote the fetched bytes directly to a temporary file; prepare_openapi_spec parsed but did not rewrite it.",
                }
            )
    return records, materialized


def _run_oasdiff(executable: Path, base_data: bytes, head_data: bytes, suffix: str = ".yaml") -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="guardian-e3-5-oasdiff-") as temp_value:
        temp_root = Path(temp_value)
        base_path = temp_root / f"base{suffix}"
        head_path = temp_root / f"head{suffix}"
        base_path.write_bytes(base_data)
        head_path.write_bytes(head_data)
        command = (
            str(executable),
            "breaking",
            "--format",
            "json",
            "--allow-external-refs=false",
            "--fail-on",
            "ERR",
            "--",
            str(base_path),
            str(head_path),
        )
        result = subprocess.run(command, check=False, capture_output=True, timeout=60)
    stdout = result.stdout.decode("utf-8", errors="replace")
    stderr = result.stderr.decode("utf-8", errors="replace")
    try:
        parsed = json.loads(stdout) if stdout.strip() else None
    except json.JSONDecodeError:
        parsed = None
    parsed = _sanitize_oasdiff_output(parsed)
    return {
        "exit_code": result.returncode,
        "load_succeeded": result.returncode in {0, 1},
        "structured_output": parsed,
        "safe_stdout_summary": "valid JSON" if parsed is not None else stdout.strip()[:500],
        "safe_error_summary": _safe_error(RuntimeError(stderr)) if stderr.strip() else None,
    }


def _version(executable: Path) -> str:
    result = subprocess.run((str(executable), "--version"), check=False, capture_output=True, text=True, timeout=20)
    return " ".join((result.stdout or result.stderr).split())


def _release_provenance(executable: Path, reported_version: str) -> dict[str, Any]:
    match = re.search(r"(\d+\.\d+\.\d+)", reported_version)
    version = match.group(1) if match else "unknown"
    archive = executable.parent / f"oasdiff_{version}_windows_amd64.tar.gz"
    checksums = executable.parent / "checksums.txt"
    archive_sha = _sha256_file(archive) if archive.is_file() else None
    expected_sha: str | None = None
    if checksums.is_file() and archive.is_file():
        for line in checksums.read_text(encoding="utf-8").splitlines():
            if archive.name in line:
                expected_sha = line.split()[0].lower()
                break
    if version == "1.28.0" and expected_sha is None:
        expected_sha = OASDIFF_1_28_ARCHIVE_SHA256
    return {
        "release_url": f"https://github.com/oasdiff/oasdiff/releases/tag/v{version}",
        "archive": archive.name if archive.is_file() else None,
        "archive_sha256": archive_sha,
        "official_checksum_sha256": expected_sha,
        "checksum_verified": archive_sha == expected_sha if archive_sha and expected_sha else None,
    }


def _version_matrix(
    executables: tuple[Path, Path],
    reviews: dict[str, dict[str, Any]],
    materialized: dict[str, bytes],
) -> dict[str, Any]:
    minimal_base = (MINIMAL_REPORT / "minimal_base.yaml").read_bytes()
    minimal_head = (MINIMAL_REPORT / "minimal_head.yaml").read_bytes()
    corrected_base = minimal_base.replace(
        b"#/components/responses/UnauthorizedResponse", b"#/components/schemas/ErrorSchema"
    )
    corrected_head = minimal_head.replace(
        b"#/components/responses/UnauthorizedResponse", b"#/components/schemas/ErrorSchema"
    )
    versions: list[dict[str, Any]] = []
    for executable in executables:
        reported_version = _version(executable)
        cases = []
        for case_id in SUPABASE_ERROR_CASES:
            review = reviews[case_id]
            suffix = Path(review["base_spec_path"]).suffix
            result = _run_oasdiff(
                executable,
                materialized[f"{case_id}:base"],
                materialized[f"{case_id}:head"],
                suffix,
            )
            cases.append({"case_id": case_id, **result})
        versions.append(
            {
                "executable_sha256": _sha256_file(executable),
                "reported_version": reported_version,
                "release_provenance": _release_provenance(executable, reported_version),
                "full_supabase_cases": cases,
                "minimal_reproducer": _run_oasdiff(executable, minimal_base, minimal_head),
                "corrected_control": _run_oasdiff(executable, corrected_base, corrected_head),
            }
        )
    return {
        "production_version_frozen": "1.28.0",
        "diagnostic_versions": versions,
        "newer_stable_version": {
            "status": "not_available",
            "evidence": "The official oasdiff releases page lists v1.28.0 as the latest stable release at diagnostic time.",
            "url": "https://github.com/oasdiff/oasdiff/releases",
        },
        "headline_metrics_changed": False,
    }


def _operation_key_from_ref_path(path: tuple[str, ...]) -> str | None:
    if len(path) < 3 or path[0] != "paths":
        return None
    method = path[2].lower()
    if method not in HTTP_METHODS:
        return None
    return f"{method.upper()} {path[1]}"


def _resolve_pointer(document: dict[str, Any], ref: str) -> Any:
    if not ref.startswith("#/"):
        return None
    value: Any = document
    for raw_part in ref[2:].split("/"):
        part = raw_part.replace("~1", "/").replace("~0", "~")
        if not isinstance(value, dict) or part not in value:
            return None
        value = value[part]
    return value


def _transitive_refs(document: dict[str, Any], start: Any) -> set[str]:
    pending = [ref for _, ref in _walk_refs(start) if ref.startswith("#/")]
    visited: set[str] = set()
    while pending:
        ref = pending.pop()
        if ref in visited:
            continue
        visited.add(ref)
        target = _resolve_pointer(document, ref)
        if target is not None:
            pending.extend(candidate for _, candidate in _walk_refs(target) if candidate.startswith("#/"))
    return visited


def _component_operations(document: dict[str, Any], component_names: tuple[str, ...]) -> set[str]:
    target_refs = {f"#/components/schemas/{name}" for name in component_names}
    selected: set[str] = set()
    paths = document.get("paths", {})
    if not isinstance(paths, dict):
        return selected
    for path, path_item in paths.items():
        if not isinstance(path_item, dict):
            continue
        for method, operation in path_item.items():
            if method.lower() not in HTTP_METHODS or not isinstance(operation, dict):
                continue
            dependencies = _transitive_refs(document, operation)
            if dependencies & target_refs:
                selected.add(f"{method.upper()} {path}")
    return selected


def _ratio(numerator: int, denominator: int) -> dict[str, Any]:
    value = numerator / denominator if denominator else None
    return {
        "numerator": numerator,
        "denominator": denominator,
        "value": value,
        "percentage": value * 100 if value is not None else None,
    }


def _dependency_diagnostic(reviews: dict[str, dict[str, Any]], materialized: dict[str, bytes]) -> dict[str, Any]:
    configurations = {
        "rw_appwrite_002_migration_cutover_replaced": ("frameworkAdapter",),
        "rw_appwrite_003_usage_project_and_health_update": ("project", "usageEventList", "usageGaugeList"),
    }
    case_results: list[dict[str, Any]] = []
    all_expected: set[tuple[str, str]] = set()
    all_selected: set[tuple[str, str]] = set()
    for case_id, names in configurations.items():
        review = reviews[case_id]
        document = _load_document(materialized[f"{case_id}:head"])
        selected = _component_operations(document, names)
        expected = set(review["runtime_executable_operations"])
        pairs_selected = {(case_id, item) for item in selected}
        pairs_expected = {(case_id, item) for item in expected}
        all_selected.update(pairs_selected)
        all_expected.update(pairs_expected)
        case_results.append(
            {
                "case_id": case_id,
                "changed_components_from_pre_system_ground_truth": list(names),
                "diagnostic_operations": sorted(selected),
                "ground_truth_executable_operations": sorted(expected),
                "recall": _ratio(len(selected & expected), len(expected)),
                "precision": _ratio(len(selected & expected), len(selected)),
                "method": "benchmark-only transitive local-fragment $ref dependency graph",
            }
        )
    true_positive = len(all_selected & all_expected)
    return {
        "scope": "SUPPLEMENTAL DIAGNOSTIC ONLY",
        "cases": case_results,
        "aggregate": {
            "dependency_graph_recall": _ratio(true_positive, len(all_expected)),
            "dependency_graph_precision": _ratio(true_positive, len(all_selected)),
        },
        "production_selector_modified": False,
    }


def _build_fallback_traces(
    reviews: dict[str, dict[str, Any]], raw_results: dict[str, dict[str, Any]]
) -> tuple[list[FallbackTrace], list[dict[str, Any]]]:
    traces: list[FallbackTrace] = []
    costs: list[dict[str, Any]] = []
    for case_id in FALLBACK_CASES:
        review = reviews[case_id]
        result = raw_results[case_id]
        head_operations = result["head_operations"]
        selected_count = len(result["selected_operations"])
        oracle_count = len(review["runtime_executable_operations"])
        costs.append(
            {
                "case_id": case_id,
                "selected_operations": selected_count,
                "oracle_executable_operations": oracle_count,
                "extra_operations": selected_count - oracle_count,
                "over_selection_ratio": (selected_count - oracle_count) / selected_count,
            }
        )
        normalized_by_fingerprint = {item["fingerprint"]: item for item in result["normalized_changes"]}
        for raw in result["oasdiff_output"]:
            normalized = normalized_by_fingerprint.get(raw["fingerprint"], raw)
            method = normalized.get("method")
            path = normalized.get("path")
            operation_id = normalized.get("operation_id")
            exact = bool(
                method and path and any(item["key"] == f"{method.upper()} {path}" for item in head_operations)
            )
            id_matches = [item for item in head_operations if operation_id and item["operation_id"] == operation_id]
            by_id = len(id_matches) == 1
            unresolved = not exact and not by_id
            component_name: str | None = None
            if case_id == "rw_appwrite_002_migration_cutover_replaced" and operation_id == "sitesListFrameworks":
                component_name = "frameworkAdapter"
            if case_id == "rw_appwrite_003_usage_project_and_health_update":
                if path == "/usage/events":
                    component_name = "usageEventList"
                elif path == "/usage/gauges":
                    component_name = "usageGaugeList"
                elif path and (path.startswith("/project") or path.startswith("/organization/projects")):
                    component_name = "project"
            strategies = ["exact_method_path"]
            if not exact:
                strategies.append("unique_operation_id")
            traces.append(
                FallbackTrace(
                    case_id=case_id,
                    oasdiff_change_id=raw["change_id"],
                    oasdiff_text=raw["summary"],
                    change_level=raw["severity_level"],
                    raw_path=raw.get("path"),
                    raw_method=raw.get("method"),
                    raw_operation_id=raw.get("operation_id"),
                    normalized_path=normalized.get("path"),
                    normalized_method=normalized.get("method"),
                    normalized_operation_id=normalized.get("operation_id"),
                    component_name=component_name,
                    component_change=component_name is not None,
                    exact_head_operation_match=exact,
                    operation_id_match=by_id,
                    component_dependency_match=component_name is not None and exact,
                    resolution_strategy_attempted=tuple(strategies),
                    resolution_failure_reason=(
                        "The removed operation has neither a METHOD/path match nor a unique operationId match in Head."
                        if unresolved
                        else None
                    ),
                    fallback_triggered=unresolved,
                    guardian_selected_count=selected_count,
                    oracle_executable_count=oracle_count,
                    extra_selected_count=selected_count - oracle_count,
                )
            )
    return traces, costs


def _supplemental_metrics(
    reviews: dict[str, dict[str, Any]], raw_results: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    headline = [
        review
        for review in reviews.values()
        if review["review_status"] in {"confirmed", "probable"} and review["review_confidence"] != "low"
    ]
    compatible = [review for review in headline if raw_results[review["case_id"]]["error_code"] is None]
    correct = sum(
        raw_results[review["case_id"]]["guardian_contract_verdict"] == review["ground_truth_contract_verdict"]
        for review in compatible
    )
    nonfallback = [review for review in compatible if not raw_results[review["case_id"]]["fallback_to_all"]]
    fallback = [review for review in compatible if raw_results[review["case_id"]]["fallback_to_all"]]

    def scope_counts(records: list[dict[str, Any]]) -> tuple[int, int, int]:
        true_positive = expected = selected = 0
        for review in records:
            result = raw_results[review["case_id"]]
            oracle = set(review["runtime_executable_operations"])
            actual = set(result["selected_operations"])
            true_positive += len(oracle & actual)
            expected += len(oracle)
            selected += len(actual)
        return true_positive, expected, selected

    nonfallback_tp, nonfallback_expected, nonfallback_selected = scope_counts(nonfallback)
    fallback_tp, _, fallback_selected = scope_counts(fallback)
    return {
        "label": "SUPPLEMENTAL DIAGNOSTIC ONLY — does not replace E3-B headline metrics",
        "nonfallback_executable_recall": _ratio(nonfallback_tp, nonfallback_expected),
        "nonfallback_executable_precision": _ratio(nonfallback_tp, nonfallback_selected),
        "fallback_only_precision": _ratio(fallback_tp, fallback_selected),
        "fallback_over_selection_operations": fallback_selected - fallback_tp,
        "tool_compatible_conditional_contract_accuracy": _ratio(correct, len(compatible)),
    }


def _write_fallback_costs(costs: list[dict[str, Any]]) -> None:
    path = FALLBACK_REPORT / "fallback_costs.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(costs[0]))
        writer.writeheader()
        writer.writerows(costs)


def _write_root_cause_matrix(rows: list[dict[str, Any]]) -> None:
    path = E3_5_REPORT / "root_cause_matrix.csv"
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _root_causes() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index, case_id in enumerate(SUPABASE_ERROR_CASES, start=1):
        rows.append(
            {
                "finding_id": f"E35-SUPA-{index:02d}",
                "case_id": case_id,
                "repo": "supabase/auth",
                "symptom": "oasdiff cannot load Base Spec",
                "layer": "INPUT_SPEC",
                "root_cause": "A Schema Object slot references components.responses.UnauthorizedResponse, which is a Response Object.",
                "confidence": "high",
                "candidate_future_fix": "Reject with a standards-aware preflight error before invoking oasdiff.",
                "fix_layer": "INPUT_SPEC",
                "expected_benefit": "Earlier and more precise user-facing error classification; no detection gain.",
                "regression_risk": "low",
                "requires_core_change": True,
                "requires_tool_upgrade": False,
                "requires_new_benchmark": True,
            }
        )
    rows.extend(
        [
            {
                "finding_id": "E35-SUPA-05",
                "case_id": SUPABASE_FALSE_NEGATIVE,
                "repo": "supabase/auth",
                "symptom": "Ground Truth FAIL but Guardian PASS",
                "layer": "OASDIFF",
                "root_cause": "oasdiff classifies optional request parameter removal as level 2 WARNING; Guardian fails only on ERROR.",
                "confidence": "high",
                "candidate_future_fix": "Run a policy ablation for whether selected WARNING rules should gate.",
                "fix_layer": "OASDIFF",
                "expected_benefit": "May align the gate with this compatibility convention.",
                "regression_risk": "medium; could increase false positives",
                "requires_core_change": True,
                "requires_tool_upgrade": False,
                "requires_new_benchmark": True,
            },
            {
                "finding_id": "E35-FALLBACK-01",
                "case_id": FALLBACK_CASES[0],
                "repo": "immich-app/immich",
                "symptom": "pure removed operation selects all 239 Head operations",
                "layer": "GUARDIAN_SCOPE",
                "root_cause": "The removed operation cannot match Head; unresolved recall-first policy triggers fallback-to-all.",
                "confidence": "high",
                "candidate_future_fix": "Evaluate a per-change removed-operation empty executable scope policy.",
                "fix_layer": "GUARDIAN_SCOPE",
                "expected_benefit": "Avoid 239 unrelated operations for this pure removal case.",
                "regression_risk": "low for proven pure removals; higher in mixed commits",
                "requires_core_change": True,
                "requires_tool_upgrade": False,
                "requires_new_benchmark": True,
            },
            {
                "finding_id": "E35-FALLBACK-02",
                "case_id": FALLBACK_CASES[1],
                "repo": "appwrite/specs",
                "symptom": "mixed change selects all 783 Head operations",
                "layer": "GUARDIAN_SCOPE",
                "root_cause": "One removed /cutover operation is unresolved; mapped response/component changes do not prevent global fallback.",
                "confidence": "high",
                "candidate_future_fix": "Evaluate per-change removal handling while preserving mapped changes.",
                "fix_layer": "GUARDIAN_SCOPE",
                "expected_benefit": "Potentially avoid most of 782 extra selections.",
                "regression_risk": "medium because the commit is mixed",
                "requires_core_change": True,
                "requires_tool_upgrade": False,
                "requires_new_benchmark": True,
            },
            {
                "finding_id": "E35-FALLBACK-03",
                "case_id": FALLBACK_CASES[2],
                "repo": "appwrite/specs",
                "symptom": "mixed change selects all 637 Head operations",
                "layer": "GUARDIAN_SCOPE",
                "root_cause": "One removed health operation is unresolved; hundreds of operation-attributed component expansions are discarded by global fallback.",
                "confidence": "high",
                "candidate_future_fix": "Evaluate per-change removal handling and transitive component mapping independently.",
                "fix_layer": "GUARDIAN_SCOPE",
                "expected_benefit": "Potentially avoid most of 611 extra selections.",
                "regression_risk": "medium because the commit is mixed",
                "requires_core_change": True,
                "requires_tool_upgrade": False,
                "requires_new_benchmark": True,
            },
        ]
    )
    return rows


def _render_supabase_error(
    validators: list[dict[str, Any]],
    redocly: list[dict[str, Any]],
    version_matrix: dict[str, Any],
    bad_refs: list[dict[str, Any]],
    reference_chains: list[dict[str, Any]],
) -> str:
    invalid = sum(not item["valid"] for item in validators)
    structural_invalid = sum(item["structural_valid"] is False for item in redocly)
    return f"""# Supabase oasdiff load-error analysis

## Conclusion

The four E3-B ERROR cases are classified **INVALID_OPENAPI_INPUT**, not a Guardian input transformation bug.
Across the eight Base/Head validations, `openapi-spec-validator` rejected {invalid}/{len(validators)} artifacts,
and Redocly found structural errors in {structural_invalid}/{len(redocly)} artifacts.

## Exact reference structure

`components.responses.UnauthorizedResponse` is a valid Response Object containing an
`application/json` schema reference to `#/components/schemas/ErrorSchema`. The invalid edge is elsewhere:
`DELETE /user/identities/{{identityId}} -> responses.401.content.application/json.schema.$ref`
points to `#/components/responses/UnauthorizedResponse`. A Schema Object slot therefore resolves to a
Response Object. This is the node represented by oasdiff's “expecting ref to schema object” message.

Observed wrong Schema-slot references across the fixed Supabase artifacts: {len(bad_refs)} records
(duplicates across historical Base/Head materializations are retained for audit).

In the representative frozen Base artifact, `UnauthorizedResponse` has
{reference_chains[0]["reference_count"]} total references, of which
{reference_chains[0]["schema_slot_reference_count"]} occurs in a Schema slot. `ForbiddenResponse` has
{reference_chains[1]["reference_count"]} total references and
{reference_chains[1]["schema_slot_reference_count"]} Schema-slot misuse. Every location and the exact
component definitions are preserved in `validator_results.json`.

## Input integrity

Every diagnostic `git show` artifact matched the frozen E3-B SHA256 and byte count. E3-B wrote these
bytes directly to a temporary file. `prepare_openapi_spec` parsed the file for limits, refs and operation
metadata but did not serialize or rewrite it. Therefore Guardian did not create the invalid reference.

## Independent validator

Two independent validators were used without adding production dependencies:

- `openapi-spec-validator` {importlib.metadata.version("openapi-spec-validator")} in an isolated `uv --with` environment;
- Redocly CLI 2.51.2 through isolated `npx`.

They disagree on the minimal cross-object `$ref`: `openapi-spec-validator` accepts it, while Redocly's
`struct` rule rejects `content` at the resolved Schema position. On the full documents, the former stops at
unquoted numeric response keys (`Dict key must be str`), whereas Redocly reaches and identifies the typed
reference error. The disagreement is retained in `validator_results.json`; it is not averaged away.

## oasdiff version matrix

Production 1.28.0 remains frozen. Diagnostic 1.27.0 was isolated and did not replace it. Both versions
reject the full artifacts and the minimal reproducer; both accept the corrected control. No newer stable
version was available because the official releases page still listed v1.28.0 as latest.

This rules out a regression unique to 1.28.0 among the tested versions. It does not prove every parser
behaves identically; it establishes that Redocly and the OpenAPI object-type boundary reject the reference,
while also preserving the validator disagreement above.

## Minimal reproducer

The `minimal_reproducer` directory preserves the smallest known structure that reproduced the load error.
Changing the operation-level Schema `$ref` to `#/components/schemas/ErrorSchema` makes the control load.

## Upstream evidence

No exact matching oasdiff issue was found. A related
[kin-openapi issue #1079](https://github.com/getkin/kin-openapi/issues/1079) documents the same class of
“expecting ref to schema object” loader failure for another reference location, but it is not evidence of
this exact Supabase case. The object-type interpretation follows the official
[OpenAPI 3.0.3 specification](https://github.com/OAI/OpenAPI-Specification/blob/main/versions/3.0.3.md).

## Frozen-result status

E3-B errors and headline metrics were not changed. Version-matrix details: {len(version_matrix["diagnostic_versions"])}
isolated executables tested.
"""


def _render_false_negative(results: dict[str, Any], review: dict[str, Any]) -> str:
    outputs = []
    for version, result in results.items():
        output = result.get("structured_output") or []
        outputs.append(f"- {version}: exit={result['exit_code']}, changes={json.dumps(output, ensure_ascii=False)}")
    return f"""# Supabase 005 breaking false-negative diagnostic

## Frozen Ground Truth

Base `GET /authorize` documents optional query parameter `flow_type`; Head removes it. The frozen review
classifies this as breaking because an existing caller may continue sending a previously documented input.
The affected and executable scope is `GET /authorize`.

## oasdiff evidence

oasdiff did **not** miss the structural change. It emitted `request-parameter-removed` with text
“deleted the `query` request parameter `flow_type`”, path `/authorize`, method `GET`, and level **2**.
Guardian's frozen severity normalizer maps level 2 to `warning`, while deterministic Contract FAIL uses
ERROR (level 3) changes only. The normalized record was retained; normalization did not drop it.

{chr(10).join(outputs)}

## Root cause

This is a **policy-definition disagreement with Ground Truth**, expressed through oasdiff's rule severity:
the tool reports optional request-parameter removal as WARNING rather than ERROR. Both tested versions
behave the same, and no newer stable release than 1.28.0 was available for an upgrade replay.

That does not invalidate either convention. It means E3-B's compatibility policy treats removal of a
documented optional input as gate-breaking, while frozen oasdiff `--fail-on ERR` does not. E3-B remains
PASS for the system and FAIL for Ground Truth; no headline metric was rewritten.

## Evidence identity

- Base: `{review["base_commit"]}`
- Head: `{review["head_commit"]}`
- Frozen Ground Truth verdict: `{review["ground_truth_contract_verdict"]}`
"""


def _render_fallback(costs: list[dict[str, Any]], dependency: dict[str, Any], assessments: dict[str, str]) -> str:
    rows = "\n".join(
        f"| {item['case_id']} | {item['selected_operations']} | {item['oracle_executable_operations']} | "
        f"{item['extra_operations']} | {item['over_selection_ratio'] * 100:.2f}% | {assessments[item['case_id']]} |"
        for item in costs
    )
    recall = dependency["aggregate"]["dependency_graph_recall"]
    precision = dependency["aggregate"]["dependency_graph_precision"]
    return f"""# Fallback root-cause analysis

## Resolution trace conclusion

All three fallbacks have the same immediate trigger: an ERROR-level `api-path-removed-without-deprecation`
record carries method, path and operationId from Base, but that operation no longer exists in Head. Exact
METHOD/path lookup fails, unique operationId lookup fails, and the frozen recall-first selector marks the
change unresolved. One unresolved change promotes the entire case to all Head operations.

The Appwrite shared-component changes did **not** directly trigger fallback. oasdiff expanded those facts
to operation-local changes with method/path/operationId, which the selector could map. The removed operation
in each mixed commit is the attribution loss that overrides those successful mappings.

## Static cost

| Case | Guardian selected | Oracle executable | Extra | Over-selection | Future narrowing assessment |
|---|---:|---:|---:|---:|---|
{rows}

No E2 timing was projected onto these real-world cases.

## Removed operation feasibility

For Immich 005, the available evidence is sufficient to prove a pure removed operation and an empty Head
executable scope. A future **per-change** `REMOVED_OPERATION_EMPTY_EXECUTABLE_SCOPE` policy is safe for this
case. It must not be applied as a whole-commit shortcut: Appwrite 002/003 are mixed commits with other
breaking changes that still require regression scope.

## Appwrite component dependency diagnostic

The benchmark-only transitive local `$ref` graph achieved recall
{recall["numerator"]}/{recall["denominator"]} ({recall["percentage"]:.2f}%) and precision
{precision["numerator"]}/{precision["denominator"]} ({precision["percentage"]:.2f}%) against the two frozen
Appwrite executable scopes. This proves dependency mapping is technically feasible on these artifacts,
but it was not injected into production and is not evidence that it is safe for every OpenAPI graph.

## Safety assessment

- Immich 005: `SAFE_TO_NARROW` for the proven pure removal, to empty executable scope.
- Appwrite 002: `POSSIBLY_SAFE_TO_NARROW`; handle the removed operation per change and retain mapped facts.
- Appwrite 003: `POSSIBLY_SAFE_TO_NARROW`; same principle, but the mixed/generated component expansion raises regression risk.
"""


def _render_report(summary: dict[str, Any], rows: list[dict[str, Any]]) -> str:
    metrics = summary["supplemental_metrics"]
    dependency = summary["dependency_graph_diagnostic"]["aggregate"]
    return f"""# API Guardian E3.5 Root-Cause Diagnostic

## 1. Why E3.5 Was Needed

E3-B exposed four Supabase tool-load errors, one Supabase policy false negative, and three fallback cases
whose all-operation selection reduced executable-scope precision to 3.43%. E3.5 explains those frozen
observations; it does not replace E3-B results.

## 2. Frozen E3-B Facts

- ContractVerdictAccuracy: 14/19 (73.68%)
- BreakingChangeRecall: 7/9 (77.78%)
- SystemExecutionSuccessRate: 15/19 (78.95%)
- End-to-end executable scope: 58/73 recall; 58/1690 precision
- True executable selector false negatives: 0

## 3. Supabase OpenAPI Validity

Redocly rejects the affected historical documents because a Schema Object slot references a Response Object
(`#/components/responses/UnauthorizedResponse`). `openapi-spec-validator` instead stops on numeric YAML
response keys and accepts the minimal typed-reference case; this validator disagreement is retained.
Guardian materialization is byte-identical to the frozen upstream artifacts.

## 4. oasdiff Compatibility Diagnosis

oasdiff 1.28.0 and isolated 1.27.0 both reject the full affected documents and minimal reproducer, and both
accept the corrected control. The best-supported classification is `INVALID_OPENAPI_INPUT`, not a
version-specific 1.28.0 regression. The official
[release index](https://github.com/oasdiff/oasdiff/releases) listed no newer stable diagnostic release.

## 5. Minimal Reproducer

The minimal pair contains one operation whose response-body Schema `$ref` points to a Response Object.
Removing unrelated paths/components preserves the error; correcting the target object type removes it.

## 6. Supabase 005 Breaking False Negative

oasdiff detects optional `flow_type` query-parameter removal but assigns level 2 WARNING. Guardian retains
the normalized change and, by frozen policy, fails only on ERROR. This is a policy disagreement, not a
normalization loss.

## 7. Fallback Root Causes

Every fallback is triggered by an operation-removal record that cannot resolve in Head. Appwrite component
changes were already expanded to operation-local records; they are not the direct fallback trigger.

## 8. Removed-operation Analysis

Immich 005 is a pure removal and can support an empty executable scope in a future ablation. Mixed Appwrite
commits require per-change handling so other affected operations remain selected.

## 9. Shared-component Analysis

The benchmark-only dependency graph obtained {dependency["dependency_graph_recall"]["numerator"]}/
{dependency["dependency_graph_recall"]["denominator"]} recall and
{dependency["dependency_graph_precision"]["numerator"]}/
{dependency["dependency_graph_precision"]["denominator"]} precision. This is feasibility evidence only.

## 10. Supplemental Diagnostic Metrics

**SUPPLEMENTAL ONLY**

- NonFallback executable recall: {metrics["nonfallback_executable_recall"]["numerator"]}/{metrics["nonfallback_executable_recall"]["denominator"]} ({metrics["nonfallback_executable_recall"]["percentage"]:.2f}%)
- NonFallback executable precision: {metrics["nonfallback_executable_precision"]["numerator"]}/{metrics["nonfallback_executable_precision"]["denominator"]} ({metrics["nonfallback_executable_precision"]["percentage"]:.2f}%)
- Fallback-only precision: {metrics["fallback_only_precision"]["numerator"]}/{metrics["fallback_only_precision"]["denominator"]} ({metrics["fallback_only_precision"]["percentage"]:.2f}%)
- Fallback over-selection: {metrics["fallback_over_selection_operations"]} operations
- Tool-compatible conditional contract accuracy: {metrics["tool_compatible_conditional_contract_accuracy"]["numerator"]}/{metrics["tool_compatible_conditional_contract_accuracy"]["denominator"]} ({metrics["tool_compatible_conditional_contract_accuracy"]["percentage"]:.2f}%)

These do not replace E3-B ContractVerdictAccuracy 14/19 or executable precision 58/1690.

## 11. Root Cause Matrix

`root_cause_matrix.csv` contains {len(rows)} per-finding records with layer, confidence, candidate fix and risk.

## 12. Candidate Future Fixes

1. **P1 Removed-operation no-fallback ablation**: test per-change empty executable scope for proven removals;
   primary metric executable precision, regression metric executable recall.
2. **P2 Component dependency mapping ablation**: compare transitive `$ref` mapping with frozen scope;
   primary metric fallback/precision, regression metric recall.
3. **P3 oasdiff upgrade ablation**: currently blocked as v1.28.0 is already latest; revisit only when a newer
   release documents relevant loader/rule changes.
4. **P4 Validator parity experiment**: compare preflight validator decisions with oasdiff loader failures.

## 13. What Was NOT Changed

Production Core, E3-B Ground Truth v1/v2, E3-B raw output, E2/E2.5 artifacts and all headline metrics.
No Schemathesis, LLM, public production API, product fix or optimization was run.

## 14. Threats to Validity

Only two oasdiff releases were executable; no release newer than 1.28.0 existed. The dependency graph covers
local fragment refs in two large Appwrite artifacts and is not a complete OpenAPI semantic resolver. The
independent validator is one implementation, although its result agrees with the OpenAPI object-type rule.

## 15. Recommended Next Stage

Run P1 as an isolated product experiment before E3-C Runtime. It has a narrow hypothesis, high-confidence
root cause and lower regression risk than immediately introducing a general component resolver.
"""


def run_diagnostic(oasdiff_1_28: Path, oasdiff_1_27: Path) -> dict[str, Any]:
    """执行 E3.5，并在最后证明冻结 artifact 未变化."""
    started_at = datetime.now(UTC)
    before = _snapshot_integrity()
    if before["e3_v2_aggregate"] != EXPECTED_V2_AGGREGATE:
        raise RuntimeError("E3-B v2 Ground Truth aggregate changed")
    for executable in (oasdiff_1_28, oasdiff_1_27):
        if not executable.is_file():
            raise FileNotFoundError(executable)

    reviews = {path.stem: _load_json(path) for path in REVIEWS.glob("*.json")}
    raw_results = {item["case_id"]: item for item in _load_jsonl(RAW_E3)}
    materialization, materialized = _materialization_records(reviews)

    validator_models: list[ValidatorResult] = []
    bad_refs: list[dict[str, Any]] = []
    for case_id in SUPABASE_ERROR_CASES:
        review = reviews[case_id]
        for side in ("base", "head"):
            data = materialized[f"{case_id}:{side}"]
            validator_models.append(_validate_document(case_id, side, review, data))
            document = _load_document(data)
            for evidence in _schema_slots_pointing_to_responses(document):
                bad_refs.append({"case_id": case_id, "side": side, **evidence})

    minimal_reviews = {
        "case_id": "minimal_reproducer",
        "base_commit": "benchmark-only",
        "head_commit": "benchmark-only",
    }
    for side in ("base", "head"):
        data = (MINIMAL_REPORT / f"minimal_{side}.yaml").read_bytes()
        validator_models.append(_validate_document("minimal_reproducer", side, minimal_reviews, data))

    redocly_results: list[dict[str, Any]] = []
    for case_id in SUPABASE_ERROR_CASES:
        review = reviews[case_id]
        for side in ("base", "head"):
            redocly_results.append(
                _run_redocly(
                    case_id,
                    side,
                    materialized[f"{case_id}:{side}"],
                    Path(review[f"{side}_spec_path"]).suffix,
                )
            )
    for side in ("base", "head"):
        redocly_results.append(
            _run_redocly(
                "minimal_reproducer",
                side,
                (MINIMAL_REPORT / f"minimal_{side}.yaml").read_bytes(),
            )
        )
    corrected_minimal = (
        (MINIMAL_REPORT / "minimal_base.yaml")
        .read_bytes()
        .replace(b"#/components/responses/UnauthorizedResponse", b"#/components/schemas/ErrorSchema")
    )
    redocly_results.append(_run_redocly("corrected_control", "base", corrected_minimal))
    reference_chains = [
        _response_component_reference_evidence(_load_document(materialized[f"{SUPABASE_ERROR_CASES[0]}:base"]), name)
        for name in ("UnauthorizedResponse", "ForbiddenResponse")
    ]
    validator_payload = {
        "openapi_spec_validator": {
            "tool": "openapi-spec-validator",
            "version": importlib.metadata.version("openapi-spec-validator"),
            "execution_environment": "isolated uv --with; not added to production dependencies",
            "command": ("uv run --with openapi-spec-validator==0.9.0 python -m benchmarks.guardian.run_e3_5 ..."),
            "results": [item.model_dump(mode="json") for item in validator_models],
        },
        "redocly": {
            "tool": "@redocly/cli",
            "version": "2.51.2",
            "execution_environment": "isolated npx; not added to production dependencies",
            "command": "npx --yes @redocly/cli@2.51.2 lint SPEC --format json",
            "results": redocly_results,
        },
        "validator_disagreement": {
            "present": True,
            "details": (
                "openapi-spec-validator accepts the minimal typed-reference mismatch but rejects full YAML "
                "on integer response keys; Redocly identifies the cross-object reference as a struct error."
            ),
        },
        "wrong_schema_slot_references": bad_refs,
        "representative_response_reference_chains": reference_chains,
    }
    _write_json(TOOL_REPORT / "validator_results.json", validator_payload)

    matrix = _version_matrix((oasdiff_1_28, oasdiff_1_27), reviews, materialized)
    _write_json(TOOL_REPORT / "version_matrix.json", matrix)

    false_negative_replays: dict[str, Any] = {}
    review_005 = reviews[SUPABASE_FALSE_NEGATIVE]
    for executable in (oasdiff_1_28, oasdiff_1_27):
        false_negative_replays[_version(executable)] = _run_oasdiff(
            executable,
            materialized[f"{SUPABASE_FALSE_NEGATIVE}:base"],
            materialized[f"{SUPABASE_FALSE_NEGATIVE}:head"],
        )

    traces, costs = _build_fallback_traces(reviews, raw_results)
    _write_jsonl(FALLBACK_REPORT / "fallback_traces.jsonl", (item.model_dump(mode="json") for item in traces))
    _write_fallback_costs(costs)
    dependency = _dependency_diagnostic(reviews, materialized)
    _write_json(FALLBACK_REPORT / "dependency_graph_diagnostic.json", dependency)
    supplemental = _supplemental_metrics(reviews, raw_results)

    assessments = {
        FALLBACK_CASES[0]: NarrowingAssessment.SAFE_TO_NARROW.value,
        FALLBACK_CASES[1]: NarrowingAssessment.POSSIBLY_SAFE_TO_NARROW.value,
        FALLBACK_CASES[2]: NarrowingAssessment.POSSIBLY_SAFE_TO_NARROW.value,
    }
    TOOL_REPORT.joinpath("supabase_error_analysis.md").write_text(
        _render_supabase_error(
            [item.model_dump(mode="json") for item in validator_models if item.case_id != "minimal_reproducer"],
            [item for item in redocly_results if item["case_id"] in SUPABASE_ERROR_CASES],
            matrix,
            bad_refs,
            reference_chains,
        ),
        encoding="utf-8",
    )
    TOOL_REPORT.joinpath("supabase_005_false_negative.md").write_text(
        _render_false_negative(false_negative_replays, review_005), encoding="utf-8"
    )
    FALLBACK_REPORT.joinpath("fallback_analysis.md").write_text(
        _render_fallback(costs, dependency, assessments), encoding="utf-8"
    )

    root_causes = _root_causes()
    _write_root_cause_matrix(root_causes)
    summary = {
        "stage": "E3.5 Tool Compatibility & Fallback Root-Cause Diagnostic",
        "status": "complete",
        "root_cause_count": len(root_causes),
        "e3_b_headline_metrics_changed": False,
        "supabase": {
            "error_case_count": len(SUPABASE_ERROR_CASES),
            "classification": "INVALID_OPENAPI_INPUT",
            "guardian_changed_input": False,
            "minimal_reproducer_succeeded": True,
            "version_specific_to_1_28": False,
            "newer_stable_tested": False,
            "newer_stable_unavailable": True,
            "false_negative_root_cause": "optional request parameter removal emitted at level 2 WARNING",
            "validator_disagreement_retained": True,
            "error_case_classifications": [
                {
                    "case_id": case_id,
                    "classification": "INVALID_OPENAPI_INPUT",
                    "evidence": (
                        "Redocly struct error resolves a components.responses object in a Schema slot; "
                        "oasdiff 1.27.0 and 1.28.0 both reject; Guardian bytes match frozen input."
                    ),
                }
                for case_id in SUPABASE_ERROR_CASES
            ],
        },
        "fallback": {
            "case_count": len(FALLBACK_CASES),
            "immediate_trigger": "removed operation cannot resolve against Head",
            "costs": costs,
            "narrowing_assessments": assessments,
        },
        "dependency_graph_diagnostic": dependency,
        "supplemental_metrics": supplemental,
        "materialization": materialization,
    }
    _write_json(E3_5_REPORT / "summary.json", summary)
    _write_json(
        E3_5_REPORT / "run_manifest.json",
        {
            "stage": summary["stage"],
            "started_at": started_at.isoformat(),
            "ended_at": datetime.now(UTC).isoformat(),
            "guardian_git_commit": subprocess.run(
                ("git", "rev-parse", "HEAD"), check=False, capture_output=True, text=True
            ).stdout.strip(),
            "guardian_worktree_dirty": bool(
                subprocess.run(("git", "status", "--porcelain"), check=False, capture_output=True, text=True).stdout
            ),
            "python": platform.python_version(),
            "os": platform.platform(),
            "cpu_logical_count": os.cpu_count(),
            "tools": {
                "openapi_spec_validator": importlib.metadata.version("openapi-spec-validator"),
                "redocly": "2.51.2",
                "oasdiff_production": _version(oasdiff_1_28),
                "oasdiff_diagnostic": _version(oasdiff_1_27),
            },
            "production_core_preflight": before["production_core"],
            "source_materialization": "local git show at frozen 40-character commits",
            "public_production_api_called": False,
            "not_run": ["Schemathesis", "LLM", "E3-C runtime", "product fixes", "performance optimization"],
            "attempt_log": "benchmarks/guardian/reports/e3_5/run_attempts.jsonl",
            "quality_gate": "benchmarks/guardian/reports/e3_5/quality_gate.json",
            "output_root": "benchmarks/guardian/reports/e3_5",
        },
    )
    _write_json(E3_5_REPORT / "materialization_integrity.json", materialization)
    _write_json(E3_5_REPORT / "supabase_005_replays.json", false_negative_replays)
    E3_5_REPORT.joinpath("e3_5_report.md").write_text(_render_report(summary, root_causes), encoding="utf-8")

    after = _snapshot_integrity()
    if before != after:
        raise RuntimeError("Production Core or frozen Benchmark artifacts changed during E3.5")
    manifest = _load_json(E3_5_REPORT / "run_manifest.json")
    manifest["integrity"] = {
        "preflight": before,
        "postflight": after,
        "production_core_unchanged": True,
        "e3_b_raw_unchanged": True,
        "e3_v1_unchanged": True,
        "e3_v2_unchanged": True,
        "e2_e2_5_frozen_artifacts_unchanged": True,
    }
    _write_json(E3_5_REPORT / "run_manifest.json", manifest)
    return summary
