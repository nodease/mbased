# Observability Query Benchmark

감사·추적 조회의 변경 전/후 SQL을 같은 임시 PostgreSQL 데이터에서 비교한다. 임시 테이블만 사용하며 애플리케이션 데이터는 수정하지 않는다.

```bash
PYTHONPATH=$(git rev-parse --show-toplevel) apps/gateway/.venv/bin/python \
  tests/performance/benchmark_observability_queries.py \
  --rows 100000 --iterations 5
```

결과의 `before_ms`/`after_ms`는 반복 실행 중앙값이고 `speedup`은 `before / after`다. 합성 데이터 결과이므로 운영 DB에서는 `EXPLAIN (ANALYZE, BUFFERS)`로 다시 확인한다.

Audit cursor 항목은 운영 목록과 동일하게 `audit_metadata ->> 'organization_id'` 조건을 양쪽 쿼리에 적용한다. 100개 조직 중 한 조직의 행을 대상으로 90% 지점까지 이동하며, cursor도 같은 조직 범위에서 만든다. `audit_filtered_total_count`는 정확한 전체 개수 조회 비용이고 `audit_first_page_count_plus_page`는 첫 페이지의 COUNT와 목록 조회를 연속 실행한 시간이다. `audit_followup_cursor_count_skip`은 같은 후속 cursor 페이지에서 기존 `COUNT+페이지`와 개선된 `페이지 단독`을 비교한다. `audit_original_offset_vs_optimized_followup`은 최초 OFFSET 구현과 최종 cursor+COUNT 생략 구현을 비교한다. 권한 확인과 표시명 조회, HTTP 직렬화 시간은 포함하지 않는다.

Trace 항목은 실제 visibility policy join 전체가 아니라 `5,000건 fetch 후 필터`와 `SQL에서 visible 20건 제한`의 조회량 차이를 단순화해 측정한다. 실제 `/api/v1/traces` 응답 시간으로 해석하지 않는다.

측정 결과는 `reports/`에 데이터 규모와 실행 날짜별 JSON으로 보관한다. 같은 날짜에 데이터 규모가 다르면 파일명에 `100k`처럼 행 수를 표시한다.

## RAG Retrieval Fan-out

사전 계산 query embedding을 사용하는 KB 1/2/4/10/20개 검색의 순차 기준과 bounded native-thread fan-out을 합성 blocking I/O로 비교한다. Provider, PostgreSQL과 실제 query/vector는 사용하지 않으며 절대 latency는 merge gate가 아니다. Overlap, 최대 active worker 5개, candidate당 검색 1회와 query embedding provider model당 1회 계약을 확인하기 위한 보조 측정이다.

```bash
PYTHONPATH=$(git rev-parse --show-toplevel) apps/workflow_engine/.venv/bin/python \
  tests/performance/benchmark_rag_retrieval_fanout.py \
  --candidates 1,2,4,10,20 --delay-ms 20 --iterations 20
```

출력에는 p50/p95, 최대 active worker와 호출 수만 포함한다. Raw query, vector, resource identifier와 provider/DB 오류는 기록하지 않는다.
