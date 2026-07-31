import { describe, expect, it } from 'vitest';
import {
  auditEventSummary,
  auditMetadataLabel,
  auditMetadataValueLabel,
  auditStatusLabel,
  auditTargetLabel,
} from './auditPresentation';

describe('auditPresentation', () => {
  it('현재 조직 권한 거부를 사람이 읽는 문장으로 만든다', () => {
    expect(
      auditEventSummary({
        action: 'permission.denied',
        status: 'failure',
        targetType: 'organization',
        targetId: 'org-1',
        currentOrganizationId: 'org-1',
      }),
    ).toBe('현재 조직에 대한 접근이 거부되었습니다.');
    expect(auditStatusLabel('failure', 'permission.denied')).toBe('접근 거부');
  });

  it('보안 알림 관리자 API 거부 원인을 구체적으로 설명한다', () => {
    expect(
      auditEventSummary({
        action: 'permission.denied',
        status: 'failure',
        targetType: 'organization',
        targetId: 'org-1',
        currentOrganizationId: 'org-1',
        requiredPermission: 'security_alert.manage',
        requestedOperation: 'security_alert.list',
      }),
    ).toBe(
      '보안 알림 목록 조회를 시도했지만 조직 관리자 권한이 필요해 거부되었습니다.',
    );
    expect(auditMetadataLabel('required_permission')).toBe('필요한 권한');
    expect(
      auditMetadataValueLabel('required_permission', 'security_alert.manage'),
    ).toBe('조직 관리자 권한');
  });

  it('보안 알림 operation이 없으면 자연스러운 공통 문구를 사용한다', () => {
    expect(
      auditEventSummary({
        action: 'permission.denied',
        status: 'failure',
        targetType: 'organization',
        targetId: 'org-1',
        currentOrganizationId: 'org-1',
        requiredPermission: 'security_alert.manage',
        requestedOperation: null,
      }),
    ).toBe(
      '보안 알림 관련 작업을 시도했지만 조직 관리자 권한이 필요해 거부되었습니다.',
    );
  });

  it('알려진 대상 종류는 한국어 이름과 안전한 ID를 유지한다', () => {
    expect(auditTargetLabel('organization_membership', 'member-1')).toBe(
      '조직 멤버 · member-1',
    );
    expect(auditTargetLabel('workflow', 'workflow-1')).toBe(
      'Workflow · workflow-1',
    );
  });

  it('일반 성공·실패 action도 설명 문장으로 만든다', () => {
    expect(
      auditEventSummary({
        action: 'workflow.deploy',
        status: 'success',
        targetType: 'workflow',
        targetId: 'workflow-1',
      }),
    ).toBe('Workflow · workflow-1에서 Workflow 배포 작업이 완료되었습니다.');
  });
});
