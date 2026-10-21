"""从 JSONL 加载 Stage E1 的固定 Mutation Dataset。."""

import json
from pathlib import Path

from pydantic import TypeAdapter

from benchmarks.guardian.config import DATASET_ROOT
from benchmarks.guardian.models import MutationCase

_CASE_ADAPTER = TypeAdapter(MutationCase)
DEFAULT_CASES_PATH = DATASET_ROOT / "mutation" / "cases.jsonl"
E2_CASES_GLOB = "e2_*.jsonl"


def load_mutation_cases(path: Path = DEFAULT_CASES_PATH) -> tuple[MutationCase, ...]:
    """加载 JSONL，并验证 case_id 唯一与稳定排序。."""
    cases: list[MutationCase] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            cases.append(_CASE_ADAPTER.validate_python(json.loads(line)))
    case_ids = [case.case_id for case in cases]
    if len(case_ids) != len(set(case_ids)):
        raise ValueError("mutation case_id must be unique")
    return tuple(sorted(cases, key=lambda case: case.case_id))


def load_e2_mutation_cases() -> tuple[MutationCase, ...]:
    """加载按 SUT 拆分的 E2 Runtime Dataset。."""
    paths = tuple(sorted((DATASET_ROOT / "mutation").glob(E2_CASES_GLOB)))
    if not paths:
        raise ValueError("E2 mutation dataset is missing")
    cases = tuple(case for path in paths for case in load_mutation_cases(path))
    case_ids = [case.case_id for case in cases]
    if len(case_ids) != len(set(case_ids)):
        raise ValueError("E2 mutation case_id must be unique")
    return tuple(sorted(cases, key=lambda case: case.case_id))
