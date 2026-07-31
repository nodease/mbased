# Judge-first 무RAG 80건 블라인드 경제성 실험 계획

Status: Draft

## 1. 실험 목적

이 실험은 RAG를 사용하지 않는 하나의 범용 LLM workflow에서 자동 모델 라우팅이 다음 목표를 동시에 달성하는지 확인한다.

1. 고성능 모델 고정 실행과 비교해 비용을 의미 있게 줄인다.
2. 저비용 모델 고정 실행보다 결과 품질을 의미 있게 높인다.
3. 중간급 모델 고정 실행과 비교해 품질·비용·속도 중 적어도 하나에서 분명한 이점을 만든다.
4. 요청을 단순히 저·중·고 난이도 세 단계로 나누지 않고, 요청에 필요한 능력에 따라 전체 후보 모델을 세밀하게 선택한다.
5. 자동 라우팅에 사용되는 Judge 비용과 지연 시간을 포함해도 전체 trade-off가 합리적이다.

이번 실험은 같은 80개 요청을 네 실행 방식에 모두 적용한다. 총 제품 실행 수는 `80 요청 x 4 Arm = 320회`다. 품질 평가는 요청마다 네 출력을 한 번에 비교하므로 기본 80회이며, 위치 편향 감사 표본은 별도 평가 비용으로 집계한다.

## 2. 이번 실험에서 검증하지 않는 것

- Knowledge Base 검색 품질
- RAG chunk, retrieval, source metadata 품질
- 특정 공급자 전체 모델의 절대적인 벤치마크 순위
- 80개 표본만으로 장기 운영 성능을 확정하는 것
- 모델을 많이 선택했다는 사실 자체를 성공으로 간주하는 것

모델 다양성은 목표를 위해 강제로 만들지 않는다. 요청의 요구 능력이 다른데도 특정 모델 하나로 과도하게 수렴하는지를 진단하는 지표로 사용한다.

## 3. 비교 Arm

| 내부 Arm | 실행 방식 | 실험에서의 역할 |
| --- | --- | --- |
| `high_fixed` | 고성능 범용 모델 고정 | 품질 상한선과 비용 상한선 |
| `mid_fixed` | 중간급 범용 모델 고정 | 현실적인 단일 모델 기준선 |
| `low_fixed` | 저비용 범용 모델 고정 | 비용·속도 하한선과 품질 위험 기준선 |
| `automatic` | Nodease 자동 모델 라우팅 | 품질을 지키면서 비용·속도를 최적화할 대상 |

실행 전 preflight에서 현재 execution subject가 사용할 수 있는 모델만 확인한 뒤 모델 ID를 고정한다. 권장 출발점은 다음과 같지만 실제 credential과 카탈로그 상태를 확인하지 않고 확정하지 않는다.

| 역할 | 권장 후보 |
| --- | --- |
| 고성능 고정 | `gpt-5.6-sol` |
| 중간 고정 | `gpt-5.4-mini` 또는 `gpt-5.6-terra` |
| 저비용 고정 | `gpt-4o-mini` 또는 `gpt-5.6-luna` |
| 자동 라우팅 후보 | 실행 주체가 사용할 수 있는 workflow chat model 전체. 별칭은 canonical ID로 중복 제거 |

`o3`는 단순히 가장 비싼 고정 Arm으로 취급하지 않는다. 형식적 논증, 수학·과학·알고리즘과 같이 전문 추론이 핵심인 요청에서 자동 Arm이 선택할 수 있는 전문 후보로 둔다.

## 4. 공정 비교 원칙

### 4.1 동일 조건

네 Arm은 모델 선택 방식 외에 다음 조건이 같아야 한다.

- 동일한 workflow graph와 LLM node ID
- 동일한 system, user, assistant prompt
- 동일한 입력 변수와 출력 계약
- 동일한 temperature와 최대 출력 길이
- 동일한 credential execution subject
- 동일한 retry와 timeout 정책
- 동일한 캐시 사용 조건

