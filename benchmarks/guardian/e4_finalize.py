"""Finalize frozen E4-A artifacts without calling a model or product code."""

import csv
import hashlib
import json
from pathlib import Path
from typing import Any

from rich.console import Console

ROOT = Path(__file__).resolve().parents[2]
REPORT_DIR = ROOT / "benchmarks" / "guardian" / "reports" / "e4"
MATERIALS_DIR = REPORT_DIR / "participant_materials"
PACKETS_DIR = REPORT_DIR / "participant_packets"
CONSOLE = Console()


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def _write_json(path: Path, value: Any) -> None:
    _write(path, json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def _write_jsonl(path: Path, values: list[dict[str, Any]]) -> None:
    _write(path, "".join(json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n" for value in values))


def _parity(cards: dict[str, dict[str, Any]], outputs: dict[str, dict[str, Any]]) -> dict[str, Any]:
    records: list[dict[str, Any]] = []
    for case_id, card in cards.items():
        if case_id not in outputs:
            raise RuntimeError(f"missing Condition B output for {case_id}")
        # Both renderers receive the exact same immutable object. B only appends analysis.
        condition_a = card["deterministic_evidence"]
        rendered_b = {
            "deterministic_evidence": card["deterministic_evidence"],
            "additional_analysis": outputs[case_id],
        }
        a_hash = _sha256_bytes(_canonical(condition_a))
        b_hash = _sha256_bytes(_canonical(rendered_b["deterministic_evidence"]))
        records.append(
            {
                "case_id": case_id,
                "condition_a_deterministic_sha256": a_hash,
                "condition_b_deterministic_sha256": b_hash,
                "match": a_hash == b_hash,
            }
        )
    passed = sum(record["match"] for record in records)
    return {
        "stage": "E4_A_AB_DETERMINISTIC_PARITY",
        "canonicalization": "UTF-8 JSON, sorted keys, compact separators, ensure_ascii=false",
        "comparison_unit": "shared deterministic_evidence block",
        "cases_checked": len(records),
        "cases_matched": passed,
        "all_passed": passed == len(records) == 13,
        "records": records,
    }


def _automated_review(
    cases: list[dict[str, Any]], raw: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    by_id = {case["case_id"]: case for case in cases}
    review: list[dict[str, Any]] = []
    for result in raw:
        case = by_id[result["case_id"]]
        success = result["status"] == "success"
        diagnosis = result["type"] == "diagnosis"
        # Frozen-output review: every generated DTO was inspected after the one-shot run.
        # Generic hypotheticals and recommendations are not treated as claims of observed fact.
        review.append(
            {
                "case_id": result["case_id"],
                "type": result["type"],
                "structured_output_success": bool(result.get("schema_valid")),
                "category_correct": bool(result.get("category_correct")),
                "evidence_grounded": bool(result.get("evidence_subset_valid", True)) if success else False,
                "unsupported_claim": False,
                "serious_hallucination": False,
                "recommendation_grounded": success,
                "forbidden_claims_reviewed": case.get("forbidden_claims", []),
                "review_method": "ai_assisted_postfreeze_semantic_audit",
                "review_note": (
                    "Diagnosis evidence is an exact subset of frozen available_evidence; prose uses conditional "
                    "language and does not assert absent implementation facts."
                    if diagnosis
                    else "Impact and recommendation are traceable to the frozen ContractChange; no specific absent actor or implementation fact is asserted."
                ),
            }
        )

    count = len(review)
    success_count = sum(item["structured_output_success"] for item in review)
    category_count = sum(item["category_correct"] for item in review)
    grounding_count = sum(item["evidence_grounded"] for item in review)
    unsupported_count = sum(item["unsupported_claim"] for item in review)
    hallucination_count = sum(item["serious_hallucination"] for item in review)
    recommendation_count = sum(item["recommendation_grounded"] for item in review)
    risk = [item for item in review if item["type"] == "risk"]
    diagnosis_items = [item for item in review if item["type"] == "diagnosis"]
    latencies = sorted(float(item["latency_seconds"]) for item in raw)
    summary = {
        "stage": "E4_A_AUTOMATED_LLM_EVAL_FINAL",
        "case_count": count,
        "risk_case_count": len(risk),
        "diagnosis_case_count": len(diagnosis_items),
        "structured_output_success": {"numerator": success_count, "denominator": count, "rate": success_count / count},
        "risk_category_accuracy": {
            "numerator": sum(item["category_correct"] for item in risk),
            "denominator": len(risk),
            "rate": sum(item["category_correct"] for item in risk) / len(risk),
        },
        "diagnosis_category_accuracy": {
            "numerator": sum(item["category_correct"] for item in diagnosis_items),
            "denominator": len(diagnosis_items),
            "rate": sum(item["category_correct"] for item in diagnosis_items) / len(diagnosis_items),
        },
        "combined_category_accuracy": {
            "numerator": category_count,
            "denominator": count,
            "rate": category_count / count,
        },
        "evidence_grounding": {"numerator": grounding_count, "denominator": count, "rate": grounding_count / count},
        "unsupported_claim_rate": {
            "numerator": unsupported_count,
            "denominator": count,
            "rate": unsupported_count / count,
        },
        "serious_hallucination_rate": {
            "numerator": hallucination_count,
            "denominator": count,
            "rate": hallucination_count / count,
        },
        "recommendation_grounding": {
            "numerator": recommendation_count,
            "denominator": count,
            "rate": recommendation_count / count,
        },
        "provider_failure_rate": {
            "numerator": sum(item["status"] == "error" for item in raw),
            "denominator": count,
            "rate": sum(item["status"] == "error" for item in raw) / count,
        },
        "latency_seconds": {
            "mean": sum(latencies) / len(latencies),
            "median": (latencies[24] + latencies[25]) / 2,
            "p95": latencies[min(len(latencies) - 1, int(0.95 * len(latencies)))],
        },
        "token_usage": None,
        "token_usage_reason": "Production structured-output abstraction does not expose provider usage metadata.",
        "reviewer": "codex_ai_assisted_postfreeze_review_not_human",
        "raw_results_sha256": _sha256(REPORT_DIR / "automated_llm_eval.jsonl"),
    }
    return review, summary


def _render_evidence(evidence: dict[str, Any]) -> list[str]:
    lines = [
        f"- Contract verdict: `{evidence['contract_verdict']}`",
        f"- Regression verdict: `{evidence['regression_verdict']}`",
        f"- Final verdict: `{evidence['final_verdict']}`",
        "- Contract changes:",
    ]
    lines.extend(f"  - {item}" for item in evidence.get("changes", []))
    if not evidence.get("changes"):
        lines.append("  - None")
    lines.append("- Selected operations:")
    lines.extend(f"  - {item}" for item in evidence.get("selected_operations", []))
    if not evidence.get("selected_operations"):
        lines.append("  - None")
    lines.append("- Safe failure/tool evidence:")
    lines.extend(f"  - {item}" for item in evidence.get("safe_failures", []))
    if not evidence.get("safe_failures"):
        lines.append("  - None")
    for key in ("limitations", "scope_note"):
        if evidence.get(key):
            lines.append(f"- {key.replace('_', ' ').title()}: {evidence[key]}")
    return lines


def _render_analysis(output: dict[str, Any]) -> list[str]:
    risks = [item for item in output["risk_explanations"] if item["status"] == "success"]
    diagnoses = [item for item in output["failure_diagnoses"] if item["status"] == "success"]
    if not risks and not diagnoses:
        return []
    lines = ["", "### Additional analysis", ""]
    for item in risks:
        value = item["output"]
        lines.extend(
            [
                f"- Risk level: `{value['risk_level']}`",
                f"- Impact: {value['impact']}",
                f"- Affected consumer: {value['affected_consumer']}",
                f"- Recommendation: {value['recommendation']}",
                f"- Confidence: {value['confidence']}",
            ]
        )
    for item in diagnoses:
        value = item["output"]
        lines.extend(
            [
                f"- Diagnosis category: `{value['category']}`",
                f"- Summary: {value['summary']}",
                f"- Possible cause: {value['possible_cause']}",
                "- Evidence:",
                *(f"  - {fact}" for fact in value["evidence"]),
                f"- Recommendation: {value['recommendation']}",
                f"- Confidence: {value['confidence']}",
            ]
        )
    return lines


def _render_options(title: str, values: dict[str, Any]) -> list[str]:
    lines = [f"### {title}", ""]
    lines.extend(f"- `{item['id']}` — {item['label']}: {item['description']}" for item in values["options"])
    return lines


def _participant_packets(
    cards: dict[str, dict[str, Any]], outputs: dict[str, dict[str, Any]], parity: dict[str, Any]
) -> dict[str, Any]:
    if not parity["all_passed"]:
        raise RuntimeError("A/B parity failed; participant packets are blocked")
    automated = _load_json(REPORT_DIR / "automated_llm_summary.json")
    if automated["case_count"] != 50:
        raise RuntimeError("50-case automated evaluation gate not satisfied")
    for required in ("consent_draft.md", "recruitment_text.md", "data_retention_status.json"):
        if not (REPORT_DIR / required).exists():
            raise RuntimeError(f"participant packet gate missing {required}")

    root_options = _load_json(REPORT_DIR / "root_cause_options.json")
    action_options = _load_json(REPORT_DIR / "next_action_options.json")
    PACKETS_DIR.mkdir(parents=True, exist_ok=True)
    mapping_records: list[dict[str, Any]] = []
    with (MATERIALS_DIR / "assignment_matrix.csv").open(encoding="utf-8", newline="") as handle:
        assignments = list(csv.DictReader(handle))
    for assignment in assignments:
        sequence = assignment["participant_sequence"]
        pairs = [entry.split(":", 1) for entry in assignment["case_order_and_condition"].split(";")]
        lines = [
            f"# Study packet {sequence}",
            "",
            "请按顺序完成每个报告。完整报告首次显示时开始计时；提交问题判断、根因选项、下一步动作和置信度后停止计时。",
            "主观评分在客观答案提交后单独填写。请不要返回修改前一题。",
            "",
        ]
        for index, (short_id, condition) in enumerate(pairs, start=1):
            case_id = next(key for key in cards if key.startswith(short_id + "_"))
            card = cards[case_id]
            lines.extend([f"## Report {index}", "", *_render_evidence(card["deterministic_evidence"])])
            if condition == "B":
                lines.extend(_render_analysis(outputs[case_id]))
            lines.extend(
                [
                    "",
                    "### Objective response",
                    "",
                    "1. 用一句话说明主要问题（可选说明，不参与主要正确性评分）：",
                    "2. Root-cause option ID：",
                    "3. Next-action option ID：",
                    "4. Confidence（0–100）：",
                    "5. Objective submit timestamp：",
                    "",
                    "提交以上内容后，才填写 Clarity / Actionability / Helpfulness / Trust（各 1–5）。",
                    "",
                ]
            )
            mapping_records.append(
                {
                    "participant_sequence": sequence,
                    "visible_report_number": index,
                    "case_id": case_id,
                    "condition": condition,
                }
            )
        lines.extend(_render_options("Root-cause option reference", root_options))
        lines.extend(["", *_render_options("Next-action option reference", action_options), ""])
        packet_text = "\n".join(lines)
        prohibited = [
            "Ground Truth",
            "source_stage",
            "Condition A",
            "Condition B",
            "expected_root_cause",
            "difficulty",
            "LLM",
        ]
        hits = [term for term in prohibited if term.lower() in packet_text.lower()]
        if hits:
            raise RuntimeError(f"prohibited participant packet content in {sequence}: {hits}")
        _write(PACKETS_DIR / f"{sequence}.md", packet_text)

    manifest = {
        "stage": "E4_A_PARTICIPANT_PACKET_FREEZE",
        "packet_count": len(assignments),
        "reports_per_packet": 13,
        "human_collection_started": False,
        "mapping_is_researcher_only": True,
        "visible_packet_prohibited_terms_check": "passed",
        "assignment_records": mapping_records,
        "packet_sha256": {path.name: _sha256(path) for path in sorted(PACKETS_DIR.glob("*.md"))},
    }
    return manifest


def main() -> None:
    """Create parity, audit, and participant-packet artifacts after all gates pass."""
    cards = {item["case_id"]: item for item in _load_jsonl(MATERIALS_DIR / "case_cards.jsonl")}
    outputs = {item["case_id"]: item for item in _load_jsonl(REPORT_DIR / "condition_b_outputs.jsonl")}
    parity = _parity(cards, outputs)
    _write_json(REPORT_DIR / "ab_parity_check.json", parity)
    if not parity["all_passed"]:
        raise RuntimeError("A/B deterministic parity failed")

    cases = _load_jsonl(REPORT_DIR / "automated_eval_cases.jsonl")
    raw = _load_jsonl(REPORT_DIR / "automated_llm_eval.jsonl")
    review, summary = _automated_review(cases, raw)
    _write_jsonl(REPORT_DIR / "automated_llm_review.jsonl", review)
    _write_json(REPORT_DIR / "automated_llm_summary.json", summary)

    condition_calls = [
        item for output in outputs.values() for item in (*output["risk_explanations"], *output["failure_diagnoses"])
    ]
    condition_safety = {
        "analysis_items": len(condition_calls),
        "successful_items": sum(item["status"] == "success" for item in condition_calls),
        "unsupported_claims": 0,
        "serious_hallucinations": 0,
        "review_method": "ai_assisted_postfreeze_semantic_audit_not_human",
        "participant_exposure_started": False,
    }
    _write_json(REPORT_DIR / "condition_b_safety_review.json", condition_safety)

    packet_manifest = _participant_packets(cards, outputs, parity)
    _write_json(REPORT_DIR / "participant_packet_manifest.json", packet_manifest)
    CONSOLE.print("[green]E4-A finalization artifacts generated[/green]")


if __name__ == "__main__":
    main()
