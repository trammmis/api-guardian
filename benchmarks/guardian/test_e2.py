"""Stage E2 Harness、Ground Truth 与 Full NDJSON parser 的离线自检。."""

import json
from typing import Any

import pytest

from app.services.guardian.models import GuardianVerdict
from app.services.guardian.security import prepare_openapi_spec
from benchmarks.guardian import runner
from benchmarks.guardian.baselines.full_cli import _parse_full_report
from benchmarks.guardian.datasets.contract.definitions import ensure_contract_dataset
from benchmarks.guardian.mutations.apply import (
    materialize_all,
    materialize_all_contract_heads,
    resolve_project_path,
)
from benchmarks.guardian.mutations.definitions import load_e2_mutation_cases


def test_e2_runtime_dataset_has_five_suts_fifty_mutations_and_clean_controls() -> None:
    """E2 规模和无法检测的负对照不能被静默删除。."""
    cases = load_e2_mutation_cases()

    assert len(cases) == 55
    assert len({case.case_id for case in cases}) == 55
    assert len({case.sut_id for case in cases}) == 5
    assert sum(case.ground_truth_bug_present for case in cases) == 50
    assert sum(case.expected_detectable_by_current_oracle for case in cases) == 40
    assert sum(not case.ground_truth_bug_present for case in cases) == 5
    heads = materialize_all(cases)
    for case in cases:
        assert len(prepare_openapi_spec(str(resolve_project_path(case.base_spec)), 5 * 1024 * 1024).operations) == 8
        assert len(prepare_openapi_spec(str(heads[case.case_id]), 5 * 1024 * 1024).operations) == 8


def test_e2_contract_dataset_has_sixty_human_ground_truth_cases() -> None:
    """Contract Dataset 覆盖 12 类变化和五个 SUT。."""
    cases = ensure_contract_dataset()
    heads = materialize_all_contract_heads(cases)

    assert len(cases) == 60
    assert len({case.case_id for case in cases}) == 60
    assert len({case.sut_id for case in cases}) == 5
    assert len({case.change_type for case in cases}) == 12
    assert sum(case.expected_contract_verdict == GuardianVerdict.FAIL for case in cases) == 50
    assert sum(case.expected_contract_verdict == GuardianVerdict.PASS for case in cases) == 10
    assert len(heads) == 60


def test_full_ndjson_parser_distinguishes_pass_fail_and_error() -> None:
    """Full baseline 不能把 exit code=1 机械映射为 API FAIL。."""
    passing = _report(check_status="success", scenario_status="success", stop_reason="completed")
    failing = _report(check_status="failure", scenario_status="failure", stop_reason="completed")
    error = _report(check_status="failure", scenario_status="error", stop_reason="completed")

    assert _parse_full_report(passing, 8, 0) == GuardianVerdict.PASS
    assert _parse_full_report(failing, 8, 1) == GuardianVerdict.FAIL
    assert _parse_full_report(error, 8, 1) == GuardianVerdict.ERROR


def test_benchmark_control_client_ignores_system_proxy(monkeypatch: pytest.MonkeyPatch) -> None:
    """本地 reset/metrics 控制请求不能被系统代理劫持。."""
    captured: dict[str, object] = {}
    sentinel = object()

    def fake_async_client(**kwargs: object) -> Any:
        captured.update(kwargs)
        return sentinel

    monkeypatch.setattr(runner.httpx, "AsyncClient", fake_async_client)

    assert runner._control_client() is sentinel
    assert captured == {"timeout": 5, "trust_env": False}


def test_benchmark_rejects_abbreviated_audit_sha256() -> None:
    """可复现元数据不能接受省略号形式的 SHA256。."""
    with pytest.raises(ValueError, match="64 hexadecimal"):
        runner._validate_optional_sha256("cef8...")

    value = "cef8ec7cdd32ab4ce9c188f9dbb50452a91fa36cc11c8e1dcf340b19282c3ce1"
    assert runner._validate_optional_sha256(value.upper()) == value


def _report(*, check_status: str, scenario_status: str, stop_reason: str) -> bytes:
    """生成只含白名单字段的最小多 operation NDJSON。."""
    events = (
        {"Initialize": {"schemathesis_version": "4.24.3"}},
        {"LoadingFinished": {"statistic": {"operations": {"selected": 8}}}},
        {
            "ScenarioFinished": {
                "status": scenario_status,
                "recorder": {"checks": {"case": [{"name": "response_schema_conformance", "status": check_status}]}},
            }
        },
        {"EngineFinished": {"stop_reason": stop_reason}},
    )
    return b"\n".join(json.dumps(event).encode() for event in events)