모델이 지원하지 않는 파라미터를 제거해야 한다면 제거된 파라미터와 fallback 발생 여부를 결과에 기록한다. 지원하지 않는 `reasoning.effort` 같은 값을 그대로 보내 발생한 실패를 모델 품질 실패로 오해하면 안 된다.

### 4.2 실행 순서

- 80개 요청의 순서는 고정 seed로 무작위화한다.
- 같은 요청의 Arm 실행 순서도 case ID 기반 고정 seed로 무작위화한다. 따라서 재개해도 순서는 같지만 `automatic → mid → high → low`로 고정되지 않는다.
- 자동 Arm의 학습 순서는 dataset 순서를 유지한다.
- fixed Arm 결과는 자동 라우터 학습 데이터에 포함하지 않는다.
- 재실행은 기존 결과를 덮어쓰지 않고 attempt 번호로 남긴다.

### 4.3 진단 모드 비활성화

이번 실험에서는 라우팅 진단 모드를 끈다.

- 운영용 Runtime Judge 출력 제한을 사용한다.
- 후보별 장문 비교 설명 생성을 요구하지 않는다.
- 실제 제품과 동일하게 선택 모델, 확신도, 짧은 이유, decision source, fallback만 trace에 남긴다.
- 진단을 위해 늘린 출력 토큰과 장문 추론 비용을 제품 비용에 섞지 않는다.

독립 품질 Judge는 런타임 라우팅 Judge와 다른 역할이다. 품질 평가는 네 Arm 실행이 모두 끝난 뒤 별도로 수행하고 `evaluation_cost`로 분리한다.

## 5. 실험 workflow

RAG 없이 다양한 기업 업무를 처리하는 LLM node 한 개를 중심으로 구성한다.

```text
Webhook 또는 Start
→ LLM: 범용 기업 업무 처리
→ Answer
```

입력 예시:

```json
{
  "request": "사용자 요청 원문",
  "context": "요청별로 주어지는 비RAG 참고 정보. 없을 수 있음",
  "constraints": ["반드시 지켜야 하는 조건"],
  "output_mode": "answer | checklist | json | analysis"
}
```

공통 출력 계약:

```json
{
  "answer": "최종 결과",
  "key_points": ["핵심 근거 또는 실행 항목"],
  "assumptions": ["입력에 없어서 가정한 내용"],
  "risk_flags": ["주의가 필요한 위험. 없으면 빈 배열"]
}
```

요청 payload와 prompt에는 예상 난이도, 기대 모델 ID, Arm 이름을 포함하지 않는다.

## 6. 80개 데이터셋 설계

8개 작업군을 10개씩 만든다. 각 작업군 안에서도 길이, 조건 수, 문체, 출력 형식, 모호성, 위험도를 다르게 구성한다.

| 작업군 | 건수 | 필요한 능력 | 사전 허용 모델군 가설 |
| --- | ---: | --- | --- |
| 단순 추출·분류 | 10 | 정형 처리, 정확한 필드 매핑, 짧은 지시 준수 | 저비용 범용 모델 중심 |
| 문장 교정·형식 변환 | 10 | 문체 제어, 의미 보존, 낮은 추론 요구 | 저비용 또는 경량 범용 모델 |
| 일반 안내·짧은 요약 | 10 | 간결한 설명, 기본 지시 준수 | 저비용 또는 중간급 범용 모델 |
| JSON 절차·체크리스트 | 10 | 구조화 출력, 여러 독립 조건 준수 | 경량 또는 중간급 비추론 모델 |
| 다중 조건 계획 | 10 | 조건 우선순위, 예외 처리, 실행 순서 | 중간급 또는 고성능 범용 모델 |
| 전문 보고·의사결정 지원 | 10 | 전문 문장, 정보 종합, 불확실성 표현 | 고성능 범용 모델 |
| 고위험 정책·보안 판단 | 10 | 보수적 판단, 충돌 조건, 안전한 표현 | 고성능 범용 또는 전문 추론 모델 |
| 수학·논리·코드 추론 | 10 | 다단계 추론, 제약 검증, 반례 확인 | `o3` 같은 추론 특화 또는 강한 범용 모델 |

### 6.1 반드시 포함할 경계 사례

