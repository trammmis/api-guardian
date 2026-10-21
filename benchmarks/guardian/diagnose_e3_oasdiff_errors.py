"""对 E3-B 主运行中的 oasdiff ERROR 做隔离诊断重放."""

import argparse
import asyncio
import hashlib
import json
import re
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from rich.console import Console

from app.services.guardian.subprocess import ControlledSubprocessRunner
from benchmarks.guardian.e3_system_evaluation import (
    MAX_ARTIFACT_BYTES,
    MAX_SCAN_ARTIFACT_BYTES,
    fetch_spec,
    load_jsonl,
    safe_suffix,
)
from benchmarks.guardian.real_world_models import PreSystemReviewRecord

REPORT_ROOT = Path("benchmarks/guardian/reports/e3")
REVIEWS_PATH = REPORT_ROOT / "review_records_v2.jsonl"
RAW_RESULTS_PATH = REPORT_ROOT / "raw_guardian_results.jsonl"
OUTPUT_PATH = REPORT_ROOT / "oasdiff_error_diagnostics.jsonl"


def sha256_bytes(value: bytes) -> str:
    """计算诊断 artifact 哈希."""
    return hashlib.sha256(value).hexdigest()


def safe_text(value: bytes, base_path: Path, head_path: Path) -> str:
    """仅保留诊断所需的脱敏文本，移除本地临时绝对路径."""
    text = value.decode("utf-8", errors="replace")
    text = text.replace(str(base_path), "<BASE_SPEC>")
    text = text.replace(str(head_path), "<HEAD_SPEC>")
    text = re.sub(
        r"failed to load (base|revision) spec from .*?: bad data",
        r"failed to load \1 spec: bad data",
        text,
    )
    return text[:4000]


async def diagnose_case(
    record: PreSystemReviewRecord,
    executable: Path,
    runner: ControlledSubprocessRunner,
) -> dict[str, Any]:
    """以相同 CLI 契约重放一个 ERROR Case，不参与 headline metrics."""
    base_data = fetch_spec(record, "base")
    head_data = fetch_spec(record, "head")
    with tempfile.TemporaryDirectory(prefix="guardian-e3-diagnostic-") as temp_value:
        temp_root = Path(temp_value)
        base_path = temp_root / f"base{safe_suffix(record.base_spec_path)}"
        head_path = temp_root / f"head{safe_suffix(record.head_spec_path)}"
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
        result = await runner.run(
            command,
            timeout_seconds=30,
            max_stdout_bytes=MAX_ARTIFACT_BYTES,
            max_stderr_bytes=MAX_ARTIFACT_BYTES,
            max_total_bytes=MAX_SCAN_ARTIFACT_BYTES - len(base_data) - len(head_data),
        )
        stdout_valid_json = False
        stdout_shape: str | None = None
        try:
            parsed = json.loads(result.stdout.decode("utf-8"))
            stdout_valid_json = True
            stdout_shape = type(parsed).__name__
        except (UnicodeDecodeError, json.JSONDecodeError):
            pass
        return {
            "case_id": record.case_id,
            "diagnostic_kind": "non_headline_oasdiff_error_replay",
            "diagnosed_at": datetime.now(UTC).isoformat(),
            "command_contract": (
                "oasdiff breaking --format json --allow-external-refs=false --fail-on ERR -- BASE HEAD"
            ),
            "exit_code": result.exit_code,
            "stdout_bytes": len(result.stdout),
            "stdout_sha256": sha256_bytes(result.stdout),
            "stdout_valid_json": stdout_valid_json,
            "stdout_shape": stdout_shape,
            "stderr_bytes": len(result.stderr),
            "stderr_sha256": sha256_bytes(result.stderr),
            "safe_stderr_excerpt": safe_text(result.stderr, base_path, head_path),
            "primary_raw_result_unchanged": True,
            "included_in_headline_metrics": False,
        }


async def run(executable: Path) -> None:
    """诊断主运行中所有 oasdiff_execution_failed Case."""
    reviews = {
        record.case_id: record
        for record in (PreSystemReviewRecord.model_validate(value) for value in load_jsonl(REVIEWS_PATH))
    }
    errors = [value for value in load_jsonl(RAW_RESULTS_PATH) if value.get("error_code") == "oasdiff_execution_failed"]
    runner = ControlledSubprocessRunner()
    records = [await diagnose_case(reviews[value["case_id"]], executable, runner) for value in errors]
    OUTPUT_PATH.write_text(
        "".join(json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n" for value in records),
        encoding="utf-8",
    )
    Console().print(f"[green]Saved {len(records)} diagnostic records[/green] to {OUTPUT_PATH}")


def parser() -> argparse.ArgumentParser:
    """构造诊断命令参数."""
    value = argparse.ArgumentParser(description="Diagnose frozen E3-B oasdiff errors")
    value.add_argument("--oasdiff-path", type=Path)
    value.add_argument("--sanitize-existing", action="store_true")
    return value


def sanitize_existing() -> None:
    """清除既有诊断记录中可能出现的临时绝对路径."""
    values = load_jsonl(OUTPUT_PATH)
    for value in values:
        excerpt = str(value.get("safe_stderr_excerpt") or "")
        value["safe_stderr_excerpt"] = re.sub(
            r"failed to load (base|revision) spec from .*?: bad data",
            r"failed to load \1 spec: bad data",
            excerpt,
        )
    OUTPUT_PATH.write_text(
        "".join(json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n" for value in values),
        encoding="utf-8",
    )


def main() -> None:
    """命令行入口."""
    args = parser().parse_args()
    if args.sanitize_existing:
        sanitize_existing()
        Console().print(f"[green]Sanitized existing diagnostics[/green] at {OUTPUT_PATH}")
        return
    if args.oasdiff_path is None:
        parser().error("--oasdiff-path is required unless --sanitize-existing is used")
    asyncio.run(run(args.oasdiff_path.resolve()))


if __name__ == "__main__":
    main()
