"""현재 자동 모델 라우팅과 고가 모델 고정을 30개 blind 요청으로 비교한다."""

from __future__ import annotations

import argparse
import faulthandler
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from scripts import experiment_judge_first_economics_80 as experiment  # noqa: E402


EXTRA_HOLDOUT_CASES: tuple[tuple[str, str, str, str], ...] = (
    (
        "holdout_builder_help",
        "economy",
        "business",
        "워크플로우를 복제한 뒤 새 이름으로 저장하는 메뉴 위치를 알려 주세요.",
    ),
    (
        "holdout_cost_help",
        "economy",
        "business",
        "최근 테스트 한 건의 입력 토큰과 출력 토큰을 각각 확인하는 방법이 궁금합니다.",
    ),
    (
        "holdout_knowledge_help",
        "economy",
        "business",
        "지식 문서가 어느 지식 베이스에 들어 있는지 확인하는 화면을 알려 주세요.",
    ),
    (
        "holdout_customer_operations",
        "balanced",
        "enterprise",
        "한 고객 문의가 두 담당 팀에 동시에 배정됐습니다. 처리 기록을 잃지 않고 담당을 정리하는 절차를 제안해 주세요.",
    ),
    (
        "holdout_release_operations",
        "balanced",
        "business",
        "새 배포 이후 일부 사용자만 이전 응답을 봅니다. 배포 버전과 캐시를 점검할 순서를 정리해 주세요.",
    ),
    (
        "holdout_usage_reconciliation",
        "balanced",
        "business",
        "워크플로우 실행 이력의 비용 합계와 월간 비용 화면의 금액이 다릅니다. 원인을 좁힐 자료를 정리해 주세요.",
    ),
    (
        "holdout_payment_incident",
        "advanced",
        "enterprise",
        "결제 승인 요청이 중복 실행돼 여러 고객에게 이중 청구 가능성이 있습니다. 즉시 중단과 복구 순서를 제시해 주세요.",
    ),
    (
        "holdout_privacy_incident",
        "advanced",
        "enterprise",
        "권한이 없는 공유 주소에서 고객 신분증 파일이 열립니다. 노출 차단과 영향 조사, 통지 판단에 필요한 사실을 정리해 주세요.",
    ),
    (
        "holdout_legal_retention",
        "advanced",
        "enterprise",
        "고객 삭제 요청과 진행 중인 법적 보존 명령이 충돌합니다. 자동 처리를 멈출 범위와 승인 주체를 제안해 주세요.",
    ),
    (
        "holdout_multi_region_incident",
        "advanced",
        "enterprise",
        "여러 리전에서 인증 실패와 관리자 권한 변경이 동시에 증가했습니다. 장애와 보안 사고 가능성을 나눠 조사해 주세요.",
    ),
)


def build_auto_vs_high_cases() -> list[experiment.ExperimentCase]:
    cases = [
        *experiment.build_benchmark_cases(),
        *experiment._cases_from_specs(
            EXTRA_HOLDOUT_CASES,
            id_prefix="auto-high-holdout",
        ),
    ]
    if len(cases) != 30:
        raise AssertionError(f"expected 30 auto-vs-high cases, got {len(cases)}")
    return cases


def build_auto_vs_high_plan() -> experiment.ExperimentPhasePlan:
    return experiment.ExperimentPhasePlan(
        phase=experiment.BENCHMARK_PHASE,
        cases=tuple(build_auto_vs_high_cases()),
        arms=(experiment.AUTO_ARM, experiment.HIGH_ARM),
        evaluate_quality=True,
        reset_learning=False,
        # 현재 구현의 초기 Judge-first 라우팅도 검증 대상이므로 발행된 로컬
        # learner version을 실험 시작 조건으로 요구하지 않는다.
        requires_ready_learner=False,
        use_policy_preview=False,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="자동 모델 라우팅과 고가 모델 고정을 동일한 30개 요청으로 비교"
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="실제 provider 호출과 DB 실행 로그 기록을 수행합니다.",
    )
    parser.add_argument(
        "--run-id",
        default="2026-07-22__auto-vs-high-30__min-price-selector",
    )
    parser.add_argument(
        "--quality-judge-model",
        default="gpt-5.4",
        help="두 arm의 익명 출력 품질만 평가할 모델입니다.",
    )
    parser.add_argument(
        "--defer-quality",
        action="store_true",
        help="workflow 실행을 먼저 끝내고 품질 평가는 별도 후처리합니다.",
    )
    parser.add_argument("--count", type=int, default=30)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument(
        "--debug-hangs",
        action="store_true",
        help="60초마다 현재 Python stack을 출력합니다.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    experiment.QUALITY_JUDGE_MODEL = str(args.quality_judge_model).strip()
    plan = build_auto_vs_high_plan()
    cases = list(plan.cases)
    if args.count < 1 or args.offset < 0 or args.offset + args.count > len(cases):
        raise SystemExit("--offset/--count 범위는 0~30 안이어야 합니다.")
    selected_cases = cases[args.offset : args.offset + args.count]
    experiment.ARMS = plan.arms
    execution_plan = experiment.ExperimentPhasePlan(
        phase=plan.phase,
        cases=tuple(selected_cases),
        arms=plan.arms,
        evaluate_quality=plan.evaluate_quality and not args.defer_quality,
        reset_learning=plan.reset_learning,
        requires_ready_learner=plan.requires_ready_learner,
        use_policy_preview=plan.use_policy_preview,
    )

    run_root = experiment.EXPERIMENT_RUNS_ROOT / str(args.run_id).strip()
    output_dir = run_root / experiment.BENCHMARK_PHASE
    if not args.execute:
        print(
            json.dumps(
                {
                    "dry_run": True,
                    "run_id": args.run_id,
                    "arms": list(plan.arms),
                    "case_count": len(selected_cases),
                    "difficulty": {
                        difficulty: sum(
                            1
                            for case in selected_cases
                            if case.expected_difficulty == difficulty
                        )
                        for difficulty in ("economy", "balanced", "advanced")
                    },
                    "message": "실제 호출은 --execute를 붙여야 시작합니다.",
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return

    # 외부 provider 또는 gevent 종료 구간이 비정상적으로 오래 멈추면 현재 Python
    # stack을 출력해 실험을 무작정 기다리지 않고 정확한 병목을 찾을 수 있게 한다.
    if args.debug_hangs:
        faulthandler.dump_traceback_later(60, repeat=True)

    run_root.mkdir(parents=True, exist_ok=True)
    (run_root / "run-config.json").write_text(
        json.dumps(
            {
                "run_id": args.run_id,
                "purpose": "automatic routing vs fixed high-cost model",
                "arms": list(plan.arms),
                "automatic_selector": "current product implementation",
                "high_fixed_model": experiment.HIGH_MODEL,
                "routing_judge_model": experiment.ROUTING_JUDGE_MODEL,
                "quality_judge_model": experiment.QUALITY_JUDGE_MODEL,
                "case_count": len(selected_cases),
                "quality_evaluation": (
                    "deferred" if args.defer_quality else "inline"
                ),
                "quality_judge_cost_is_product_cost": False,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    experiment.run_experiment(
        selected_cases,
        output_dir,
        phase_plan=execution_plan,
        resume=args.resume,
        batch_offset=args.offset,
        report_name=None,
    )
    if args.debug_hangs:
        faulthandler.cancel_dump_traceback_later()


if __name__ == "__main__":
    main()
