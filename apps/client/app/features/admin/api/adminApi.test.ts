import { afterEach, describe, expect, it, vi } from 'vitest';

vi.mock('@/lib/apiClient', () => ({
  apiClient: {
    get: vi.fn(),
    post: vi.fn(),
    delete: vi.fn(),
  },
}));

import { apiClient } from '@/lib/apiClient';
import { adminApi } from './adminApi';

const mockedGet = vi.mocked(apiClient.get);
const mockedPost = vi.mocked(apiClient.post);
const mockedDelete = vi.mocked(apiClient.delete);

afterEach(() => {
  // 실패한 테스트가 소비하지 못한 mockResolvedValueOnce 큐가 다음 테스트로
  // 새지 않도록 구현까지 초기화한다.
  vi.resetAllMocks();
});

describe('adminApi.listAuditLogs', () => {
  it('빈 필터 값은 query에서 제외하고 조회한다', async () => {
    mockedGet.mockResolvedValueOnce({ data: { total: 0, items: [] } });

    const result = await adminApi.listAuditLogs({
      cursor: 'cursor-2',
      limit: 20,
      action: 'workflow.deploy',
      actorId: undefined,
      targetType: '',
    });

    expect(mockedGet).toHaveBeenCalledWith('/admin/audit-logs', {
      params: { cursor: 'cursor-2', limit: 20, action: 'workflow.deploy' },
    });
    expect(result).toEqual({ total: 0, items: [] });
  });
});

describe('adminApi.getAuditLogDetail', () => {
  it('audit log id로 상세를 조회한다', async () => {
    const detail = {
      id: 'log-1',
      action: 'workflow.deploy',
      audit_metadata: { request_id: 'req-1' },
    };
    mockedGet.mockResolvedValueOnce({ data: detail });

    const result = await adminApi.getAuditLogDetail('log-1');

    expect(mockedGet).toHaveBeenCalledWith('/admin/audit-logs/log-1');
    expect(result).toEqual(detail);
  });
});

describe('adminApi.listPermissionRequests', () => {
  it('status와 pagination으로 권한 신청 목록을 조회한다', async () => {
    mockedGet.mockResolvedValueOnce({ data: { total: 0, items: [] } });

    await adminApi.listPermissionRequests({
      status: 'pending',
      page: 1,
      limit: 20,
    });

    expect(mockedGet).toHaveBeenCalledWith('/admin/permission-requests', {
      params: { status: 'pending', page: 1, limit: 20 },
    });
  });
});

describe('adminApi permission request actions', () => {
  it('승인/거절 action endpoint를 호출한다', async () => {
    mockedPost.mockResolvedValue({ data: { id: 'req-1' } });

    await adminApi.approvePermissionRequest('req-1');
    await adminApi.rejectPermissionRequest('req-2');

    expect(mockedPost).toHaveBeenCalledWith(
      '/admin/permission-requests/req-1/approve',
    );
    expect(mockedPost).toHaveBeenCalledWith(
      '/admin/permission-requests/req-2/reject',
    );
  });
});

describe('adminApi.listAppCreationPermissions', () => {
  it('pagination으로 App 생성 권한 보유 목록을 조회한다', async () => {
    mockedGet.mockResolvedValueOnce({ data: { total: 0, items: [] } });

    const result = await adminApi.listAppCreationPermissions({
      page: 1,
      limit: 20,
    });

    expect(mockedGet).toHaveBeenCalledWith('/admin/app-creation-permissions', {
      params: { page: 1, limit: 20 },
    });
    expect(result).toEqual({ total: 0, items: [] });
  });
});

describe('adminApi.revokeAppCreationPermission', () => {
  it('회수 endpoint를 DELETE로 호출한다', async () => {
    mockedDelete.mockResolvedValueOnce({
      data: { id: 'perm-1', user_id: 'user-3' },
    });

    const result = await adminApi.revokeAppCreationPermission('perm-1');

    expect(mockedDelete).toHaveBeenCalledWith(
      '/admin/app-creation-permissions/perm-1',
    );
    expect(result).toEqual({ id: 'perm-1', user_id: 'user-3' });
  });
});

describe('adminApi.listWorkflowUsage', () => {
  it('기간 미지정 시 기간 파라미터 없이 조회한다', async () => {
    mockedGet.mockResolvedValueOnce({
      data: { total: 0, period: {}, items: [] },
    });

    await adminApi.listWorkflowUsage({ page: 1, limit: 20 });

    expect(mockedGet).toHaveBeenCalledWith('/admin/usage/workflows', {
      params: { page: 1, limit: 20 },
    });
  });
});

