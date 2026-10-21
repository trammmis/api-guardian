"""无需 FastAPI 或数据库调用的 API Guardian 命令行入口."""

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any
from uuid import uuid4

from rich.console import Console

from app.services.guardian.graph import create_guardian_graph
from app.services.guardian.models import GuardianVerdict


def _parser() -> argparse.ArgumentParser:
    """构造只接受 Guardian 业务输入的 CLI parser."""
    parser = argparse.ArgumentParser(description="运行 API Guardian 确定性回归 Vertical Slice")
    parser.add_argument("--base", required=True, type=Path, help="Base OpenAPI JSON/YAML")
    parser.add_argument("--head", required=True, type=Path, help="Head OpenAPI JSON/YAML")
    parser.add_argument("--url", required=True, help="已进入 Guardian allowlist 的目标 API base URL")
    return parser


async def _run(base: Path, head: Path, target_base_url: str) -> dict[str, Any]:
    """执行完整 graph 并构造显式白名单 JSON 输出."""
    scan_id = uuid4().hex
    graph = create_guardian_graph()
    state = await graph.ainvoke(
        {
            "scan_id": scan_id,
            "base_spec_path": str(base),
            "head_spec_path": str(head),
            "target_base_url": target_base_url,
        }
    )
    contract_verdict = state.get("contract_verdict")
    regression_verdict = state.get("regression_verdict")
    final_verdict = state.get("final_verdict", GuardianVerdict.ERROR)
    selected_scope = state.get("selected_scope")
    regression_tool = state.get("regression_tool")
    return {
        "scan_id": scan_id,
        "contract_verdict": contract_verdict.value if contract_verdict else "error",
        "regression_verdict": regression_verdict.value if regression_verdict else "error",
        "final_verdict": final_verdict.value,
        "changes": [change.model_dump(mode="json") for change in state.get("contract_changes", ())],
        "selected_operations": [operation.model_dump(mode="json") for operation in selected_scope.operations]
        if selected_scope
        else [],
        "regression_results": [result.model_dump(mode="json") for result in state.get("regression_results", ())],
        "regression_summary": regression_tool.model_dump(mode="json") if regression_tool else None,
        "errors": [error.model_dump(mode="json") for error in state.get("errors", ())],
        "analysis": {
            "enabled": state.get("analysis_enabled", False),
            "degraded": state.get("analysis_degraded", False),
            "risk_explanations": [
                explanation.model_dump(mode="json") for explanation in state.get("risk_explanations", ())
            ],
            "failure_diagnoses": [
                diagnosis.model_dump(mode="json") for diagnosis in state.get("failure_diagnoses", ())
            ],
            "warnings": list(state.get("analysis_warnings", ())),
            "truncated": state.get("analysis_truncated", False),
        },
    }


def main() -> None:
    """运行 CLI，以 PASS=0、FAIL=1、ERROR=2 返回进程状态."""
    args = _parser().parse_args()
    payload = asyncio.run(_run(args.base, args.head, args.url))
    Console().print_json(json=json.dumps(payload, ensure_ascii=False))
    exit_codes = {"pass": 0, "fail": 1, "error": 2}
    raise SystemExit(exit_codes[payload["final_verdict"]])


if __name__ == "__main__":
    main()
