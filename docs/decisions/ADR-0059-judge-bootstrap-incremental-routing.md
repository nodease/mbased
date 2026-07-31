# ADR-0059: Judge Bootstrap과 점진 학습 로컬 모델 라우팅

Status: Accepted

Supersedes: [ADR-0058](ADR-0058-bootstrap-difficulty-routing-policy.md)

## Context

ADR-0045의 bootstrap 복잡도 회귀기는 Planner가 만든 소수 예시만으로 첫 요청부터
모델을 선택했다. 실제 운영 요청과 예시의 분포가 다르면, 로컬 회귀기는 충분한 근거 없이
낮거나 높은 모델을 선택할 수 있었다. 반대로 운영 성적만 신뢰하면 새 모델을 실행해
증거를 쌓지 못해 기본 모델에 고착될 수 있다.

Nodease는 첫 요청부터 자동 라우팅을 제공해야 하지만, 매 요청마다 Judge를 영구적으로
호출해 비용과 지연 시간을 계속 늘리는 구조도 피해야 한다.

## Decision

자동 모델 라우팅은 `judge_bootstrap_incremental_v1` 전략을 사용한다.

1. **Judge bootstrap**: 자동 라우팅을 처음 켠 배포 정책은 각 운영 요청마다 짧은 Judge
   호출을 실행한다. Judge는 현재 변수 치환이 끝난 요청과 노드의 고정 계약, 실행 주체가
   사용할 수 있는 후보 모델만 보고 선택 모델과 신뢰도를 반환한다.
2. **정책과 독립된 학습 표본**: Judge 선택, 후보 집합, 로컬 분류용 numeric artifact,
   실제 실행 모델, schema/downstream/성공/fallback 결과는 배포 정책이 아니라
   `organization + workflow + node + task_fingerprint + judge_contract_hash`로 식별한
   자동 라우팅 학습기에 저장한다. 원문 prompt, 입력 payload, RAG 문서 원문,
   credential은 저장하지 않는다. 같은 작업 지문과 학습 계약은 재배포 뒤에도 같은
   학습기를 사용한다.
3. **비동기 점진 학습**: 요청 중에는 로컬 라우터가 먼저 예측하고 Judge 정답과 비교할
   안전한 숫자 vector와 예측값만 저장한다. workflow 완료 후 계약 결과로 label을 확정하며,
   Celery worker가 학습기별 10건 또는 첫 대기 label 이후 최대 5분 단위로 학습한다. 학습기
   행을 잠가 같은 학습기에 대한 병렬 batch가 가중치와 카운터를 덮어쓰지 않게 한다. 요청 처리
   경로에서는 가중치를 갱신하지 않는다.
   로컬 학습 vector는 `referenced_variables`가 가리키는 실행별 값을 핵심 요청, 동적 문맥,
   구조화 특징으로 나누어 각각 임베딩한 뒤 `75% / 15% / 10%` 비율로 결합한다. 변하는 RAG
   안전 신호는 구조화 특징에 포함한다. 노드 제목, 작업 설명, system/user/assistant prompt의
   고정 문구는 같은 작업에서 요청 차이를 가리므로 제외한다. prompt, RAG, 출력 또는 downstream
   계약이 바뀌어 작업 지문이 달라지거나 Judge rubric, feature schema, E5 encoder가 바뀌면
   새 학습기 계보를 만들고 이전 artifact와 새 vector를 섞지 않는다.
4. **낮은 신뢰도 보완**: `local_first`에서도 처음 보는 입력처럼 학습 표본과 거리가 멀거나,
   예측 경계가 모호해 로컬 분류기의 신뢰도가 기준 미만이거나,
   선택 모델이 현재 실행 주체에게 더 이상 허용되지 않으면 Judge를 다시 호출한다.
5. **안전한 실패 처리**: Judge 호출·응답 파싱·학습이 실패해도 workflow 실행은 막지 않는다.
   사용자가 정한 기본 모델과 기본 대체 모델로 닫는다.
6. **성과 반영**: 배포 후 운영 실행만 학습 label과 품질·비용·지연 성적에 반영한다. 재평가 주기에는
   완료된 Judge 표본과 운영 성적을 점검해 local-first 전환을 유지하거나 Judge-first로
   되돌린다. Test Sidebar 실행은 학습 및 집계 대상이 아니다.
7. **불변 학습 버전과 전환 기준**: Judge 정답 50건 이상과 최근 20건의 학습 전 예측을 기준으로 Judge 요구
   수준 완전 일치율 80% 이상, 각 축 평균 오차 0.5 이하, 계약 통과율 95% 이상을 모두
   만족해야 한다. Judge 정답이 여러 종류인데 로컬 예측이 한 값으로만 수렴한 경우에는
   전환하지 않는다. 기준을 통과한 candidate artifact는 변경 불가능한 learner version으로
   발행한다. 배포 정책은 학습 가중치나 카운터를 직접 소유하지 않고 `learner_id`와
   `active_learner_version_id`만 참조한다. 새 버전은 같은 학습기에 연결된 활성 정책에
   자동 반영하며, 활성 버전의 안전 기준이 무너지면 정책은 Judge-first로 돌아가되 기존
   버전은 감사용으로 보존한다.
8. **테스트 실행 격리**: Test Sidebar 실행은 같은 작업 지문의 검증 버전이 있으면 로컬
   라우터를 사용하고, 없거나 불확실하면 Judge를 사용한다. 어느 경로든 학습 label, 학습
   횟수, 운영 성적과 정책 점검 카운터는 변경하지 않는다.

Judge 비용은 일반 LLM 실행 비용과 구분해 같은 workflow run의 별도 usage row와 trace
metadata에 기록한다. 따라서 운영 총비용에는 포함되지만 노드 본 실행의 모델 성적을
오염시키지 않는다.

## Consequences

- 새 workflow도 첫 배포 실행부터 Judge가 여러 후보 중 하나를 선택하므로, 검증 모델이
  없어 기본 모델만 계속 쓰는 순환 구조를 만들지 않는다.
- 초기에는 요청당 Judge 비용과 지연 시간이 추가된다. 이 비용은 local-first 전환 뒤에는
  낮은 신뢰도 요청에만 발생한다.
- 로컬 모델은 Judge의 결정을 그대로 흉내 내는 데서 끝나지 않는다. 운영 결과의
  schema/downstream/실행 성공률이 기준을 통과하지 못하면 local-first 전환을 보류하거나
  되돌린다.
- 배포 정책을 삭제하거나 새로 배포해도 작업 지문과 학습 계약이 같으면 검증된 learner
  version을 재사용한다. 반대로 작업 또는 학습 계약이 바뀌면 이전 계보를 덮어쓰지 않는다.
- 과거 전략의 DB row와 migration schema는 데이터 호환을 위해 유지하지만 신규 runtime은
  이를 실행하지 않는다. refresh 시 정적 rule을 제거한 Judge-first 정책으로 한 번 이관하고,
  이관 전 실행은 노드에 저장된 기본 모델로 안전하게 닫는다.
- 기존 policy 소유 학습 label과 `active_policy.learning` artifact는 새 계약으로 안전하게
  이관할 수 없으므로 migration에서 폐기한다. 기존 정책, 실행 로그와 비용·성능 데이터는
  유지한다.
