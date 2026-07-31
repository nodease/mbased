# Judge-first RAG 50건 공정 비교 실험 계획

Status: Draft

## 목적

이 실험은 RAG가 연결된 하나의 기업 요청 처리 LLM 노드에서 자동 모델 라우팅이 실제 비용을 줄이면서도, 항상 비싼 모델을 고정한 결과보다 품질을 과도하게 낮추지 않는지 확인한다.

이번에는 요청을 난이도별 10개씩 묶어 실행하지 않는다. 실제 서비스처럼 서로 다른 길이, 주제, 위험도, RAG 근거 필요 여부를 섞은 50개 요청을 준비하고, **같은 50개 요청**을 네 방식에 모두 적용한다.

| 비교 방식 | 의미 |
| --- | --- |
| 자동 라우팅 | 현재 Judge-first 정책과 local learner가 선택한 모델로 실행 |
| 고가 고정 | 같은 노드를 고성능 기준 모델 하나로 고정 실행 |
| 중가 고정 | 같은 노드를 중간 비용 기준 모델 하나로 고정 실행 |
| 저가 고정 | 같은 노드를 저비용 기준 모델 하나로 고정 실행 |

따라서 총 실행 수는 `50 요청 x 4 방식 = 200회`다. 요청을 난이도별 10개씩 나누지는 않지만, 섞인 요청을 10개 처리할 때마다 네 방식의 누적 결과 40개를 기준으로 중간 보고서를 갱신한다. 보고서 시점은 10·20·30·40·50개 요청 완료 후 총 5회다.

## 왜 익명 품질 평가가 필요한가

출력 품질을 평가하는 모델이 `자동 라우팅`, `고가 고정`, `중가 고정`, `저가 고정`이라는 이름이나 모델명, 비용, 속도를 보면 그 정보에 끌려 점수를 줄 수 있다. 이를 막기 위해 실행이 끝난 뒤 각 요청의 네 출력을 `response_1`부터 `response_4`까지 무작위 순서로 바꿔 품질 Judge에 전달한다.

품질 Judge가 받는 정보:

- 원래 요청과 고객 등급
- RAG 근거가 필요한 요청인지
- JSON 출력 계약과 안전 응답 기준
- 이름이 가려진 세 출력

품질 Judge가 받지 않는 정보:

- 실행 arm 이름
- 모델명과 provider
- 비용, 토큰, 시간
- 어떤 출력이 먼저 실행됐는지

Judge 응답의 `response_1` 같은 키를 실제 arm에 다시 연결하는 mapping은 로컬 결과 폴더에만 별도 보관한다. 외부 Judge request에는 포함하지 않는다.

## 데이터셋

원본 파일: `tests/fixtures/model_routing/judge_first_rag_50_cases.json`

50개 요청은 다음 영역을 섞어 둔다.

| 영역 | 예시 성격 | RAG 역할 |
| --- | --- | --- |
| 제품 사용 안내 | 짧은 화면 사용법 | 일반적으로 불필요 |
| 계정·접근 관리 | MFA, 권한 이전, 협력사 계정 | 온보딩/권한 정책 근거 |
| 온보딩 문서 질의 | 교육, VPN, 지급 승인 | 문서 검색과 근거 종합 |
| 운영 장애 | 웹훅, OAuth, 복구, 중복 처리 | 직접 판단 또는 절차 안내 |
| 보안·개인정보 | 유출, 권한 회수, SLA | 보수적 위험 판단 |
| 재무·계약 | 보상, 결산, 계약, 승인 | 다중 조건 판단 |

`expected_difficulty`는 라우터에 주는 힌트가 아니라, 실험 후 사람이 예상과 실제 선택을 비교하기 위한 참조값이다. RAG 연결 노드라도 모든 요청이 같은 검색 근거를 얻지는 않으므로 `requires_grounding`으로 문서 근거가 꼭 필요한 요청을 표시한다.

## 사전 준비 실행

재빌드 중에는 아래 명령만 실행한다. 이 명령은 provider, DB, WorkflowEngine을 호출하지 않는다.

```powershell
.\apps\workflow_engine\.venv\Scripts\python.exe `
  .\scripts\experiment_judge_first_rag_50_blind.py `
  --prepare
```

생성 위치는 `reports/model-routing/runs/judge-first/rag-50-blind-prepared/`이며 git ignore 대상이다.

- `dataset.json`: 실제 실행할 50개 synthetic 요청 사본
- `execution-schedule.json`: arm별 순서를 case마다 섞은 200회 실행 계획
- `checkpoints/report-after-10-cases.md` 등: 10개 요청마다 갱신할 누적 보고서 경로

실행기가 만든 결과 JSON은 아래 명령으로 익명 품질 평가 묶음으로 바꾼다. 이 단계도 provider를 호출하지 않는다.

```powershell
.\apps\workflow_engine\.venv\Scripts\python.exe `
  .\scripts\experiment_judge_first_rag_50_blind.py `
  --build-blind-package .\reports\model-routing\runs\judge-first\<run-id>\execution-results.json `
  --output-dir .\reports\model-routing\runs\judge-first\<run-id>
```

`blind-quality-packets.json`만 품질 Judge에 전달한다. `blind-quality-mapping.private.json`은 점수를 원래 arm에 연결하는 로컬 전용 파일이다.

## 실제 실행 전 확인 항목

1. 동일한 RAG KB와 JSON 출력 계약을 네 arm에 똑같이 적용한다.
2. 고가/중가/저가 고정 arm과 자동 arm의 deployment snapshot 차이는 모델 선택 방법뿐이어야 한다.
3. 각 실행에서 workflow 성공 여부, JSON 계약 통과, 검색 문서 수, RAG context 길이, 선택 모델, fallback, 비용, 지연 시간을 저장한다.
4. 50개 요청을 모두 끝낸 뒤에만 blind quality packet을 만든다. 중간 결과를 보고 arm별 모델 정책을 수동으로 바꾸지 않는다.
5. 품질 Judge 비용은 제품 실행 비용과 분리해서 기록한다.

중간 보고서는 10개 요청 완료 시점의 비용·지연·JSON 계약·fallback·선택 모델 분포를 먼저 기록한다. 익명 품질 평가는 동일 시점의 네 출력이 모두 모인 요청만 대상으로 packet을 만들고, 최종 50개 완료 후 전체 점수를 다시 집계한다.

## 결과 판단

자동 라우팅이 채택 가능한 결과인지 다음 세 축으로 판단한다.

- **품질:** 익명 Judge 평균 점수, JSON 계약 통과율, RAG 근거 요청의 안전 응답 여부
- **비용:** 고가 고정과 중가 고정 대비 실행 비용 절감률, 라우팅 Judge 비용을 포함한 순비용
- **속도:** 처리 모델 시간과 라우팅 Judge 시간을 분리한 전체 지연 시간

저가 고정은 비용의 하한선이고 고가 고정은 품질의 기준선이며, 중가 고정은 현실적인 단일 모델 기준선이다. 자동 라우팅은 고가 고정에 가까운 품질을 유지하면서 중가 고정보다도 비용·속도 또는 품질 중 하나에서 분명한 이점을 보여야 의미가 있다.

## 이번 준비 범위에서 하지 않는 일

- 실제 provider 호출
- DB workflow/deployment 생성 또는 reset
- 정책 학습 상태 변경
- 실제 품질 Judge 호출과 점수 산출

이 작업은 Docker 재빌드와 실행 환경 확인이 끝난 뒤 별도 단계에서 진행한다.