describe('adminApi.getOrganizationSummary', () => {
  it('조직 월간 요약을 조회한다', async () => {
    const summary = { month: '2026-07', total_cost: 1.5, budget: null };
    mockedGet.mockResolvedValueOnce({ data: summary });

    const result = await adminApi.getOrganizationSummary();

    expect(mockedGet).toHaveBeenCalledWith('/admin/summary');
    expect(result).toEqual(summary);
  });
});

describe('adminApi Security Alert queries', () => {
  it('빈 값을 제외한 필터와 pagination으로 보안 알림 목록을 조회한다', async () => {
    mockedGet.mockResolvedValueOnce({ data: { total: 0, items: [] } });

    const result = await adminApi.listSecurityAlerts({
      page: 2,
      limit: 20,
      severity: 'high',
      status: 'open',
      ruleId: 'repeated_permission_denied',
      actorId: 'actor-1',
      startAt: '2026-07-01T00:00:00+09:00',
      endAt: '',
    });

    expect(mockedGet).toHaveBeenCalledWith('/admin/security-alerts', {
      params: {
        page: 2,
        limit: 20,
        severity: 'high',
        status: 'open',
        ruleId: 'repeated_permission_denied',
        actorId: 'actor-1',
        startAt: '2026-07-01T00:00:00+09:00',
      },
    });
    expect(result).toEqual({ total: 0, items: [] });
  });

  it('Sidebar용 open alert 요약을 별도 endpoint에서 조회한다', async () => {
    const summary = { open_count: 2, high_open_count: 1, recent_items: [] };
    mockedGet.mockResolvedValueOnce({ data: summary });

    const result = await adminApi.getSecurityAlertSummary();

    expect(mockedGet).toHaveBeenCalledWith('/admin/security-alerts/summary');
    expect(result).toEqual(summary);
  });

  it('alert 상세와 연결된 safe audit evidence를 각각 조회한다', async () => {
    mockedGet
      .mockResolvedValueOnce({ data: { id: 'alert-1' } })
      .mockResolvedValueOnce({ data: { total: 1, items: [{ id: 'audit-1' }] } });

    await adminApi.getSecurityAlertDetail('alert-1');
    const evidence = await adminApi.listSecurityAlertAuditLogs('alert-1', {
      page: 2,
      limit: 10,
    });

    expect(mockedGet).toHaveBeenNthCalledWith(1, '/admin/security-alerts/alert-1');
    expect(mockedGet).toHaveBeenNthCalledWith(
      2,
      '/admin/security-alerts/alert-1/audit-logs',
      { params: { page: 2, limit: 10 } },
    );
    expect(evidence).toEqual({ total: 1, items: [{ id: 'audit-1' }] });
  });
});

describe('adminApi Security Alert lifecycle actions', () => {
  it('현재 lifecycle version으로 확인과 미확인 전환을 요청한다', async () => {
    mockedPost.mockResolvedValue({ data: { id: 'alert-1', version: 4 } });

    await adminApi.acknowledgeSecurityAlert('alert-1', 2);
    await adminApi.reopenSecurityAlert('alert-1', 3);

    expect(mockedPost).toHaveBeenCalledWith(
      '/admin/security-alerts/alert-1/acknowledge',
      { expected_version: 2 },
    );
    expect(mockedPost).toHaveBeenCalledWith(
      '/admin/security-alerts/alert-1/reopen',
      { expected_version: 3 },
    );
  });

  it('해결 결과와 사유를 canonical request field로 전송한다', async () => {
    mockedPost.mockResolvedValueOnce({
      data: { id: 'alert-1', status: 'resolved', version: 5 },
    });

    const result = await adminApi.resolveSecurityAlert('alert-1', {
      expectedVersion: 4,
      resolutionType: 'false_positive',
      reason: '확인 결과 정상적인 접근이었습니다.',
    });

    expect(mockedPost).toHaveBeenCalledWith(
      '/admin/security-alerts/alert-1/resolve',
      {
        expected_version: 4,
        resolution_type: 'false_positive',
        reason: '확인 결과 정상적인 접근이었습니다.',
      },
    );
    expect(result).toEqual({
      id: 'alert-1',
      status: 'resolved',
      version: 5,
    });
  });
});
