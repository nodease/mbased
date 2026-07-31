import { afterEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { AxiosError, type AxiosResponse } from 'axios';

vi.mock('../api/adminApi', () => ({
  adminApi: {
    getSecurityAlertDetail: vi.fn(),
    listSecurityAlertAuditLogs: vi.fn(),
    getAuditLogDetail: vi.fn(),
    acknowledgeSecurityAlert: vi.fn(),
    reopenSecurityAlert: vi.fn(),
    resolveSecurityAlert: vi.fn(),
  },
}));

import { adminApi } from '../api/adminApi';
import { SecurityAlertDetailDrawer } from './SecurityAlertDetailDrawer';
import type { OrganizationMember } from '../../organization/types/Organization';
import type { SecurityAlertAuditLogItem, SecurityAlertAuditLogListResponse } from '../types/SecurityAlert';

const mockedDetail = vi.mocked(adminApi.getSecurityAlertDetail);
const mockedEvidence = vi.mocked(adminApi.listSecurityAlertAuditLogs);
const mockedAuditDetail = vi.mocked(adminApi.getAuditLogDetail);
const mockedAcknowledge = vi.mocked(adminApi.acknowledgeSecurityAlert);
const mockedReopen = vi.mocked(adminApi.reopenSecurityAlert);
const mockedResolve = vi.mocked(adminApi.resolveSecurityAlert);

const deferred = <T,>() => {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((resolvePromise) => {
    resolve = resolvePromise;
  });
  return { promise, resolve };
};

const members = [
  {
    id: 'membership-1',
    user_id: 'user-1',
    user_name: '김사용자',
    user_email: 'secret@example.com',
    membership_state: 'active',
    organization_auth_state: 'member',
  },
] as unknown as OrganizationMember[];

const detail = {
  id: '123e4567-e89b-42d3-a456-426614174000',
  organization_id: 'org-1',
  rule_id: 'repeated_policy_block' as const,
  rule_version: 'v1',
  severity: 'high' as const,
  status: 'resolved' as const,
  policy_reason: 'rag.pii_evidence_detected',
  actor: { id: 'user-1', display_name: '김사용자', state: 'active' as const },
  occurrence_count: 3,
  evidence_count: 1,
  first_detected_at: '2026-07-13T00:00:00Z',
  last_detected_at: '2026-07-13T00:05:00Z',
  version: 3,
  acknowledged: {
    by: { id: 'manager-1', display_name: '김관리', state: 'active' as const },
    at: '2026-07-13T00:06:00Z',
  },
  resolution: {
    type: 'false_positive' as const,
    reason: '정상적인 테스트 요청으로 확인했습니다.',
    by: { id: 'manager-1', display_name: '김관리', state: 'active' as const },
    at: '2026-07-13T00:10:00Z',
  },
  created_at: '2026-07-13T00:05:00Z',
  updated_at: '2026-07-13T00:10:00Z',
};

const evidence = {
  id: 'audit-1',
  occurred_at: '2026-07-13T00:04:00Z',
  actor_id: 'user-1',
  actor_type: 'user',
  category: 'authorization',
  action: 'permission.denied',
  target_type: 'workflow',
  target_id: 'wf-safe-1',
  status: 'failure' as const,
  request_id: 'request-safe-1',
  required_permission: 'security_alert.manage',
  requested_operation: 'security_alert.list',
  denial_reason: 'organization_manager_required',
} satisfies SecurityAlertAuditLogItem;

const notFoundError = () => {
  const error = new AxiosError('raw server detail');
  error.response = { status: 404 } as AxiosResponse;
  return error;
};

const conflictError = () => {
  const error = new AxiosError('raw conflict');
  error.response = { status: 409 } as AxiosResponse;
  return error;
};

const forbiddenError = () => {
  const error = new AxiosError('raw forbidden');
  error.response = { status: 403 } as AxiosResponse;
  return error;
};

const retryableError = () => {
  const error = new AxiosError('raw server detail');
  error.response = { status: 500 } as AxiosResponse;
  return error;
};

const openDetail = {
  ...detail,
  status: 'open' as const,
  version: 1,
  acknowledged: null,
  resolution: null,
};

const acknowledgedDetail = {
  ...openDetail,
  status: 'acknowledged' as const,
  version: 2,
  acknowledged: detail.acknowledged,
};

afterEach(() => {
  vi.clearAllMocks();
});

describe('SecurityAlertDetailDrawer', () => {
  it('상세와 safe evidence를 사용자용 라벨로 표시한다', async () => {
    mockedDetail.mockResolvedValue(detail);
    mockedEvidence.mockResolvedValue({ total: 1, items: [evidence] });

    render(
      <SecurityAlertDetailDrawer
        alertId={detail.id}
        members={members}
        onClose={vi.fn()}
        onNotFound={vi.fn()}
        onManageActor={vi.fn()}
      />,
    );

    const drawer = await screen.findByRole('dialog', { name: '보안 알림 상세' });
    expect(drawer).toHaveClass(
      'max-w-4xl',
      '[&_.text-xs]:text-sm',
      '[&_.text-sm]:text-base',
      '[&_.text-base]:text-lg',
      '[&_.text-lg]:text-xl',
    );
    expect(await within(drawer).findByText('반복된 정책 차단')).toBeInTheDocument();
    expect(within(drawer).getByText('개인정보 포함 근거 감지')).toBeInTheDocument();
    expect(within(drawer).getByText('오탐')).toBeInTheDocument();
    expect(within(drawer).getByText('정상적인 테스트 요청으로 확인했습니다.')).toBeInTheDocument();
    expect(within(drawer).queryByText('rag.pii_evidence_detected')).not.toBeInTheDocument();
    expect(within(drawer).queryByText('secret@example.com')).not.toBeInTheDocument();

    const evidenceTable = await within(drawer).findByRole('table');
    expect(within(evidenceTable).getByText('permission.denied')).toBeInTheDocument();
    expect(
      within(evidenceTable).getByText(
        '보안 알림 목록 조회를 시도했지만 조직 관리자 권한이 필요해 거부되었습니다.',
      ),
    ).toBeInTheDocument();
    expect(within(evidenceTable).getByText('접근 거부')).toBeInTheDocument();
    expect(mockedDetail).toHaveBeenCalledWith(detail.id);
    expect(mockedEvidence).toHaveBeenCalledWith(detail.id, { page: 1, limit: 20 });
  });

  it('evidence 실패는 상세를 유지하고 evidence만 다시 시도한다', async () => {
    mockedDetail.mockResolvedValue(detail);
    mockedEvidence
      .mockRejectedValueOnce(new Error('network'))
      .mockResolvedValueOnce({ total: 0, items: [] });

    render(
      <SecurityAlertDetailDrawer
        alertId={detail.id}
        members={members}
        onClose={vi.fn()}
        onNotFound={vi.fn()}
      />,
    );

    expect(await screen.findByText('반복된 정책 차단')).toBeInTheDocument();
    expect(
      await screen.findByText('연결된 감사 기록을 불러오지 못했습니다.'),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '감사 기록 다시 시도' }));

    expect(
      await screen.findByText('연결된 감사 기록이 없습니다.'),
    ).toBeInTheDocument();
    expect(screen.getByText('반복된 정책 차단')).toBeInTheDocument();
    expect(mockedDetail).toHaveBeenCalledTimes(1);
    expect(mockedEvidence).toHaveBeenCalledTimes(2);
  });

  it('evidence pagination은 alert 상세을 다시 조회하지 않는다', async () => {
    mockedDetail.mockResolvedValue(detail);
    mockedEvidence.mockResolvedValue({ total: 21, items: [evidence] });

    render(
      <SecurityAlertDetailDrawer
        alertId={detail.id}
        members={members}
        onClose={vi.fn()}
        onNotFound={vi.fn()}
      />,
    );

    const drawer = await screen.findByRole('dialog', { name: '보안 알림 상세' });
    fireEvent.click(await within(drawer).findByRole('button', { name: '다음' }));

    await waitFor(() =>
      expect(mockedEvidence).toHaveBeenLastCalledWith(detail.id, {
        page: 2,
        limit: 20,
      }),
    );
    expect(mockedDetail).toHaveBeenCalledTimes(1);
  });

  it('refresh token이 바뀌면 상세와 현재 evidence page를 영속 API에서 다시 조회한다', async () => {
    mockedDetail.mockResolvedValue(detail);
    mockedEvidence.mockResolvedValue({ total: 21, items: [evidence] });

    const { rerender } = render(
      <SecurityAlertDetailDrawer
        alertId={detail.id}
        members={members}
        onClose={vi.fn()}
        onNotFound={vi.fn()}
        refreshToken={0}
      />,
    );

    const drawer = await screen.findByRole('dialog', { name: '보안 알림 상세' });
    fireEvent.click(await within(drawer).findByRole('button', { name: '다음' }));
    await waitFor(() => expect(mockedEvidence).toHaveBeenCalledTimes(2));

    rerender(
      <SecurityAlertDetailDrawer
        alertId={detail.id}
        members={members}
        onClose={vi.fn()}
        onNotFound={vi.fn()}
        refreshToken={1}
      />,
    );

    await waitFor(() => expect(mockedDetail).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(mockedEvidence).toHaveBeenCalledTimes(3));
    expect(mockedEvidence).toHaveBeenLastCalledWith(detail.id, {
      page: 2,
      limit: 20,
    });
  });

  it('refresh token background refresh 중 기존 상세와 evidence를 유지한다', async () => {
    const detailRefreshRequest = deferred<typeof detail>();
    const evidenceRefreshRequest = deferred<SecurityAlertAuditLogListResponse>();
    mockedDetail
      .mockResolvedValueOnce(detail)
      .mockReturnValueOnce(detailRefreshRequest.promise);
    mockedEvidence
      .mockResolvedValueOnce({ total: 1, items: [evidence] })
      .mockReturnValueOnce(evidenceRefreshRequest.promise);

    const { rerender } = render(
      <SecurityAlertDetailDrawer
        alertId={detail.id}
        members={members}
        onClose={vi.fn()}
        onNotFound={vi.fn()}
        refreshToken={0}
      />,
    );
    expect(await screen.findByText('반복된 정책 차단')).toBeInTheDocument();
    expect(await screen.findByText('permission.denied')).toBeInTheDocument();

    rerender(
      <SecurityAlertDetailDrawer
        alertId={detail.id}
        members={members}
        onClose={vi.fn()}
        onNotFound={vi.fn()}
        refreshToken={1}
      />,
    );
    await waitFor(() => expect(mockedDetail).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(mockedEvidence).toHaveBeenCalledTimes(2));

    expect(screen.getByText('반복된 정책 차단')).toBeInTheDocument();
    expect(screen.getByText('permission.denied')).toBeInTheDocument();
    expect(
      screen.queryByText('보안 알림 상세를 불러오는 중...'),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByText('연결된 감사 기록을 불러오는 중...'),
    ).not.toBeInTheDocument();

    detailRefreshRequest.resolve({ ...detail, occurrence_count: 4 });
    evidenceRefreshRequest.resolve({ total: 1, items: [evidence] });
    expect(await screen.findByText('4')).toBeInTheDocument();
  });

  it('refresh token background refresh 실패 후에도 기존 상세와 evidence를 유지한다', async () => {
    mockedDetail
      .mockResolvedValueOnce(detail)
      .mockRejectedValueOnce(new Error('temporary detail failure'));
    mockedEvidence
      .mockResolvedValueOnce({ total: 1, items: [evidence] })
      .mockRejectedValueOnce(new Error('temporary evidence failure'));

    const { rerender } = render(
      <SecurityAlertDetailDrawer
        alertId={detail.id}
        members={members}
        onClose={vi.fn()}
        onNotFound={vi.fn()}
        refreshToken={0}
      />,
    );
    expect(await screen.findByText('반복된 정책 차단')).toBeInTheDocument();
    expect(await screen.findByText('permission.denied')).toBeInTheDocument();

    rerender(
      <SecurityAlertDetailDrawer
        alertId={detail.id}
        members={members}
        onClose={vi.fn()}
        onNotFound={vi.fn()}
        refreshToken={1}
      />,
    );

    await waitFor(() => expect(mockedDetail).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(mockedEvidence).toHaveBeenCalledTimes(2));
    await waitFor(() =>
      expect(
        screen.queryByText('보안 알림 상세를 불러오지 못했습니다.'),
      ).not.toBeInTheDocument(),
    );
    expect(
      screen.queryByText('연결된 감사 기록을 불러오지 못했습니다.'),
    ).not.toBeInTheDocument();
    expect(screen.getByText('반복된 정책 차단')).toBeInTheDocument();
    expect(screen.getByText('permission.denied')).toBeInTheDocument();
  });

  it('alert 전환 전에 시작한 늦은 evidence 응답은 현재 alert를 덮어쓰지 않는다', async () => {
    const nextAlertId = '223e4567-e89b-42d3-a456-426614174000';
    const oldAlertRequest = deferred<SecurityAlertAuditLogListResponse>();
    const currentAlertRequest = deferred<SecurityAlertAuditLogListResponse>();
    const currentEvidence = {
      ...evidence,
      id: 'audit-2',
      request_id: 'request-current',
      action: 'policy.block',
      required_permission: null,
      requested_operation: null,
      denial_reason: null,
    };
    mockedDetail.mockImplementation(async (alertId) => ({
      ...detail,
      id: alertId,
    }));
    mockedEvidence
      .mockReturnValueOnce(oldAlertRequest.promise)
      .mockReturnValueOnce(currentAlertRequest.promise);

    const { rerender } = render(
      <SecurityAlertDetailDrawer
        alertId={detail.id}
        members={members}
        onClose={vi.fn()}
        onNotFound={vi.fn()}
      />,
    );
    await waitFor(() => expect(mockedEvidence).toHaveBeenCalledTimes(1));

    rerender(
      <SecurityAlertDetailDrawer
        alertId={nextAlertId}
        members={members}
        onClose={vi.fn()}
        onNotFound={vi.fn()}
      />,
    );
    await waitFor(() => expect(mockedEvidence).toHaveBeenCalledTimes(2));

    currentAlertRequest.resolve({ total: 1, items: [currentEvidence] });
    expect(await screen.findByText('policy.block')).toBeInTheDocument();

    oldAlertRequest.resolve({ total: 1, items: [evidence] });
    await waitFor(() =>
      expect(screen.getByText('policy.block')).toBeInTheDocument(),
    );
    expect(screen.queryByText('permission.denied')).not.toBeInTheDocument();
  });

  it('evidence 상세 보기는 기존 Audit detail 경로를 연다', async () => {
    mockedDetail.mockResolvedValue(detail);
    mockedEvidence.mockResolvedValue({ total: 1, items: [evidence] });
    mockedAuditDetail.mockResolvedValue({
      ...evidence,
      audit_metadata: {},
      change_summary: null,
    });

    render(
      <SecurityAlertDetailDrawer
        alertId={detail.id}
        members={members}
        onClose={vi.fn()}
        onNotFound={vi.fn()}
      />,
    );

    const alertDrawer = await screen.findByRole('dialog', {
      name: '보안 알림 상세',
    });
    const auditTrigger = await screen.findByRole('button', {
      name: '감사 기록 상세 보기',
    });
    fireEvent.click(auditTrigger);
    const auditDrawer = await screen.findByRole('dialog', {
      name: '감사 로그 상세',
    });

    expect(alertDrawer.parentElement).toHaveClass('z-[130]');
    expect(auditDrawer.parentElement).toHaveClass('z-[140]');
    await waitFor(() =>
      expect(mockedAuditDetail).toHaveBeenCalledWith(evidence.id),
    );
    fireEvent.click(within(auditDrawer).getByRole('button', { name: '닫기' }));
    await waitFor(() => expect(auditTrigger).toHaveFocus());
  });

  it('관리 가능한 actor는 접근 관리로 넘길 수 있다', async () => {
    mockedDetail.mockResolvedValue(detail);
    mockedEvidence.mockResolvedValue({ total: 0, items: [] });
    const onManageActor = vi.fn();

    render(
      <SecurityAlertDetailDrawer
        alertId={detail.id}
        members={members}
        onClose={vi.fn()}
        onNotFound={vi.fn()}
        onManageActor={onManageActor}
      />,
    );

    fireEvent.click(await screen.findByRole('button', { name: '사용자 접근 관리' }));
    expect(onManageActor).toHaveBeenCalledWith('user-1');
  });

  it('404는 raw 오류를 노출하지 않고 safe not-found 처리를 요청한다', async () => {
    mockedDetail.mockRejectedValue(notFoundError());
    mockedEvidence.mockResolvedValue({ total: 0, items: [] });
    const onNotFound = vi.fn();

    render(
      <SecurityAlertDetailDrawer
        alertId={detail.id}
        members={members}
        onClose={vi.fn()}
        onNotFound={onNotFound}
      />,
    );

    await waitFor(() => expect(onNotFound).toHaveBeenCalledTimes(1));
    expect(screen.queryByText('raw server detail')).not.toBeInTheDocument();
  });

  it('open alert 확인은 현재 version을 전송하고 응답으로 상세를 갱신한다', async () => {
    mockedDetail.mockResolvedValue(openDetail);
    mockedEvidence.mockResolvedValue({ total: 0, items: [] });
    mockedAcknowledge.mockResolvedValue(acknowledgedDetail);
    const onChanged = vi.fn();

    render(
      <SecurityAlertDetailDrawer
        alertId={detail.id}
        members={members}
        onClose={vi.fn()}
        onNotFound={vi.fn()}
        onChanged={onChanged}
      />,
    );

    fireEvent.click(await screen.findByRole('button', { name: '확인' }));

    await waitFor(() =>
      expect(mockedAcknowledge).toHaveBeenCalledWith(detail.id, 1),
    );
    expect(await screen.findByText('확인됨')).toBeInTheDocument();
    expect(onChanged).toHaveBeenCalledWith(acknowledgedDetail);
  });

  it('acknowledged alert를 현재 version으로 미확인 상태로 되돌린다', async () => {
    mockedDetail.mockResolvedValue(acknowledgedDetail);
    mockedEvidence.mockResolvedValue({ total: 0, items: [] });
    mockedReopen.mockResolvedValue({ ...openDetail, version: 3 });

    render(
      <SecurityAlertDetailDrawer
        alertId={detail.id}
        members={members}
        onClose={vi.fn()}
        onNotFound={vi.fn()}
      />,
    );

    fireEvent.click(
      await screen.findByRole('button', { name: '미확인으로 되돌리기' }),
    );

    await waitFor(() =>
      expect(mockedReopen).toHaveBeenCalledWith(detail.id, 2),
    );
    expect(await screen.findByText('미확인')).toBeInTheDocument();
  });

  it('resolve dialog는 필수값을 검사하고 canonical payload를 전송한다', async () => {
    mockedDetail.mockResolvedValue(openDetail);
    mockedEvidence.mockResolvedValue({ total: 0, items: [] });
    mockedResolve.mockResolvedValue(detail);

    render(
      <SecurityAlertDetailDrawer
        alertId={detail.id}
        members={members}
        onClose={vi.fn()}
        onNotFound={vi.fn()}
      />,
    );

    fireEvent.click(await screen.findByRole('button', { name: '해결' }));
    const dialog = screen.getByRole('dialog', { name: '보안 알림 해결' });
    fireEvent.click(within(dialog).getByRole('button', { name: '해결 확정' }));
    expect(within(dialog).getByText('처리 결과를 선택해 주세요.')).toBeInTheDocument();

    fireEvent.change(within(dialog).getByLabelText('처리 결과'), {
      target: { value: 'false_positive' },
    });
    fireEvent.change(within(dialog).getByLabelText('처리 사유'), {
      target: { value: '  정상 요청으로 확인했습니다.  ' },
    });
    fireEvent.click(within(dialog).getByRole('button', { name: '해결 확정' }));

    await waitFor(() =>
      expect(mockedResolve).toHaveBeenCalledWith(detail.id, {
        expectedVersion: 1,
        resolutionType: 'false_positive',
        reason: '정상 요청으로 확인했습니다.',
      }),
    );
    await waitFor(() =>
      expect(
        screen.queryByRole('dialog', { name: '보안 알림 해결' }),
      ).not.toBeInTheDocument(),
    );
    expect(await screen.findByText('해결됨')).toBeInTheDocument();
  });

  it('resolve reason의 길이와 금지 control character를 client에서 차단한다', async () => {
    mockedDetail.mockResolvedValue(openDetail);
    mockedEvidence.mockResolvedValue({ total: 0, items: [] });

    render(
      <SecurityAlertDetailDrawer
        alertId={detail.id}
        members={members}
        onClose={vi.fn()}
        onNotFound={vi.fn()}
      />,
    );

    fireEvent.click(await screen.findByRole('button', { name: '해결' }));
    const dialog = screen.getByRole('dialog', { name: '보안 알림 해결' });
    fireEvent.change(within(dialog).getByLabelText('처리 결과'), {
      target: { value: 'mitigated' },
    });
    fireEvent.change(within(dialog).getByLabelText('처리 사유'), {
      target: { value: '😀'.repeat(501) },
    });
    fireEvent.click(within(dialog).getByRole('button', { name: '해결 확정' }));
    expect(within(dialog).getByText('처리 사유는 500자 이하여야 합니다.')).toBeInTheDocument();

    fireEvent.change(within(dialog).getByLabelText('처리 사유'), {
      target: { value: '검토\u0000완료' },
    });
    fireEvent.click(within(dialog).getByRole('button', { name: '해결 확정' }));
    expect(within(dialog).getByText('처리 사유에 허용되지 않는 문자가 있습니다.')).toBeInTheDocument();
    expect(mockedResolve).not.toHaveBeenCalled();
  });

  it('409 stale_state는 최신 상세을 다시 조회하고 safe 안내를 표시한다', async () => {
    mockedDetail
      .mockResolvedValueOnce(openDetail)
      .mockResolvedValueOnce(acknowledgedDetail);
    mockedEvidence.mockResolvedValue({ total: 0, items: [] });
    mockedAcknowledge.mockRejectedValue(conflictError());

    render(
      <SecurityAlertDetailDrawer
        alertId={detail.id}
        members={members}
        onClose={vi.fn()}
        onNotFound={vi.fn()}
      />,
    );

    fireEvent.click(await screen.findByRole('button', { name: '확인' }));

    expect(
      await screen.findByText('다른 관리자가 상태를 변경했습니다.'),
    ).toBeInTheDocument();
    expect(await screen.findByText('확인됨')).toBeInTheDocument();
    expect(mockedDetail).toHaveBeenCalledTimes(2);
    expect(screen.queryByText('raw conflict')).not.toBeInTheDocument();
  });

  it('409 이후 최신 상세 재조회가 실패하면 stale 상세와 작업 버튼을 제거한다', async () => {
    mockedDetail
      .mockResolvedValueOnce(openDetail)
      .mockRejectedValueOnce(retryableError());
    mockedEvidence.mockResolvedValue({ total: 0, items: [] });
    mockedAcknowledge.mockRejectedValue(conflictError());

    render(
      <SecurityAlertDetailDrawer
        alertId={detail.id}
        members={members}
        onClose={vi.fn()}
        onNotFound={vi.fn()}
      />,
    );

    fireEvent.click(await screen.findByRole('button', { name: '확인' }));

    expect(
      await screen.findByText('보안 알림 상세를 불러오지 못했습니다.'),
    ).toBeInTheDocument();
    expect(mockedDetail).toHaveBeenCalledTimes(2);
    expect(
      screen.queryByRole('button', { name: '확인' }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByText('다른 관리자가 상태를 변경했습니다.'),
    ).not.toBeInTheDocument();
    expect(screen.queryByText('raw conflict')).not.toBeInTheDocument();
    expect(screen.queryByText('raw server detail')).not.toBeInTheDocument();
  });

  it('확인 요청이 403이면 cached detail과 drawer URL을 닫는다', async () => {
    mockedDetail.mockResolvedValue(openDetail);
    mockedEvidence.mockResolvedValue({ total: 0, items: [] });
    mockedAcknowledge.mockRejectedValue(forbiddenError());
    const onClose = vi.fn();

    render(
      <SecurityAlertDetailDrawer
        alertId={detail.id}
        members={members}
        onClose={onClose}
        onNotFound={vi.fn()}
      />,
    );

    fireEvent.click(await screen.findByRole('button', { name: '확인' }));

    await waitFor(() => expect(onClose).toHaveBeenCalledTimes(1));
    expect(screen.queryByRole('button', { name: '확인' })).not.toBeInTheDocument();
    expect(screen.queryByText('raw forbidden')).not.toBeInTheDocument();
  });

  it('미확인 전환 요청이 403이면 cached detail과 drawer URL을 닫는다', async () => {
    mockedDetail.mockResolvedValue(acknowledgedDetail);
    mockedEvidence.mockResolvedValue({ total: 0, items: [] });
    mockedReopen.mockRejectedValue(forbiddenError());
    const onClose = vi.fn();

    render(
      <SecurityAlertDetailDrawer
        alertId={detail.id}
        members={members}
        onClose={onClose}
        onNotFound={vi.fn()}
      />,
    );

    fireEvent.click(
      await screen.findByRole('button', { name: '미확인으로 되돌리기' }),
    );

    await waitFor(() => expect(onClose).toHaveBeenCalledTimes(1));
    expect(
      screen.queryByRole('button', { name: '미확인으로 되돌리기' }),
    ).not.toBeInTheDocument();
  });

  it('해결 요청이 403이면 dialog, cached detail과 drawer URL을 닫는다', async () => {
    mockedDetail.mockResolvedValue(openDetail);
    mockedEvidence.mockResolvedValue({ total: 0, items: [] });
    mockedResolve.mockRejectedValue(forbiddenError());
    const onClose = vi.fn();

    render(
      <SecurityAlertDetailDrawer
        alertId={detail.id}
        members={members}
        onClose={onClose}
        onNotFound={vi.fn()}
      />,
    );

    fireEvent.click(await screen.findByRole('button', { name: '해결' }));
    const dialog = screen.getByRole('dialog', { name: '보안 알림 해결' });
    fireEvent.change(within(dialog).getByLabelText('처리 결과'), {
      target: { value: 'mitigated' },
    });
    fireEvent.change(within(dialog).getByLabelText('처리 사유'), {
      target: { value: '조치 완료' },
    });
    fireEvent.click(within(dialog).getByRole('button', { name: '해결 확정' }));

    await waitFor(() => expect(onClose).toHaveBeenCalledTimes(1));
    expect(
      screen.queryByRole('dialog', { name: '보안 알림 해결' }),
    ).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '해결' })).not.toBeInTheDocument();
  });
});
