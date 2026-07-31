// ADR-0008 canonical action에서 사용자 친화 라벨을 파생한다.
// 라벨은 표시 보조일 뿐이며 검색/필터 비교는 항상 canonical 문자열로 수행한다.
const ACTION_LABELS: Record<string, string> = {
  'permission.denied': '권한 차단',
  'permission.revoke': '권한 회수',
  'auth.permission_denied': '인증/권한 차단',
  'permission_request.created': '권한 신청 제출',
  'permission_request.approved': '권한 신청 승인',
  'permission_request.rejected': '권한 신청 거절',
  'user_app_creation_permission.created': 'App 생성 권한 부여',
  'user_app_creation_permission.deleted': 'App 생성 권한 회수',
  'organization.invite': '멤버 초대',
  'organization.member.accept': '초대 수락',
  'organization.member.decline': '초대 거절',
  'organization.member.update': '멤버 정보 변경',
  'organization.member.remove': '멤버 제거',
  'workflow.execute': 'Workflow 실행',
  'workflow.deploy': 'Workflow 배포',
  'deployment.toggle': '배포 상태 변경',
  'deployment.activate_previous': '이전 배포 재활성화',
  'deployment.delete': '배포 삭제',
  'llm.call': 'LLM 호출',
  'mail_credential.create': 'Mail Credential 등록',
  'mail_credential.update': 'Mail Credential 수정',
  'mail_credential.revoke': 'Mail Credential 폐기',
  'policy.block': '정책 차단',
  'policy.warn': '정책 경고',
  'rag.retrieve': 'RAG 검색',
  'resource.not_found': '리소스 없음',
};

const VERB_LABELS: Record<string, string> = {
  created: '생성',
  updated: '수정',
  deleted: '삭제',
  approved: '승인',
  rejected: '거절',
  denied: '차단',
};

export function auditActionLabel(action: string): string | null {
  const exact = ACTION_LABELS[action];
  if (exact) return exact;

  const separatorIndex = action.lastIndexOf('.');
  if (separatorIndex <= 0) return null;

  const verb = VERB_LABELS[action.slice(separatorIndex + 1)];
  if (!verb) return null;
  return `${action.slice(0, separatorIndex)} ${verb}`;
}
