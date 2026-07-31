import { auditActionLabel } from './auditActionLabel';
import {
  isSecurityAlertOperation,
  type SecurityAlertOperation,
} from '../types/SecurityAlert';

const TARGET_TYPE_LABELS: Record<string, string> = {
  organization: '조직',
  organization_membership: '조직 멤버',
  team: '팀',
  workflow: 'Workflow',
  knowledge_base: 'Knowledge Base',
  llm_credential: 'LLM Credential',
  mail_credential: 'Mail Credential',
  deployment: '배포',
  app: 'App',
  user: '사용자',
};

const SECURITY_ALERT_OPERATION_LABELS: Record<SecurityAlertOperation, string> = {
  'security_alert.list': '보안 알림 목록 조회',
  'security_alert.summary': '보안 알림 요약 조회',
  'security_alert.detail': '보안 알림 상세 조회',
  'security_alert.evidence.list': '연결된 감사 기록 조회',
  'security_alert.acknowledge': '보안 알림 확인 처리',
  'security_alert.resolve': '보안 알림 해결 처리',
  'security_alert.reopen': '보안 알림 다시 열기',
};

export function auditTargetLabel(
  targetType: string | null,
  targetId: string | null,
  currentOrganizationId?: string | null,
): string {
  if (!targetType) return '대상 없음';
  if (
    targetType === 'organization' &&
    targetId &&
    targetId === currentOrganizationId
  ) {
    return '현재 조직';
  }
  const typeLabel = TARGET_TYPE_LABELS[targetType] || targetType;
  return targetId ? `${typeLabel} · ${targetId}` : typeLabel;
}

export function auditStatusLabel(status: 'success' | 'failure', action: string) {
  if (
    status === 'failure' &&
    (action === 'permission.denied' || action === 'auth.permission_denied')
  ) {
    return '접근 거부';
  }
  return status === 'success' ? '성공' : '실패';
}

export function auditEventSummary({
  action,
  status,
  targetType,
  targetId,
  targetDisplayLabel,
  currentOrganizationId,
  requiredPermission,
  requestedOperation,
}: {
  action: string;
  status: 'success' | 'failure';
  targetType: string | null;
  targetId: string | null;
  targetDisplayLabel?: string | null;
  currentOrganizationId?: string | null;
  requiredPermission?: string | null;
  requestedOperation?: string | null;
}) {
  const target =
    targetDisplayLabel ||
    auditTargetLabel(targetType, targetId, currentOrganizationId);
  if (action === 'permission.denied' || action === 'auth.permission_denied') {
    if (requiredPermission === 'security_alert.manage') {
      const operationWithParticle =
        requestedOperation && isSecurityAlertOperation(requestedOperation)
          ? `${SECURITY_ALERT_OPERATION_LABELS[requestedOperation]}를`
          : '보안 알림 관련 작업을';
      return `${operationWithParticle} 시도했지만 조직 관리자 권한이 필요해 거부되었습니다.`;
    }
    return `${target}에 대한 접근이 거부되었습니다.`;
  }
  if (action === 'policy.block') {
    return `${target}에 대한 요청이 정책에 의해 차단되었습니다.`;
  }
  const actionLabel = auditActionLabel(action) || '알 수 없는 작업';
  return status === 'success'
    ? `${target}에서 ${actionLabel} 작업이 완료되었습니다.`
    : `${target}에서 ${actionLabel} 작업에 실패했습니다.`;
}

export function auditMetadataLabel(key: string) {
  return (
    {
      organization_id: '조직 ID',
      required_permission: '필요한 권한',
      requested_operation: '시도한 작업',
      denial_reason: '거부 사유',
      request_id: '요청 ID',
      reason: '사유',
    }[key] || key
  );
}

export function auditMetadataValueLabel(key: string, value: unknown) {
  if (key === 'required_permission' && value === 'security_alert.manage') {
    return '조직 관리자 권한';
  }
  if (
    key === 'requested_operation' &&
    typeof value === 'string' &&
    isSecurityAlertOperation(value)
  ) {
    return SECURITY_ALERT_OPERATION_LABELS[value] || value;
  }
  if (key === 'denial_reason' && value === 'organization_manager_required') {
    return '조직 관리자 권한이 필요함';
  }
  return typeof value === 'string' ? value : JSON.stringify(value);
}
