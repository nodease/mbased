import { describe, it } from 'vitest';

describe('workflow test cases: 노드 실행 기록 패널 추가', () => {
  it.todo(
    '실행 기록 탭 기본 상태는 실행 목록 검색과 가장 최신 로그 기록 불러오기 버튼을 표시한다',
  );
  it.todo('선택된 실행 기록이 없으면 빈 상태 안내를 표시한다');
  it.todo('실행 목록 검색 버튼을 누르면 오른쪽 패널이 picker view로 전환된다');
  it.todo('picker view는 workflow run 목록을 최신순으로 표시한다');
  it.todo(
    'picker row는 workflow run 요약과 현재 node_id의 node run/trace preview를 함께 표시한다',
  );
  it.todo(
    'picker row를 선택하면 detail view로 전환되고 선택한 run 안의 현재 노드 input/output을 표시한다',
  );
  it.todo(
    '가장 최신 로그 기록 불러오기 버튼은 현재 node_id 기록이 포함된 가장 최신 run을 선택한다',
  );
  it.todo(
    '현재 node_id 기록이 없는 run은 row에서 제외되거나 현재 노드 기록 없음으로 표시된다',
  );
  it.todo('input/output JSON 값은 읽기 가능한 형태로 렌더링된다');
  it.todo('plain text input/output 값은 줄바꿈이 보존되어 렌더링된다');
  it.todo(
    'workflow read 권한이 있는 사용자는 node execution log 목록 API로 현재 node_id 기록이 포함된 run 목록을 조회할 수 있다',
  );
  it.todo(
    'node execution log 목록 API는 limit, cursor, status, q, from, to query를 처리한다',
  );
  it.todo(
    'node execution log 목록 API는 full input/output이 아니라 preview 문자열만 반환한다',
  );
  it.todo(
    'node execution log 상세 API는 선택한 run 안의 현재 node_id input/output/trace/usage 상세를 반환한다',
  );
  it.todo(
    'workflow read 권한이 없는 사용자의 실행 로그 조회 요청은 403으로 거부된다',
  );
  it.todo(
    'active organization scope 밖 workflow의 실행 로그 조회 요청은 404로 처리된다',
  );
  it.todo(
    '현재 node_id 기록이 없는 workflow는 목록 API에서 빈 목록을 반환한다',
  );
  it.todo(
    'run은 존재하지만 현재 node_id 기록이 없는 상세 조회는 404 또는 node_execution_log.not_found로 처리한다',
  );
  it.todo(
    '상태 필터를 실패로 바꾸면 실패 run 또는 실패 node 기록만 목록에 남는다',
  );
  it.todo(
    '검색어를 입력하면 input/output/error preview에 해당 검색어가 포함된 실행 기록을 찾을 수 있다',
  );
  it.todo(
    'workflow read 권한만 있는 사용자는 실행 기록을 조회할 수 있지만 노드 설정을 수정할 수 없다',
  );
  it.todo('실행 기록 조회는 workflow write 권한을 요구하지 않는다');
  it.todo(
    '현재 node_id 기록이 있는 실행 로그가 없으면 이 노드의 실행 기록이 없습니다 안내를 표시한다',
  );
  it.todo(
    'run detail에는 있으나 input/output payload가 retention 또는 redaction으로 비어 있으면 표시 가능한 기록 없음으로 표시한다',
  );
  it.todo(
    '실행 로그가 120개 이상 있어도 초기 목록은 제한된 개수만 렌더링하고 더 보기로 확장한다',
  );
  it.todo('실행 로그 목록 조회 실패 시 오류 안내와 다시 시도 액션을 표시한다');
});
