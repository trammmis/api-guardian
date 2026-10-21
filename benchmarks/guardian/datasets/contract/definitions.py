"""E2 Contract Dataset：12 类人工 Ground Truth × 5 个 SUT。."""

import json
import re
from typing import Any

import yaml

from app.services.guardian.models import GuardianVerdict
from benchmarks.guardian.config import DATASET_ROOT
from benchmarks.guardian.models import ContractCase, PatchMode, SpecPatch

CONTRACT_ROOT = DATASET_ROOT / "contract"
GENERATED_ROOT = CONTRACT_ROOT / "generated"
CASES_PATH = CONTRACT_ROOT / "cases.jsonl"

SUT_OPERATION_KEYS: dict[str, tuple[str, ...]] = {
    "users_api": (
        "GET /users",
        "GET /users/{user_id}",
        "GET /profiles/{user_id}",
        "GET /teams",
        "GET /teams/{team_id}",
        "GET /roles",
        "GET /permissions",
        "GET /status",
    ),
    "orders_api": (
        "GET /orders",
        "GET /orders/{order_id}",
        "GET /orders/{order_id}/summary",
        "GET /customers/{customer_id}/orders",
        "GET /invoices/{invoice_id}",
        "GET /shipments/{shipment_id}",
        "GET /currencies",
        "GET /status",
    ),
    "products_api": (
        "GET /products/primary",
        "GET /products",
        "GET /categories/primary",
        "GET /categories",
        "GET /brands/primary",
        "GET /prices/current",
        "GET /reviews/latest",
        "GET /status",
    ),
    "inventory_api": (
        "GET /stock/primary",
        "GET /stock",
        "GET /warehouses/primary",
        "GET /warehouses",
        "GET /reservations/primary",
        "GET /movements/latest",
        "GET /suppliers/primary",
        "GET /status",
    ),
    "booking_api": (
        "GET /bookings/primary",
        "GET /bookings",
        "GET /rooms/primary",
        "GET /rooms",
        "GET /guests/primary",
        "GET /payments/latest",
        "GET /availability/current",
        "GET /status",
    ),
}


