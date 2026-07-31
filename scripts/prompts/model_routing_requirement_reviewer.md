# 독립 Requirement Judge 검토 프롬프트

아래 `CASES_JSON`을 읽고 각 요청이 요구하는 능력만 0~3으로 판정한다. 후보 모델, 가격, 이전 Judge 결과, 다른 검토자의 결과는 보지 않는다.

- `task_complexity`: 0 단순 전달·추출, 1 한 단계 변환·분류, 2 여러 조건 비교·다단계 처리, 3 복합 전문 추론·충돌 해결
- `decision_impact`: 0 외부 영향 없는 내부 참고, 1 상태 변경 없는 일반 안내, 2 금전·권한·보상 또는 사용자 행동에 영향을 주는 판단, 3 법무·보안·개인정보·대규모 장애 또는 되돌리기 어려운 실행
- `evidence_synthesis`: 0 근거 불필요, 1 단일 사실 확인, 2 여러 근거 결합·비교, 3 충돌 근거 해석과 결론

같은 주제라도 설명 요청과 승인·실행 요청을 다르게 판단한다. `customerTier`, `outputMode` 같은 업무 속성만으로 점수를 올리지 않는다. `structural_facts`와 `rag_context`는 서버가 계산한 사실이므로 신뢰한다.

Markdown 없이 아래 JSON만 반환한다.

```json
{
  "rubric_version": "routing-requirements-v4",
  "reviewer_id": "independent-agent",
  "labels": [
    {
      "case_id": "case-id",
      "task_complexity": 0,
      "decision_impact": 0,
      "evidence_synthesis": 0,
      "confidence": 0.0,
      "ambiguity_flags": [],
      "reason_codes": [],
      "rationale": "관찰 가능한 요청 행동과 구조 사실에 근거한 짧은 설명"
    }
  ]
}
```

`ambiguity_flags`에는 `boundary_score`, `conflicting_evidence`, `high_impact_uncertainty`, `insufficient_context`, `novel_request` 중 필요한 값만 넣는다. `reason_codes`에는 `high_decision_impact`, `security_or_compliance_risk`, `multi_step_reasoning`, `evidence_conflict`, `broad_context_synthesis`, `long_context_handling` 중 최대 세 개만 넣는다.

CASES_JSON:

```json
{{CASES_JSON}}
```
