"""Stage E2.5 Scaling Dataset 的确定性 generator。."""

import hashlib
import json
from pathlib import Path
from typing import Any, Final

from benchmarks.guardian.config import DATASET_ROOT, PROJECT_ROOT
from benchmarks.guardian.e2_5_models import ScalingCase, ScalingConfig, ScalingScenario

GENERATOR_VERSION: Final = "guardian_scaling_generator_v1"
SCALING_DATASET_ROOT = DATASET_ROOT / "scaling"
GENERATED_ROOT = SCALING_DATASET_ROOT / "generated"
CASES_PATH = SCALING_DATASET_ROOT / "cases.jsonl"


def ensure_scaling_dataset(config: ScalingConfig) -> tuple[ScalingCase, ...]:
    """按固定排序生成 8--128 operation、S1/S2 的 Base/Head Spec。."""
    GENERATED_ROOT.mkdir(parents=True, exist_ok=True)
    cases: list[ScalingCase] = []
    for total in config.sizes:
        for scenario, affected_count in (
            (ScalingScenario.ONE_OPERATION, 1),
            (ScalingScenario.FIVE_PERCENT, max(1, round(total * 0.05))),
        ):
            affected_indexes = tuple(range(1, affected_count + 1))
            case_id = f"scale_{total}_{scenario.value}"
            base_path = GENERATED_ROOT / f"{case_id}_base.json"
            head_path = GENERATED_ROOT / f"{case_id}_head.json"
            _write_spec(base_path, _openapi_document(total, frozenset(affected_indexes), head=False))
            _write_spec(head_path, _openapi_document(total, frozenset(affected_indexes), head=True))
            cases.append(
                ScalingCase(
                    case_id=case_id,
                    total_operations=total,
                    scenario=scenario,
                    affected_operations=tuple(_operation_key(index) for index in affected_indexes),
                    base_spec=base_path.relative_to(PROJECT_ROOT).as_posix(),
                    head_spec=head_path.relative_to(PROJECT_ROOT).as_posix(),
                    generator_version=GENERATOR_VERSION,
                )
            )
    ordered = tuple(sorted(cases, key=lambda item: (item.total_operations, item.scenario.value)))
    with CASES_PATH.open("w", encoding="utf-8", newline="\n") as handle:
        for case in ordered:
            handle.write(json.dumps(case.model_dump(mode="json"), sort_keys=True) + "\n")
    return ordered


def scaling_dataset_sha256(cases: tuple[ScalingCase, ...]) -> str:
    """把 manifest 与全部生成 Spec 纳入一个稳定 Dataset 指纹。."""
    digest = hashlib.sha256()
    for path in (
        CASES_PATH,
        *(PROJECT_ROOT / case.base_spec for case in cases),
        *(PROJECT_ROOT / case.head_spec for case in cases),
    ):
        digest.update(path.relative_to(PROJECT_ROOT).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def affected_indexes(case: ScalingCase) -> frozenset[int]:
    """从 generator 的稳定 operation key 恢复 SUT mutation index。."""
    indexes: set[int] = set()
    for key in case.affected_operations:
        indexes.add(int(key.rsplit("/", maxsplit=1)[1]))
    return frozenset(indexes)


def _openapi_document(total: int, affected: frozenset[int], *, head: bool) -> dict[str, Any]:
    """Base 的 affected value 为 string，Head 统一要求 integer。."""
    paths: dict[str, Any] = {}
    for index in range(1, total + 1):
        value_type = "integer" if head or index not in affected else "string"
        paths[_path(index)] = {
            "get": {
                "operationId": _operation_id(index),
                "description": f"Controlled scaling operation {index}",
                "responses": {
                    "200": {
                        "description": "Success",
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "required": ["id", "value"],
                                    "additionalProperties": False,
                                    "properties": {
                                        "id": {"type": "integer"},
                                        "value": {"type": value_type},
                                    },
                                }
                            }
                        },
                    }
                },
            }
        }
    return {
        "openapi": "3.0.3",
        "info": {"title": "Guardian Scaling API", "version": "1.0.0" if not head else "2.0.0"},
        "paths": paths,
    }


def _write_spec(path: Path, document: dict[str, Any]) -> None:
    path.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _path(index: int) -> str:
    return f"/resources/{index:04d}"


def _operation_id(index: int) -> str:
    return f"getResource{index:04d}"


def _operation_key(index: int) -> str:
    return f"GET {_path(index)}"