def ensure_contract_dataset() -> tuple[ContractCase, ...]:
    """确定性生成五份 Base、60 个 Head 和可审计 JSONL Ground Truth。."""
    GENERATED_ROOT.mkdir(parents=True, exist_ok=True)
    cases: list[ContractCase] = []
    for sut_id, operation_keys in SUT_OPERATION_KEYS.items():
        base_path = GENERATED_ROOT / f"{sut_id}_base.yaml"
        base_path.write_text(
            yaml.safe_dump(_build_base_document(sut_id, operation_keys), sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )
        cases.extend(_build_cases(sut_id, operation_keys))
    ordered = tuple(sorted(cases, key=lambda case: case.case_id))
    with CASES_PATH.open("w", encoding="utf-8", newline="\n") as handle:
        for case in ordered:
            handle.write(json.dumps(case.model_dump(mode="json"), ensure_ascii=False, sort_keys=True) + "\n")
    return ordered


def _build_base_document(sut_id: str, operation_keys: tuple[str, ...]) -> dict[str, Any]:
    """创建具有 query、404 和双 operation internal component 引用的统一 Base。."""
    paths: dict[str, Any] = {}
    for index, operation_key in enumerate(operation_keys):
        _, path = operation_key.split(" ", maxsplit=1)
        response_schema: dict[str, Any]
        if index >= len(operation_keys) - 2:
            response_schema = {"$ref": "#/components/schemas/SharedEntity"}
        else:
            response_schema = _entity_schema()
        paths[path] = {
            "get": {
                "operationId": f"{sut_id}_{index}",
                "description": f"Controlled contract operation {index}",
                "parameters": _parameters(path),
                "responses": {
                    "200": {
                        "description": "Success",
                        "content": {"application/json": {"schema": response_schema}},
                    },
                    "404": {"description": "Not found"},
                },
            }
        }
    return {
        "openapi": "3.0.3",
        "info": {"title": f"Guardian Contract {sut_id}", "version": "1.0.0"},
        "paths": paths,
        "components": {"schemas": {"SharedEntity": _entity_schema()}},
    }


def _entity_schema() -> dict[str, Any]:
    """返回每个 Contract Case 使用的稳定响应模型。."""
    return {
        "type": "object",
        "required": ["id", "label"],
        "properties": {
            "id": {"type": "string"},
            "label": {"type": "string"},
            "status": {"type": "string", "enum": ["active", "inactive"]},
        },
    }


def _parameters(path: str, *, required: bool = False, narrowed: bool = False, integer: bool = False) -> list[Any]:
    """构造 path 参数和一个可变 query 参数。."""
    parameters: list[Any] = [
        {
            "name": name,
            "in": "path",
            "required": True,
            "schema": {"type": "string"},
        }
        for name in re.findall(r"\{([^{}]+)\}", path)
    ]
    query_schema: dict[str, Any] = {"type": "integer"} if integer else {"type": "string", "enum": ["a", "b"]}
    if narrowed:
        query_schema["enum"] = ["a"]
    parameters.append({"name": "mode", "in": "query", "required": required, "schema": query_schema})
    return parameters


def _build_cases(sut_id: str, operation_keys: tuple[str, ...]) -> tuple[ContractCase, ...]:
    """从人工定义的 12 类契约场景构造稳定 Case。."""
    paths = tuple(key.split(" ", maxsplit=1)[1] for key in operation_keys)
    base = f"benchmarks/guardian/datasets/contract/generated/{sut_id}_base.yaml"
    head_prefix = f"benchmarks/guardian/datasets/contract/generated/{sut_id}"
    response = ("get", "responses", "200", "content", "application/json", "schema")

    def case(
        suffix: str,
        change_type: str,
        patches: tuple[SpecPatch, ...],
        verdict: GuardianVerdict,
        affected: tuple[str, ...],
        reason: str,
    ) -> ContractCase:
        return ContractCase(
            case_id=f"{sut_id}_{suffix}",
            sut_id=sut_id,
            change_type=change_type,
            base_spec=base,
            head_spec=f"{head_prefix}_{suffix}.yaml",
            patches=patches,
            expected_contract_verdict=verdict,
            expected_affected_operations=affected,
            reason=reason,
        )

    return (
        case(
            "endpoint_removed",
            "endpoint_removed",
            (SpecPatch(path=("paths", paths[0]), mode=PatchMode.DELETE, value=None),),
            GuardianVerdict.FAIL,
            (operation_keys[0],),
            "删除 endpoint 会使既有调用方无法访问。",
        ),
        case(
            "method_removed",
            "method_removed",
            (SpecPatch(path=("paths", paths[1], "get"), mode=PatchMode.DELETE, value=None),),
            GuardianVerdict.FAIL,
            (operation_keys[1],),
            "删除 HTTP method 会破坏既有调用。",
        ),
        case(
            "response_field_removed",
            "response_field_removed",
            (
                SpecPatch(
                    path=("paths", paths[2], *response, "properties", "label"), mode=PatchMode.DELETE, value=None
                ),
            ),
            GuardianVerdict.FAIL,
            (operation_keys[2],),
            "删除响应字段会破坏依赖该字段的 consumer。",
        ),
        case(
            "response_type_changed",
            "response_type_changed",
            (SpecPatch(path=("paths", paths[3], *response, "properties", "id", "type"), value="integer"),),
            GuardianVerdict.FAIL,
            (operation_keys[3],),
            "响应 id 从 string 改为 integer。",
        ),
        case(
            "required_query_added",
            "required_request_parameter_added",
            (
                SpecPatch(
                    path=("paths", paths[4], "get", "parameters"),
                    value=(
                        *_parameters(paths[4]),
                        {"name": "limit", "in": "query", "required": True, "schema": {"type": "integer"}},
                    ),
                ),
            ),
            GuardianVerdict.FAIL,
            (operation_keys[4],),
            "新增 required query parameter。",
        ),
        case(
            "optional_to_required",
            "optional_request_parameter_became_required",
            (SpecPatch(path=("paths", paths[5], "get", "parameters"), value=_parameters(paths[5], required=True)),),
            GuardianVerdict.FAIL,
            (operation_keys[5],),
            "既有 optional query parameter 变为 required。",
        ),
        case(
            "response_status_removed",
            "response_status_removed",
            (SpecPatch(path=("paths", paths[2], "get", "responses", "404"), mode=PatchMode.DELETE, value=None),),
            GuardianVerdict.FAIL,
            (operation_keys[2],),
            "删除已声明响应状态会改变公开契约。",
        ),
        case(
            "request_enum_narrowed",
            "request_enum_narrowed",
            (SpecPatch(path=("paths", paths[3], "get", "parameters"), value=_parameters(paths[3], narrowed=True)),),
            GuardianVerdict.FAIL,
            (operation_keys[3],),
            "请求 enum 缩窄会拒绝以前合法的值。",
        ),
        case(
            "request_type_changed",
            "request_type_changed",
            (SpecPatch(path=("paths", paths[4], "get", "parameters"), value=_parameters(paths[4], integer=True)),),
            GuardianVerdict.FAIL,
            (operation_keys[4],),
            "请求 mode 从 string 改为 integer。",
        ),
        case(
            "compatible_field_added",
            "compatible_response_field_added",
            (
                SpecPatch(
                    path=("paths", paths[5], *response, "properties", "note"),
                    mode=PatchMode.ADD,
                    value={"type": "string"},
                ),
            ),
            GuardianVerdict.PASS,
            (),
            "新增 optional response field 按当前 policy 视为兼容。",
        ),
        case(
            "documentation_only",
            "documentation_only",
            (SpecPatch(path=("paths", paths[0], "get", "description"), value="Updated documentation only"),),
            GuardianVerdict.PASS,
            (),
            "文档文字变化不影响 API contract。",
        ),
        case(
            "component_type_changed",
            "multi_operation_component_change",
            (SpecPatch(path=("components", "schemas", "SharedEntity", "properties", "id", "type"), value="integer"),),
            GuardianVerdict.FAIL,
            operation_keys[-2:],
            "共享 component 的类型变化真实影响最后两个 operations。",
        ),
    )
