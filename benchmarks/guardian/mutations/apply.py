"""从 Base OpenAPI 以固定 patch 生成可复查 Head OpenAPI。."""

from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml

from benchmarks.guardian.config import BENCHMARK_ROOT, PROJECT_ROOT
from benchmarks.guardian.models import ContractCase, MutationCase, PatchMode, SpecPatch


def resolve_project_path(relative_path: str) -> Path:
    """解析 Dataset 中只允许位于项目根目录内的相对路径。."""
    candidate = (PROJECT_ROOT / relative_path).resolve()
    try:
        candidate.relative_to(PROJECT_ROOT)
    except ValueError as exc:
        raise ValueError("benchmark dataset path escapes project root") from exc
    return candidate


def materialize_head_spec(case: MutationCase) -> Path:
    """应用单点 mutation 并把 Head Spec 写到 Dataset 的固定位置。."""
    base_path = resolve_project_path(case.base_spec)
    head_path = resolve_project_path(case.head_spec)
    if BENCHMARK_ROOT not in head_path.parents:
        raise ValueError("generated head spec must remain inside benchmark root")
    with base_path.open("r", encoding="utf-8") as handle:
        document = yaml.safe_load(handle)
    if not isinstance(document, dict):
        raise ValueError("base OpenAPI must be an object")
    mutated = deepcopy(document)
    info = mutated.get("info")
    if not isinstance(info, dict):
        raise ValueError("base OpenAPI info must be an object")
    # oasdiff 1.28.0 会检查 breaking change 是否伴随 major version bump；
    # 所有 E1 Head 统一从 1.x 升至 2.0.0，避免引入无 operation 归属的版本错误。
    info["version"] = "2.0.0"
    if case.selection_patch is not None:
        _apply_one_patch(mutated, case.case_id, case.selection_patch)
    _apply_one_patch(mutated, case.case_id, case.head_patch)
    head_path.parent.mkdir(parents=True, exist_ok=True)
    with head_path.open("w", encoding="utf-8", newline="\n") as handle:
        yaml.safe_dump(mutated, handle, sort_keys=False, allow_unicode=True)
    return head_path


def materialize_all(cases: tuple[MutationCase, ...]) -> dict[str, Path]:
    """按 case_id 稳定顺序生成所有 Head Specs。."""
    return {case.case_id: materialize_head_spec(case) for case in cases}


def materialize_contract_head(case: ContractCase) -> Path:
    """应用一组确定性 patch，生成 Contract Dataset 的 Head Spec。."""
    base_path = resolve_project_path(case.base_spec)
    head_path = resolve_project_path(case.head_spec)
    if BENCHMARK_ROOT not in head_path.parents:
        raise ValueError("generated contract head must remain inside benchmark root")
    with base_path.open("r", encoding="utf-8") as handle:
        document = yaml.safe_load(handle)
    if not isinstance(document, dict):
        raise ValueError("contract base OpenAPI must be an object")
    mutated = deepcopy(document)
    info = mutated.get("info")
    if not isinstance(info, dict):
        raise ValueError("contract base OpenAPI info must be an object")
    info["version"] = "2.0.0"
    for patch in case.patches:
        _apply_one_patch(mutated, case.case_id, patch)
    head_path.parent.mkdir(parents=True, exist_ok=True)
    with head_path.open("w", encoding="utf-8", newline="\n") as handle:
        yaml.safe_dump(mutated, handle, sort_keys=False, allow_unicode=True)
    return head_path


def materialize_all_contract_heads(cases: tuple[ContractCase, ...]) -> dict[str, Path]:
    """按 case_id 稳定生成全部 Contract Head Specs。."""
    return {case.case_id: materialize_contract_head(case) for case in cases}


def _apply_one_patch(document: dict[str, Any], case_id: str, patch: SpecPatch) -> None:
    """执行白名单 patch；路径不符合预期时立即失败。."""
    parent: Any = document
    for segment in patch.path[:-1]:
        if not isinstance(parent, dict) or segment not in parent:
            raise ValueError(f"patch path not found for {case_id}: {segment}")
        parent = parent[segment]
    final = patch.path[-1]
    if not isinstance(parent, dict):
        raise ValueError(f"patch parent is not an object for {case_id}: {final}")
    if patch.mode == PatchMode.ADD:
        if final in parent:
            raise ValueError(f"patch add target already exists for {case_id}: {final}")
        parent[final] = deepcopy(patch.value)
        return
    if final not in parent:
        raise ValueError(f"patch target not found for {case_id}: {final}")
    if patch.mode == PatchMode.DELETE:
        del parent[final]
        return
    if patch.mode == PatchMode.SET:
        parent[final] = deepcopy(patch.value)
        return
    target = parent[final]
    if not isinstance(target, list):
        raise ValueError(f"append target must be a list for {case_id}")
    if patch.value not in target:
        target.append(deepcopy(patch.value))
