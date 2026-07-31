"""Judge-first RAG workflow의 50건 공정 비교 실험 준비 도구.

이 파일은 기본적으로 외부 API, DB, WorkflowEngine을 호출하지 않는다. ``--prepare``는
50개 synthetic 입력과 세 비교 arm의 실행 순서만 만들고, ``--build-blind-package``는
실행 결과에서 모델명·비용·arm 이름을 제거한 품질 Judge 입력을 만든다.

실제 실행기는 재빌드가 끝난 뒤 같은 schedule 파일을 읽어 별도 단계로 연결한다.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import random
from dataclasses import asdict, dataclass
from typing import Any, Iterable


ROOT = pathlib.Path(__file__).resolve().parents[1]
DATASET_PATH = ROOT / "tests" / "fixtures" / "model_routing" / "judge_first_rag_50_cases.json"
DEFAULT_OUTPUT_DIR = ROOT / "reports" / "model-routing" / "runs" / "judge-first" / "rag-50-blind-prepared"
ARMS = ("automatic", "high_fixed", "mid_fixed", "low_fixed")


@dataclass(frozen=True)
class ExperimentCase:
    case_id: str
    category: str
    expected_difficulty: str
    customer_tier: str
    message: str
    requires_grounding: bool
    expected_output_focus: str


def load_cases(path: pathlib.Path = DATASET_PATH) -> list[ExperimentCase]:
    """버전 관리되는 synthetic 입력셋을 읽고 실행 전 계약을 검사한다."""

    rows = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(rows, list) or len(rows) != 50:
        raise ValueError("실험 데이터셋은 정확히 50개 요청이어야 합니다.")
    cases = [ExperimentCase(**row) for row in rows]
    if len({case.case_id for case in cases}) != len(cases):
        raise ValueError("case_id는 중복될 수 없습니다.")
    if len({case.message for case in cases}) != len(cases):
        raise ValueError("동일한 요청 문장은 한 번만 포함해야 합니다.")
    if {case.expected_difficulty for case in cases} != {"economy", "balanced", "advanced"}:
        raise ValueError("난이도 참조값은 economy, balanced, advanced를 모두 포함해야 합니다.")
    if sum(case.requires_grounding for case in cases) < 12:
        raise ValueError("RAG 근거가 필요한 요청은 최소 12개 포함해야 합니다.")
    return cases


def build_schedule(cases: Iterable[ExperimentCase], *, seed: int = 20260718) -> list[dict[str, Any]]:
    """각 요청을 네 arm에 한 번씩 배정하고 arm 순서는 case별로 무작위화한다.

    네 arm이 항상 같은 순서로 실행되면 시간대별 provider 상태가 특정 arm에만 유리할 수
    있다. 각 case 안에서 arm 순서를 섞어 이 영향을 줄인다.
    """

    randomizer = random.Random(seed)
    schedule: list[dict[str, Any]] = []
    for case_index, case in enumerate(list(cases), start=1):
        ordered_arms = list(ARMS)
        randomizer.shuffle(ordered_arms)
        for arm in ordered_arms:
            schedule.append(
                {
                    "case_id": case.case_id,
                    "arm": arm,
                    "payload": {
                        "customerTier": case.customer_tier,
                        "message": case.message,
                    },
                    "report_checkpoint_after": case_index % 10 == 0 and arm == ordered_arms[-1],
                    "completed_case_count": case_index,
                    "completed_execution_count": case_index * len(ARMS),
                }
            )
    return schedule


def build_blind_quality_packets(
    cases: Iterable[ExperimentCase],
    execution_rows: Iterable[dict[str, Any]],
    *,
    seed: int = 20260718,
) -> tuple[list[dict[str, Any]], dict[str, dict[str, str]]]:
    """실행 결과를 모델·arm을 모르는 품질 Judge용 packet으로 바꾼다.

    반환값의 첫 항목만 외부 품질 Judge에 보낸다. 두 번째 mapping은 결과를 원래 arm에
    되돌리기 위한 비공개 파일용이며, Judge request에는 절대 포함하지 않는다.
    """

    by_case = {str(row["case_id"]): row for row in execution_rows}
    randomizer = random.Random(seed)
    packets: list[dict[str, Any]] = []
    mapping: dict[str, dict[str, str]] = {}
    for case in cases:
        row = by_case.get(case.case_id)
        if row is None:
            raise ValueError(f"실행 결과에 {case.case_id}가 없습니다.")
        variants = row.get("variants")
        if not isinstance(variants, list) or {item.get("arm") for item in variants} != set(ARMS):
            raise ValueError(f"{case.case_id}에는 네 arm 결과가 각각 하나씩 필요합니다.")
        shuffled = list(variants)
        randomizer.shuffle(shuffled)
        anonymous_outputs: dict[str, str] = {}
        case_mapping: dict[str, str] = {}
        for index, variant in enumerate(shuffled, start=1):
            anonymous_id = f"response_{index}"
            output = str(variant.get("output") or "")
            anonymous_outputs[anonymous_id] = output
            case_mapping[anonymous_id] = str(variant["arm"])
        packets.append(
            {
                "case_id": case.case_id,
                "request": {
                    "customerTier": case.customer_tier,
                    "message": case.message,
                    "requiresGrounding": case.requires_grounding,
                },
                "expected_output_focus": case.expected_output_focus,
                "required_contract": {
                    "format": "JSON object",
                    "rules": [
                        "요청에 맞는 분류와 우선순위를 제시한다.",
                        "근거가 필요한 요청은 제공된 문서 근거 범위를 벗어나 확정하지 않는다.",
                        "고위험 요청은 확인 전 확정 약속을 하지 않는다.",
                    ],
                },
                "anonymous_outputs": anonymous_outputs,
                "response_schema": {
                    key: {
                        "quality_score": "0..100",
                        "contract_pass": "boolean",
                        "reason": "20 Korean characters or fewer",
                    }
                    for key in anonymous_outputs
                },
            }
        )
        mapping[case.case_id] = case_mapping
    return packets, mapping


def write_preparation_bundle(output_dir: pathlib.Path, *, seed: int) -> dict[str, str]:
    """실험 전 사람이 확인할 데이터셋 사본과 200회 실행 schedule을 저장한다."""

    cases = load_cases()
    schedule = build_schedule(cases, seed=seed)
    checkpoints = [
        {
            "completed_case_count": count,
            "completed_execution_count": count * len(ARMS),
            "report_file": f"checkpoints/report-after-{count:02d}-cases.md",
            "blind_quality_packet_file": f"checkpoints/blind-quality-after-{count:02d}-cases.json",
        }
        for count in range(10, len(cases) + 1, 10)
    ]
    output_dir.mkdir(parents=True, exist_ok=True)
    dataset_path = output_dir / "dataset.json"
    schedule_path = output_dir / "execution-schedule.json"
    dataset_path.write_text(json.dumps([asdict(case) for case in cases], ensure_ascii=False, indent=2), encoding="utf-8")
    schedule_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "seed": seed,
                "case_count": len(cases),
                "arm_count": len(ARMS),
                "total_execution_count": len(schedule),
                "arms": list(ARMS),
                "report_checkpoints": checkpoints,
                "schedule": schedule,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return {"dataset": str(dataset_path), "schedule": str(schedule_path)}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Judge-first RAG 50회 blind quality 실험 준비")
    parser.add_argument("--prepare", action="store_true", help="데이터셋과 200회 실행 schedule만 생성합니다.")
    parser.add_argument(
        "--build-blind-package",
        default=None,
        metavar="EXECUTION_RESULTS_JSON",
        help="네 arm의 실행 결과 JSON을 익명 품질 Judge packet으로 변환합니다.",
    )
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--seed", type=int, default=20260718)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    output_dir = pathlib.Path(args.output_dir)
    if args.prepare:
        paths = write_preparation_bundle(output_dir, seed=args.seed)
        print(json.dumps({"mode": "prepare", **paths}, ensure_ascii=False))
        return
    if args.build_blind_package:
        source_path = pathlib.Path(args.build_blind_package)
        payload = json.loads(source_path.read_text(encoding="utf-8"))
        execution_rows = payload.get("cases") if isinstance(payload, dict) else payload
        if not isinstance(execution_rows, list):
            raise SystemExit("실행 결과 JSON은 cases 배열 또는 배열 자체여야 합니다.")
        packets, mapping = build_blind_quality_packets(
            load_cases(), execution_rows, seed=args.seed
        )
        output_dir.mkdir(parents=True, exist_ok=True)
        packet_path = output_dir / "blind-quality-packets.json"
        mapping_path = output_dir / "blind-quality-mapping.private.json"
        packet_path.write_text(json.dumps(packets, ensure_ascii=False, indent=2), encoding="utf-8")
        mapping_path.write_text(json.dumps(mapping, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"mode": "blind_package", "packets": str(packet_path), "private_mapping": str(mapping_path)}, ensure_ascii=False))
        return
    raise SystemExit("--prepare 또는 --build-blind-package를 지정하세요. 이 도구는 provider를 호출하지 않습니다.")


if __name__ == "__main__":
    main()
