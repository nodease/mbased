# RAG Evaluation Test Suite

RAG(Retrieval-Augmented Generation) 검색 시스템의 성능을 평가하기 위한 종합 테스트 스위트입니다.

## 🚀 Quick Start (전체 워크플로우)

```bash
cd apps/server

# 1. HotpotQA 데이터셋 다운로드 (50개 샘플)
python tests/evaluation/prepare_datasets.py --dataset hotpotqa --samples 50

# 2. 문서를 Knowledge Base에 인덱싱
python tests/evaluation/index_documents.py --dataset hotpotqa --kb-name "RAG Eval - HotpotQA"

# 3. 벤치마크 실행 (KB ID는 2번에서 출력됨)
python tests/evaluation/run_benchmark.py --kb-id YOUR_KB_ID --dataset hotpotqa --samples 50

# 4. 리포트 확인
cat tests/evaluation/reports/rag_eval_hotpotqa_*.md
```

## 📂 구조

```
tests/evaluation/
├── __init__.py
├── rag_metrics.py        # 평가 지표 구현
├── rag_evaluator.py      # 평가 프레임워크
├── test_rag_baseline.py  # 베이스라인 테스트
├── datasets/
│   └── sample_qa.json    # 샘플 데이터셋
└── reports/              # 평가 리포트 저장
```

## 🚀 사용법

### 1. 단위 테스트 실행

```bash
cd apps/server
pytest tests/evaluation/test_rag_baseline.py -v
```

### 2. 베이스라인 성능 측정

```python
from tests.evaluation.rag_evaluator import RAGEvaluator, EvaluationConfig, DatasetLoader
from tests.evaluation.rag_metrics import RetrievalResult

# 설정
config = EvaluationConfig(
    dataset_name="baseline_v1",
    knowledge_base_id="your-kb-id",
    top_k_values=[1, 3, 5, 10]
)

# 샘플 로드
samples = DatasetLoader.load_json("tests/evaluation/datasets/sample_qa.json")

# 평가 실행
evaluator = RAGEvaluator(config)
results = evaluator.evaluate(samples, your_retrieval_func)

# 리포트 저장
evaluator.save_report(results)
print(results.summary())
```

### 3. 성능 비교

```python
# 두 리포트 비교
comparison = evaluator.compare_reports([
    "reports/rag_eval_baseline_v1.json",
    "reports/rag_eval_improved_v2.json"
])
```

## 📊 지원 지표

| 지표            | 설명                             | 범위      |
| --------------- | -------------------------------- | --------- |
| **Recall@K**    | 정답이 top-k에 포함된 비율       | 0.0 - 1.0 |
| **Precision@K** | top-k 중 정답인 비율             | 0.0 - 1.0 |
| **Hit@K**       | 정답이 top-k에 하나라도 있으면 1 | 0 or 1    |
| **MRR**         | 첫 번째 정답의 역순위            | 0.0 - 1.0 |
| **NDCG@K**      | 순위 가중 정확도                 | 0.0 - 1.0 |

## 📁 데이터셋

### 지원 형식

1. **JSON 파일**

   ```json
   [{ "query": "질문", "relevant_passages": ["정답1", "정답2"] }]
   ```

2. **HuggingFace Datasets**
   - Natural Questions
   - HotpotQA

### 샘플 데이터셋

`datasets/sample_qa.json` - 10개의 사내 FAQ QA 쌍

## 📈 리포트 예시

```
============================================================
RAG Evaluation Report
============================================================
Dataset: baseline_v1
Samples: 100
Timestamp: 2026-01-13T12:00:00
------------------------------------------------------------
Metrics:
  hit@1: 0.4500
  hit@3: 0.6800
  hit@5: 0.7500
  mrr: 0.5234
  ndcg@5: 0.6123
  precision@5: 0.3200
  recall@5: 0.7500
============================================================
```

## 🔄 개선 추적

1. 베이스라인 측정 → `baseline_v1.json`
2. 개선 적용 (예: Query Rewriting)
3. 재측정 → `improved_v2.json`
4. 비교 리포트 생성

## ⚠️ 주의사항

- 실제 DB 연결 테스트는 `@pytest.mark.skip` 처리되어 있음
- HuggingFace 데이터셋 사용 시 `pip install datasets` 필요

## Agent Builder Intent Evaluation

`run_agent_builder_intent_benchmark.py`는 permission-aware 실제 planner model의 새 workflow/수정/전체 교체/unsupported 분류, capability 순서, 기존 node target/placement, 명시적 integration action을 측정한다. Deterministic unit test에는 포함하지 않는다. Dataset에는 `GitHub`와 `깃허브`로 표기한 GitHub Pull Request 생성 삽입 요청을 각각 포함한다.

```powershell
$env:NODEASE_EVAL_USER_ID = "<user uuid>"
$env:NODEASE_EVAL_ORGANIZATION_ID = "<organization uuid>"
$env:NODEASE_EVAL_CREDENTIAL_ID = "<credential uuid>"
$env:NODEASE_EVAL_MODEL_ID = "<model uuid>"
python tests/evaluation/run_agent_builder_intent_benchmark.py --output tests/evaluation/reports/agent_builder_intent.json
```

Runner는 DB의 active organization, credential `use` 권한, verified model relation을 그대로 검증한다. Report에는 case ID, 기대/실제 구조 필드, 지표만 기록하고 prompt, credential 원문, provider payload, node/edge ID를 기록하지 않는다. 기본 gate는 request type/draft mode 0.90, ordered capability exact match 0.80, target/placement 0.85, 명시된 integration action exact match 1.00, invalid output rate 0.05 이하이다.