- 한두 문장이지만 다단계 논증이 필요한 요청
- 입력은 길지만 단순 추출만 필요한 요청
- JSON 출력이지만 판단 자체는 쉬운 요청
- 출력은 짧지만 의사결정 영향이 큰 요청
- 조건이 많지만 서로 독립적인 요청
- 조건이 적어 보여도 서로 충돌하는 요청
- 모호해서 가정을 분리해야 하는 요청
- 코드가 길지만 단순한 이름 변경 요청
- 코드는 짧지만 동시성 또는 보안 결함을 찾는 요청

### 6.2 데이터 누수 방지

`expected_capabilities`, `acceptable_model_ids`, `underpowered_model_ids`, `overprovisioned_model_ids`는 실험 fixture의 평가 메타데이터로만 저장한다. workflow 입력과 Runtime Judge 입력에는 정답 정보를 넣지 않는다.

## 7. 세밀한 라우팅 가설

단순한 `economy / balanced / advanced` 일대일 대응을 정답으로 사용하지 않는다. 각 요청은 필요한 능력 벡터와 허용 모델 집합을 가진다.

요청 요구 능력 예시:

```json
{
  "reasoning": 0.8,
  "instruction_following": 0.6,
  "structured_output": 0.4,
  "long_context": 0.2,
  "professional_writing": 0.9,
  "coding": 0.0,
  "risk": 0.7,
  "latency_sensitivity": 0.3
}
```

모델 선택 평가는 다음 세 결과로 나눈다.

| 결과 | 의미 |
| --- | --- |
| `appropriate` | 요청 능력을 충족하는 사전 허용 후보군 안에서 선택 |
| `overprovisioned` | 필요한 수준보다 과도하게 비싸거나 느린 모델 선택 |
| `underpowered` | 요청 계약과 필요한 추론을 안정적으로 처리하기 어려운 모델 선택 |

하나의 요청에 정답 모델을 하나만 지정하지 않는다. 예를 들어 전문 보고서 요청은 `gpt-5.4`, `gpt-5.6-sol`처럼 복수의 고성능 범용 후보가 허용될 수 있지만, 형식적 제약 증명에서는 `o3`가 더 적합할 수 있다.

## 8. 자동 라우팅 단계와 학습

현재 제품 계약을 기준으로 실험 구간을 분리한다.

| 구간 | 자동 Arm 선택 방식 | 확인할 내용 |
| --- | --- | --- |
| 요청 1~50 | Runtime Judge 우선 | 초기 라우팅 품질과 Judge 비용·지연 |
| 요청 51~80 | 학습 정책 우선, 불확실하면 Judge | local takeover, 모델 고착, Judge 절감 효과 |

자동 Arm에서 성공한 실행 중 다음 계약을 통과한 결과만 학습에 사용한다.

- workflow 성공
- JSON Schema 통과
- 후속 출력 계약 통과
- provider 응답 완료
- fallback 또는 retry 발생 여부가 정확히 기록됨

10개 요청마다 다음 checkpoint를 저장한다.

- 누적 제품 비용
- 품질 평가는 완료된 동일 요청 네 Arm만 집계
- 선택 모델 분포
- Runtime Judge 호출 수와 비용
- local router 직접 선택 수
- fallback과 retry
- 계약 통과율
- 작업군별 과소·과잉 선택 수

## 9. 블라인드 품질 평가

각 요청의 네 출력을 모델명과 Arm 이름을 제거한 뒤 무작위 ID로 바꾼다. 품질 Judge 오류는 모델 품질 실패가 아니다. 해당 요청의 품질 평가만 최대 3회 재시도하며, 끝내 실패하면 실행 결과는 보존하고 품질 점수는 `평가 실패`로 별도 집계한다.

```text
response_A7
response_K2
response_M9
response_R4
```

품질 Judge가 받는 정보:

- 원래 요청
- 비RAG context와 constraints
- 기대 출력 계약
- 작업별 평가 rubric
- 이름과 순서가 무작위화된 네 출력

품질 Judge가 받지 않는 정보:

- 모델명과 provider
- Arm 이름
- 비용, 토큰, 실행 시간
- 라우팅 확신도와 선택 이유
- 실행 순서

평가 축은 100점으로 정규화한다.

| 평가 축 | 기본 가중치 |
| --- | ---: |
| 작업 정답성 | 40 |
| 지시·제약 준수 | 25 |
| 출력 계약 | 15 |
| 완전성 | 10 |
| 안전성과 위험 표현 | 10 |

작업별 객관적 정답이 있는 추출·분류·계산·코드 테스트는 Judge 점수보다 deterministic scorer를 우선한다. Judge는 자유형 결과의 품질 평가를 보완한다.

위치 편향 확인을 위해 전체 요청 중 20%는 출력 순서를 다시 섞어 재평가한다. 재평가 편차가 기준을 넘으면 품질 결과를 확정하지 않는다.

## 10. 수집 지표

### 10.1 품질

- 평균, 중앙값, 하위 10% 품질 점수
- 작업군별 평균 품질
- 고위험 작업군 최저 품질
- JSON·출력 계약 통과율
- deterministic 정답률
- high fixed 대비 paired 품질 차이
- low fixed 대비 품질 회복량

### 10.2 비용

- 처리 모델 비용
- Runtime Judge 비용
- fallback·retry 비용
- 자동 Arm 총 제품 비용
- 독립 품질 Judge 비용. 제품 비용과 분리
- high/mid/low fixed 대비 순절감액과 절감률

### 10.3 속도

- 처리 모델 latency `p50`, `p95`
- Runtime Judge latency
- 전체 workflow latency `p50`, `p95`
- fallback·retry를 포함한 사용자 체감 완료 시간

### 10.4 운영 안정성

- workflow 실패율
- provider incomplete 응답
- Schema 실패율
- fallback과 retry 비율
- 사용할 수 없는 모델 선택 횟수
- 선택 모델과 실제 호출 모델 불일치 횟수

### 10.5 라우팅 품질과 다양성

- 사전 허용 모델군 적중률: 각 요청의 평가 metadata에 복수의 `acceptable_model_ids`, `underpowered_model_ids`, `overprovisioned_model_ids`를 둔다. 자동 선택을 `appropriate`, `underpowered`, `overprovisioned`, `unclassified`로 나눠 계산한다. 이 metadata는 Runtime Judge 입력에 전달하지 않는다.
- 과소 선택률과 과잉 선택률
- 작업군별 선택 모델 분포
- 선택한 고유 모델 수
- 최다 선택 모델 비중
- 모델 분포 entropy와 concentration
- 같은 난이도지만 요구 능력이 다른 요청에서 모델 선택이 달라지는 비율
- 전문 추론 모델이 실제 전문 추론 요청에서 선택된 비율

서로 다른 8개 작업군인데 한 모델이 전체 자동 실행의 70%를 넘으면 `과집중 경고`를 표시한다. 다만 모델 다양성이 낮다는 이유만으로 실패 처리하지 않고 품질·비용 근거와 함께 해석한다.

## 11. 사전 성공 기준

| 항목 | 채택 기준 |
| --- | --- |
| high fixed 대비 평균 품질 손실 | 5점 이내 |
| 고위험·전문 추론 품질 손실 | 3점 이내 또는 비열등성 확인 |
| high fixed 대비 비용 절감 | 25% 이상 |
| 출력 계약 통과율 차이 | high fixed 대비 2%p 이내 |
| fallback 비율 | 5% 이하 |
| 사전 허용 모델군 적중률 | 75% 이상 |
| underpowered 선택률 | 5% 이하 |
| 전체 `p95` 지연 | high fixed 대비 10% 초과 증가하지 않음 |

80개 표본은 제품 방향을 판단하기 위한 실험이다. 작업군당 10개이므로 평균값만 단정하지 않고 paired 차이와 bootstrap 95% 신뢰구간을 함께 보고한다.

## 12. 중단 조건

다음 중 하나가 발생하면 원인을 수정하기 전까지 실험을 중단한다.

