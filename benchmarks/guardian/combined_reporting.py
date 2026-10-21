"""从不可变的 E1/E2 summary 生成跨阶段综合实验报告。."""

import csv
import json
from pathlib import Path
from typing import Any

from benchmarks.guardian.config import REPORT_ROOT

COMBINED_REPORT_ROOT = REPORT_ROOT / "combined" / "latest"


def generate_combined_report(
    e1_summary_path: Path = REPORT_ROOT / "latest" / "summary.json",
    e2_summary_path: Path = REPORT_ROOT / "e2" / "latest" / "summary.json",
    output_root: Path = COMBINED_REPORT_ROOT,
) -> Path:
    """读取两阶段真实聚合值，输出 JSON、CSV 和 Markdown。."""
    e1 = _read_json(e1_summary_path)
    e2 = _read_json(e2_summary_path)
    output_root.mkdir(parents=True, exist_ok=True)
    combined = {
        "benchmark": "API Guardian Effectiveness Benchmark E1 + E2",
        "source_runs": {"E1": e1["run_id"], "E2": e2["run_id"]},
        "methodology_compatibility": {
            "correctness_and_scope": "comparable_with_dataset_expansion",
            "runtime": "not_directly_poolable_due_to_full_baseline_process_strategy_change",
        },
        "E1": _stage_snapshot(e1),
        "E2": _stage_snapshot(e2),
    }
    (output_root / "combined_summary.json").write_text(
        json.dumps(combined, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    _write_csv(output_root / "combined_summary.csv", combined)
    report_path = output_root / "e1_e2_complete_experiment_report.md"
    report_path.write_text(_render_markdown(e1, e2), encoding="utf-8")
    return report_path


def _stage_snapshot(summary: dict[str, Any]) -> dict[str, Any]:
    """保留综合比较真正需要的白名单聚合值。."""
    metrics = summary["metrics"]
    scope = metrics.get("contract_scope", metrics.get("scope"))
    runtime_scope = metrics.get("runtime_scope", metrics.get("scope"))
    return {
        "run_id": summary["run_id"],
        "dataset": summary["dataset"],
        "contract_verdict_accuracy": metrics["contract_verdict_accuracy"],
        "guardian_regression": metrics["systems"]["guardian_deterministic"],
        "contract_scope": scope,
        "runtime_scope": runtime_scope,
        "performance": metrics["performance"],
        "paired_guardian_vs_full": metrics["paired_guardian_vs_full"],
        "reliability": metrics["reliability"],
        "production_guardian_core_unchanged": summary["production_guardian_core_unchanged_during_run"],
    }


def _write_csv(path: Path, combined: dict[str, Any]) -> None:
    """输出 E1/E2 一行一指标的阶段对比 CSV。."""
    rows: list[tuple[str, str, Any, Any, Any]] = []
    for stage in ("E1", "E2"):
        data = combined[stage]
        for name in ("contract_verdict_accuracy",):
            metric = data[name]
            rows.append((stage, name, metric["value"], metric["numerator"], metric["denominator"]))
        for name in (
            "bug_detection_recall",
            "bug_detection_precision",
            "in_scope_mutation_recall",
            "overall_mutation_recall",
        ):
            metric = data["guardian_regression"][name]
            rows.append((stage, name, metric["value"], metric["numerator"], metric["denominator"]))
        scope = data["contract_scope"]
        for name in ("recall", "precision"):
            metric = scope[name]
            rows.append((stage, f"scope_{name}", metric["value"], metric["numerator"], metric["denominator"]))
        rows.append((stage, "scope_reduction", scope["mean_scope_reduction"], "", ""))
        full = data["performance"]["full_schemathesis"]["wall_clock_duration_seconds"]
        guardian = data["performance"]["guardian_deterministic"]["wall_clock_duration_seconds"]
        rows.append((stage, "full_runtime_median_seconds", full["median"], "", full["sample_size"]))
        rows.append((stage, "guardian_runtime_median_seconds", guardian["median"], "", guardian["sample_size"]))
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(("stage", "metric", "value", "numerator", "denominator"))
        writer.writerows(rows)


def _render_markdown(e1: dict[str, Any], e2: dict[str, Any]) -> str:
    """生成包含方法、结果、解释和限制的完整跨阶段 Markdown。."""
    e1m = e1["metrics"]
    e2m = e2["metrics"]
    e1g = e1m["systems"]["guardian_deterministic"]
    e2g = e2m["systems"]["guardian_deterministic"]
    e1_scope = e1m["scope"]
    e2_contract_scope = e2m["contract_scope"]
    e2_runtime_scope = e2m["runtime_scope"]
    e2_request_reduction = _reduction(
        e2m["performance"]["guardian_deterministic"]["http_requests_issued"]["median"],
        e2m["performance"]["full_schemathesis"]["http_requests_issued"]["median"],
    )
    return f"""# API Guardian E1 + E2 完整实验报告

## 1. 报告范围

本报告合并 Stage E1 和 Stage E2 的真实执行结果，用于回答 API Guardian 是否在保持较高可检测 Bug Recall 的同时减少 API Regression 成本。它不包含 Real-world GitHub Dataset、LLM 正式 Evaluation 或 Human Evaluation。

生产 Guardian Core 在两个正式运行期间均保持哈希不变。结果来自保存的 per-case raw JSONL，不是手工填入的演示数字。

## 2. 两阶段实验设计

| 项目 | E1 | E2 |
| --- | ---: | ---: |
| Controlled SUTs | {e1["dataset"]["sut_count"]} | {e2["dataset"]["sut_count"]} |
| Runtime cases | {e1["dataset"]["case_count"]} | {e2["dataset"]["runtime_case_count"]} |
| Runtime mutations | {e1["dataset"]["case_count"]} | {e2["dataset"]["mutation_count"]} |
| Clean controls | 0 | {e2["dataset"]["clean_control_count"]} |
| Contract cases | E1 runtime pairs only | {e2["dataset"]["contract_case_count"]} |
| Measured repetitions | {e1["configuration"]["repetitions"]} | {e2["configuration"]["repetitions"]} |
| Warm-up | {e1["configuration"]["warmup_runs"]} | {e2["configuration"]["warmup_runs"]} |

E1 和 E2 使用相同的 oasdiff 1.28.0、Schemathesis 4.24.3、`response_schema_conformance`、`examples,coverage`、`max_examples=1`、deterministic generation、2 秒 request timeout 和 0 redirect。

### Runtime 方法差异

E1 的 Full baseline 复用了生产逐 operation Adapter，因此一个 8-operation Spec 启动 8 个 Schemathesis 进程。E2 将 Full baseline 修正为一次不带 include filter 的完整 Spec 运行。这个修正显著减少 Full baseline 的进程启动成本，因此两阶段 runtime 不应直接合并或用于趋势推断；报告分别展示。

## 3. RQ1 Detection Correctness

| 指标 | E1 | E2 |
| --- | ---: | ---: |
| Contract Verdict Accuracy | {_ratio(e1m["contract_verdict_accuracy"])} | {_ratio(e2m["contract_verdict_accuracy"])} |
| Guardian Bug Detection Recall | {_ratio(e1g["bug_detection_recall"])} | {_ratio(e2g["bug_detection_recall"])} |
| In-Scope Mutation Recall | {_ratio(e1g["in_scope_mutation_recall"])} | {_ratio(e2g["in_scope_mutation_recall"])} |
| Overall Mutation Recall | {_ratio(e1g["overall_mutation_recall"])} | {_ratio(e2g["overall_mutation_recall"])} |
| Bug Detection Precision | {_ratio(e1g["bug_detection_precision"])}* | {_ratio(e2g["bug_detection_precision"])} |

注：E1 没有 clean no-bug controls，不能用于正式 false-positive 结论。E2 增加了真实 clean controls。

E2 中 In-Scope Recall 与 Overall Recall 的差异量化了当前 Schema Oracle 的能力边界：结构错误可以检测，schema-valid 的金额、实体、库存或状态语义错误不能检测。

## 4. RQ2 Regression Efficiency

| Stage | Full median | Full p95 | Guardian median | Guardian p95 | Paired median reduction |
| --- | ---: | ---: | ---: | ---: | ---: |
| E1 | {_seconds(e1m["performance"]["full_schemathesis"]["wall_clock_duration_seconds"]["median"])} | {_seconds(e1m["performance"]["full_schemathesis"]["wall_clock_duration_seconds"]["p95"])} | {_seconds(e1m["performance"]["guardian_deterministic"]["wall_clock_duration_seconds"]["median"])} | {_seconds(e1m["performance"]["guardian_deterministic"]["wall_clock_duration_seconds"]["p95"])} | {_percent(e1m["paired_guardian_vs_full"]["runtime_reduction"]["median"])} |
| E2 | {_seconds(e2m["performance"]["full_schemathesis"]["wall_clock_duration_seconds"]["median"])} | {_seconds(e2m["performance"]["full_schemathesis"]["wall_clock_duration_seconds"]["p95"])} | {_seconds(e2m["performance"]["guardian_deterministic"]["wall_clock_duration_seconds"]["median"])} | {_seconds(e2m["performance"]["guardian_deterministic"]["wall_clock_duration_seconds"]["p95"])} | {_percent(e2m["paired_guardian_vs_full"]["runtime_reduction"]["median"])} |

E2 是更可信的 performance 结论，因为 Full baseline 使用单进程完整 Spec。North Star 结论应以 E2 的 In-Scope Recall、Scope Reduction 和 paired runtime reduction 联合表述，而不是只引用 E1 的较大时间差。

### E2 North Star

- In-Scope Bug Recall：{_ratio(e2g["in_scope_mutation_recall"])}
- Operations Reduction：{_percent(e2_runtime_scope["mean_scope_reduction"])}（8 → 1）
- HTTP Request Reduction：{_percent(e2_request_reduction)}（65 → 9）
- Paired Median Runtime Reduction：{_percent(e2m["paired_guardian_vs_full"]["runtime_reduction"]["median"])}

因此，E2 证明了测试操作数与 HTTP 请求数的下降，但没有证明小型 8-operation SUT 上的端到端时间收益。Guardian 的逐 operation 子进程与 oasdiff 编排成本抵消了请求减少；这是真实负结果，不应以 E1 的 84.81% 取代。

## 5. RQ3 Scope Quality

| 指标 | E1 Runtime Dataset | E2 Runtime Dataset | E2 Contract Dataset |
| --- | ---: | ---: | ---: |
| Scope Recall | {_ratio(e1_scope["recall"])} | {_ratio(e2_runtime_scope["recall"])} | {_ratio(e2_contract_scope["recall"])} |
| Scope Precision | {_ratio(e1_scope["precision"])} | {_ratio(e2_runtime_scope["precision"])} | {_ratio(e2_contract_scope["precision"])} |
| Mean Scope Reduction | {_percent(e1_scope["mean_scope_reduction"])} | {_percent(e2_runtime_scope["mean_scope_reduction"])} | {_percent(e2_contract_scope["mean_scope_reduction"])} |
| Mean Oracle Overhead | {float(e1_scope["mean_oracle_overhead_operations"]):.4f} | N/A | {float(e2_contract_scope["mean_oracle_overhead_operations"]):.4f} |

E2 Contract Dataset 包含 removed operations 与 component changes，因此比 E1 的直接单-operation changes 更能揭示 scope 的真实边界。removed endpoint/method 在 Head Spec 中已经不存在，不能成为 Schemathesis 的可执行 operation，这是 Contract affected scope 与 Runtime executable scope 之间的结构性差异。

## 6. RQ4 LLM Diagnosis Utility

E1/E2 均显式禁用 LLM。当前报告不能声称 RiskExplanation 或 FailureDiagnosis 提升了理解效率。LLM Evaluation 与匿名 Human Study 必须在后续独立阶段完成。

## 7. RQ5 Reliability and Cost

| 指标 | E1 | E2 |
| --- | ---: | ---: |
| Deterministic Reproducibility | {_ratio(e1m["reliability"]["deterministic_reproducibility"])} | {_ratio(e2m["reliability"]["deterministic_reproducibility"])} |
| LLM Calls | 0 | 0 |
| LLM Cost | N/A | N/A |

E1/E2 没有运行 LLM，因此不存在 token/cost 数据。Dedicated timeout、missing executable、malformed NDJSON 和 checkpoint interruption 的 Benchmark 仍需独立 Reliability Dataset；现有产品测试只能作为非回归证据，不能替代正式 Benchmark。

## 8. 可归因的工程价值

- oasdiff 提供 Breaking Change Detection，本项目不把这部分包装为自研算法。
- Schemathesis 提供基于 OpenAPI 的请求生成和响应 Schema 检查。
- API Guardian 本阶段被测的增量价值是：change-aware scope、两类事实的统一编排、确定性 verdict 和一致的可审计报告。

## 9. 主要限制

1. 五个 SUT 仍是本地 synthetic FastAPI 服务，外部有效性有限。
2. Runtime Dataset 仍以无认证 GET operations 为主。
3. 当前 oracle 无法发现任意业务语义 Bug。
4. Contract response-status removal 的人工 Ground Truth 与 oasdiff 可能存在定义差异，报告按原始结果保留。
5. E1/E2 runtime 方法不同，不能把样本直接合并计算一个总平均值。
6. 尚未执行 Real-world Dataset、LLM Evaluation 或真实 Human Evaluation。

## 10. 复现与审计

- E1 run: `{e1["run_id"]}`
- E2 run: `{e2["run_id"]}`
- E1 raw: `benchmarks/guardian/reports/latest/raw_results.jsonl`
- E2 Contract raw: `benchmarks/guardian/reports/e2/latest/contract_raw_results.jsonl`
- E2 Runtime raw: `benchmarks/guardian/reports/e2/latest/raw_results.jsonl`
- 两阶段各自的 `run_manifest.json` 保存输入哈希、工具版本、Python、OS、CPU、Git 状态和固定参数。
- E2 raw 共 1320 行（55 cases × 4 systems × 1 warm-up/5 measured），Contract raw 共 60 行。
- 被中断运行保存在 `benchmarks/guardian/reports/e2/interrupted/`，明确不参与聚合。
"""


def _read_json(path: Path) -> dict[str, Any]:
    """读取并验证报告源是 JSON object。."""
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"summary must be a JSON object: {path}")
    return value


def _ratio(value: dict[str, Any]) -> str:
    """输出带分子分母的百分比。."""
    return f"{value['numerator']}/{value['denominator']} ({_percent(value['value'])})"


def _percent(value: Any) -> str:
    """输出百分比或 N/A。."""
    return "N/A" if value is None else f"{float(value) * 100:.2f}%"


def _seconds(value: Any) -> str:
    """输出秒数或 N/A。."""
    return "N/A" if value is None else f"{float(value):.4f} s"


def _reduction(selected: Any, full: Any) -> float | None:
    """计算选定系统相对 Full baseline 的减少量。."""
    full_value = float(full)
    return None if full_value == 0 else 1 - float(selected) / full_value