- 진단 모드가 켜져 있음
- 네 Arm의 prompt 또는 출력 계약이 다름
- 실행 주체 credential이 한 Arm에서만 다름
- 3회 연속 인증·권한 실패
- 5회 연속 provider 또는 workflow 실패
- 후보 모델 alias가 중복되어 Judge에 전달됨
- 모델명 또는 Arm 이름이 품질 Judge 입력에 노출됨
- 자동 Arm trace에 선택 모델·실제 모델·decision source가 없음
- 결과 파일에 secret 또는 원문 credential이 포함됨
- 예상 실험 비용이 사전 상한의 120%를 초과함

## 13. 실행 산출물

```text
reports/model-routing/runs/judge-first/norag-80-blind-<run-id>/
  experiment-config.json
  dataset.json
  execution-schedule.json
  execution-events.jsonl
  execution-results.json
  blind-quality-packets.json
  blind-quality-mapping.private.json
  quality-results.json
  checkpoints/
    batch-01-10.md
    ...
    batch-71-80.md
  final-report.md
```

`execution-events.jsonl`은 요청 하나의 네 Arm 실행과 해당 품질 평가가 끝날 때마다 append-only로 fsync 저장한다. Provider 비용이 발생한 직후 결과를 남기므로 process가 중단돼도 이미 끝난 요청을 식별할 수 있다. Markdown/JSON 누적 보고서는 10건마다 생성한다.

`blind-quality-mapping.private.json`에는 익명 ID와 실제 Arm의 대응이 들어 있으므로 외부 Judge에 전달하지 않는다. secret과 raw credential은 어떤 산출물에도 기록하지 않는다.

## 14. 최종 보고서 해석

최종 보고서는 단순 평균 순위를 넘어 다음 질문에 답해야 한다.

1. 자동 라우팅은 high fixed 대비 품질을 몇 점 잃고 비용을 몇 % 줄였는가?
2. 품질 1점 손실당 절감된 금액은 얼마인가?
3. low fixed 대비 추가 비용으로 품질을 얼마나 회복했는가?
4. mid fixed와 비교해 자동 라우팅의 복잡성과 Judge 비용을 감수할 가치가 있는가?
5. Runtime Judge 비용과 fallback 비용을 포함한 뒤에도 순절감이 남는가?
6. 평균은 좋아도 고위험 요청이나 하위 10%에서 위험한 손실이 발생하지 않았는가?
7. 51~80회 local takeover 구간에서 Judge 호출, 비용, 지연이 실제 줄었는가?
8. 자동 라우터가 특정 모델 하나로 고착되지 않았는가?
9. 선택 모델 다양성이 요청의 능력 차이로 설명되는가?
10. 고가 모델을 잃은 품질과 얻은 비용의 trade-off가 제품과 시연에서 납득 가능한가?

최종 결론은 다음 형식으로 작성한다.

> 자동 라우팅은 high fixed 대비 총 제품 비용을 X% 절감했고 평균 품질은 Y점 감소했다. 고위험 작업의 품질 손실은 Z점이며 출력 계약 통과율 차이는 N%p였다. low fixed와 비교하면 비용은 A% 증가했지만 품질은 B점 향상됐다. Runtime Judge와 fallback 비용을 포함한 순절감은 C%였다. 같은 난이도 안에서도 작업 요구 능력에 따라 D개 모델을 선택했으며 underpowered 선택률은 E%였다. 따라서 이번 자동 라우팅의 품질·비용·속도 trade-off는 합리적/조건부 합리적/비합리적이다.

## 15. 실행 전 확정해야 할 값

- high/mid/low fixed 모델 ID
- 자동 라우팅 전체 후보 목록과 canonical alias 결과
- Runtime Judge 모델과 운영용 최대 출력 토큰
- 독립 품질 Judge 모델
- temperature, 최대 출력 토큰, timeout, retry
- 품질 비열등 기준과 고위험 작업 하한
- 예상 제품 실행 비용, 품질 평가 비용, 전체 중단 상한
- 1~50회와 51~80회 local takeover 계약이 현재 코드와 일치하는지
- 실험 실행이 운영 정책 학습 통계를 오염시키지 않도록 하는 experiment 식별자
