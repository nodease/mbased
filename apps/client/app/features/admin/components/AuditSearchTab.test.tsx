import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react';
import { AxiosError, type AxiosResponse } from 'axios';

vi.mock('../api/adminApi', () => ({
  adminApi: {
    listAuditLogs: vi.fn(),
    getAuditLogDetail: vi.fn(),
  },
}));

vi.mock('./ActorAccessDrawer', () => ({
  ActorAccessDrawer: ({ onClose }: { onClose: () => void }) => (
    <div role="dialog" aria-label="행위자 접근 관리">
      <button onClick={onClose}>행위자 관리 닫기</button>
    </div>
  ),
}));

import { adminApi } from '../api/adminApi';
import { AuditSearchTab } from './AuditSearchTab';
import type { OrganizationMember } from '../../organization/types/Organization';

const mockedList = vi.mocked(adminApi.listAuditLogs);
const mockedDetail = vi.mocked(adminApi.getAuditLogDetail);

const members = [
  {
    id: 'membership-1',
    user_id: 'user-1',
    user_name: '김관리',
    user_email: 'admin@example.com',
    membership_state: 'active',
    organization_auth_state: 'manager',
  },
] as unknown as OrganizationMember[];

const auditItem = {
  id: 'log-1',
  occurred_at: '2026-07-01T00:30:00+09:00',
  actor_id: 'user-1',
  actor_type: 'user',
  category: 'workflow',
  action: 'workflow.deploy',
  target_type: 'workflow',
  target_id: 'wf-1',
  status: 'success' as const,
  request_id: 'req-1',
};

const accessAuditItem = {
  ...auditItem,
  id: 'log-access-1',
  action: 'organization.member.update',
  target_type: 'organization_membership',
  target_id: 'membership-1',
};

const projectedAuditItem = {
  ...auditItem,
  id: 'log-projected-1',
  actor_id: '11111111-1111-4111-8111-111111111111',
  actor_display: {
    label: '감사 당시 이름 (historical@example.com)',
    source: 'event_snapshot' as const,
  },
  target_id: '22222222-2222-4222-8222-222222222222',
  target_display: {
    label: '고객문의 봇',
    source: 'current_resource' as const,
  },
};

const deletedActorAuditItem = {
  ...projectedAuditItem,
  id: 'log-deleted-actor-1',
  actor_id: null,
};

const forbiddenError = () => {
  const error = new AxiosError('Forbidden');
  error.response = { status: 403 } as AxiosResponse;
  return error;
};

afterEach(() => {
  vi.clearAllMocks();
});

describe('AuditSearchTab', () => {
  it('감사 로그 목록을 canonical action, 파생 라벨, 상태와 함께 렌더링한다', async () => {
    mockedList.mockResolvedValue({ total: 1, items: [auditItem] });

    render(<AuditSearchTab members={members} />);

    expect(await screen.findByText('workflow.deploy')).toBeInTheDocument();
    const table = screen.getByRole('table');
    expect(within(table).getByText('Workflow 배포')).toBeInTheDocument();
    expect(within(table).getByText('성공')).toBeInTheDocument();
    expect(within(table).getByText('Workflow · wf-1')).toBeInTheDocument();
    expect(
      within(table).getByText('김관리 (admin@example.com)'),
    ).toBeInTheDocument();
    // timestamp는 <time datetime>에 ISO 값을 유지한다.
    const time = screen.getByText(
      new Date(auditItem.occurred_at).toLocaleString(),
    );
    expect(time.closest('time')).toHaveAttribute(
      'dateTime',
      auditItem.occurred_at,
    );
    expect(mockedList).toHaveBeenCalledWith({ limit: 20 });
  });

  it('safe 표시명을 먼저 보여주고 canonical UUID와 복사 버튼을 병기한다', async () => {
    mockedList.mockResolvedValue({ total: 1, items: [projectedAuditItem] });

    render(<AuditSearchTab members={members} />);

    const table = await screen.findByRole('table');
    expect(
      within(table).getByText('감사 당시 이름 (historical@example.com)'),
    ).toBeInTheDocument();
    expect(within(table).getByText('고객문의 봇')).toBeInTheDocument();
    expect(
      within(table).getByText('11111111-1111-4111-8111-111111111111'),
    ).toBeInTheDocument();
    expect(
      within(table).getByText('22222222-2222-4222-8222-222222222222'),
    ).toBeInTheDocument();
    expect(
      within(table).getByRole('button', { name: '행위자 ID 복사' }),
    ).toBeInTheDocument();
    expect(
      within(table).getByRole('button', { name: '대상 ID 복사' }),
    ).toBeInTheDocument();
  });

  it('actor ID가 삭제된 감사 로그는 스냅샷 이름만 목록과 상세에 표시한다', async () => {
    mockedList.mockResolvedValue({ total: 1, items: [deletedActorAuditItem] });
    mockedDetail.mockResolvedValue({
      ...deletedActorAuditItem,
      audit_metadata: {},
    });

    render(<AuditSearchTab members={members} />);

    const table = await screen.findByRole('table');
    expect(
      within(table).getByText('감사 당시 이름 (historical@example.com)'),
    ).toBeInTheDocument();
    expect(
      within(table).queryByRole('button', { name: '행위자 ID 복사' }),
    ).not.toBeInTheDocument();

    fireEvent.click(within(table).getByText('workflow.deploy'));

    const drawer = await screen.findByRole('dialog', {
      name: '감사 로그 상세',
    });
    expect(drawer).toHaveClass(
      'max-w-xl',
      '[&_.text-xs]:text-sm',
      '[&_.text-sm]:text-base',
      '[&_.text-base]:text-lg',
      '[&_.text-lg]:text-xl',
    );
    expect(
      within(drawer).getByText('감사 당시 이름 (historical@example.com)'),
    ).toBeInTheDocument();
    expect(
      within(drawer).queryByRole('button', { name: '행위자 ID 복사' }),
    ).not.toBeInTheDocument();
  });

  it('검색 결과가 없으면 빈 목록 안내를 표시한다', async () => {
    mockedList.mockResolvedValue({ total: 0, items: [] });

    render(<AuditSearchTab members={members} />);

    expect(
      await screen.findByText('조건에 맞는 감사 로그가 없습니다.'),
    ).toBeInTheDocument();
  });

  it('403 응답이면 접근 권한 안내 문구를 표시한다', async () => {
    mockedList.mockRejectedValue(forbiddenError());

    render(<AuditSearchTab members={members} />);

    expect(
      await screen.findByText(/감사 로그 조회 권한이 없습니다/),
    ).toBeInTheDocument();
  });

  it('조회 버튼은 입력한 필터로 1페이지부터 재조회한다', async () => {
    mockedList.mockResolvedValue({ total: 0, items: [] });

    render(<AuditSearchTab members={members} />);
    await screen.findByText('조건에 맞는 감사 로그가 없습니다.');

    fireEvent.change(screen.getByLabelText('action 필터'), {
      target: { value: 'workflow.deploy' },
    });
    fireEvent.change(screen.getByLabelText('status 필터'), {
      target: { value: 'failure' },
    });
    fireEvent.click(screen.getByRole('button', { name: /조회/ }));

    await waitFor(() =>
      expect(mockedList).toHaveBeenLastCalledWith({
        action: 'workflow.deploy',
        status: 'failure',
        limit: 20,
      }),
    );
  });

  it('다음 cursor를 저장하고 이전 페이지 cursor를 재사용한다', async () => {
    const secondPageItem = {
      ...auditItem,
      id: 'audit-2',
      action: 'workflow.delete',
    };
    mockedList
      .mockResolvedValueOnce({
        total: 21,
        next_cursor: 'cursor-2',
        items: [auditItem],
      })
      .mockResolvedValueOnce({
        total: null,
        next_cursor: null,
        items: [secondPageItem],
      })
      .mockResolvedValueOnce({
        total: 21,
        next_cursor: 'cursor-2',
        items: [auditItem],
      });

    render(<AuditSearchTab members={members} />);
    await screen.findByText('workflow.deploy');

    fireEvent.click(screen.getByRole('button', { name: '다음' }));
    await waitFor(() =>
      expect(mockedList).toHaveBeenLastCalledWith({
        cursor: 'cursor-2',
        limit: 20,
      }),
    );
    expect(await screen.findByText('workflow.delete')).toBeInTheDocument();
    expect(screen.getByText('21개 중 page 2/2')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '이전' }));
    await waitFor(() =>
      expect(mockedList).toHaveBeenLastCalledWith({ limit: 20 }),
    );
  });

  it('행을 클릭하면 상세 드로어가 열리고 allowlist metadata를 표시한다', async () => {
    mockedList.mockResolvedValue({ total: 1, items: [accessAuditItem] });
    mockedDetail.mockResolvedValue({
      ...accessAuditItem,
      audit_metadata: { request_id: 'req-1', reason: '데모 배포' },
      change_summary: {
        before: { membership_state: 'active' },
        after: { membership_state: 'suspended' },
      },
    });

    render(<AuditSearchTab members={members} />);

    fireEvent.click(await screen.findByText('organization.member.update'));

    const drawer = await screen.findByRole('dialog', {
      name: '감사 로그 상세',
    });
    expect(drawer).toBeInTheDocument();
    expect(mockedDetail).toHaveBeenCalledWith('log-access-1');
    expect(
      await screen.findByText(
        '조직 멤버 · membership-1에서 멤버 정보 변경 작업이 완료되었습니다.',
      ),
    ).toBeInTheDocument();
    expect(await screen.findByText('데모 배포')).toBeInTheDocument();
    expect(screen.getByText('req-1')).toBeInTheDocument();
    expect(screen.getByText('변경 요약')).toBeInTheDocument();
    expect(screen.getByText('suspended')).toBeInTheDocument();

    // ESC로 닫힌다.
    fireEvent.keyDown(drawer, { key: 'Escape' });
    await waitFor(() =>
      expect(
        screen.queryByRole('dialog', { name: '감사 로그 상세' }),
      ).not.toBeInTheDocument(),
    );
  });

  it('상세 metadata와 변경 요약 UUID에도 안전하게 resolve된 이름을 병기한다', async () => {
    const organizationId = '33333333-3333-4333-8333-333333333333';
    mockedList.mockResolvedValue({ total: 1, items: [projectedAuditItem] });
    mockedDetail.mockResolvedValue({
      ...projectedAuditItem,
      audit_metadata: { organization_id: organizationId },
      change_summary: {
        before: { organization_id: organizationId },
        after: { organization_id: organizationId },
      },
      resolved_references: {
        [organizationId]: {
          label: 'Nodease 개발팀',
          source: 'current_resource',
        },
      },
    });

    render(<AuditSearchTab members={members} />);

    fireEvent.click(await screen.findByText('workflow.deploy'));

    const drawer = await screen.findByRole('dialog', {
      name: '감사 로그 상세',
    });
    expect(
      within(drawer).getByText('감사 당시 이름 (historical@example.com)'),
    ).toBeInTheDocument();
    expect(within(drawer).getByText('고객문의 봇')).toBeInTheDocument();
    expect(within(drawer).getAllByText('Nodease 개발팀')).toHaveLength(3);
    expect(within(drawer).getAllByText(organizationId)).toHaveLength(3);
  });

  it('행위자 관리 버튼은 row 상세 클릭과 분리되고 닫은 뒤 focus를 돌려준다', async () => {
    mockedList.mockResolvedValue({ total: 1, items: [auditItem] });

    render(
      <AuditSearchTab
        members={members}
        organizationId="org-1"
        canManageActors
      />,
    );

    const actorButton = await screen.findByRole('button', {
      name: '김관리 접근 관리',
    });
    fireEvent.click(actorButton);

    expect(
      screen.getByRole('dialog', { name: '행위자 접근 관리' }),
    ).toBeInTheDocument();
    expect(mockedDetail).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole('button', { name: '행위자 관리 닫기' }));
    await waitFor(() => expect(actorButton).toHaveFocus());
  });

  it('system actor는 current member와 id가 같아도 관리 버튼을 표시하지 않는다', async () => {
    mockedList.mockResolvedValue({
      total: 1,
      items: [{ ...auditItem, actor_type: 'system' }],
    });

    render(
      <AuditSearchTab
        members={members}
        organizationId="org-1"
        canManageActors
      />,
    );

    await screen.findByText('workflow.deploy');
    expect(
      screen.queryByRole('button', { name: '김관리 접근 관리' }),
    ).not.toBeInTheDocument();
  });

  it('좁은 viewport에서도 표를 가로 스크롤 영역에 가둔다', async () => {
    mockedList.mockResolvedValue({ total: 1, items: [auditItem] });

    render(<AuditSearchTab members={members} />);

    const table = await screen.findByRole('table');
    expect(table).toHaveClass('min-w-[760px]');
    expect(table.parentElement).toHaveClass('overflow-x-auto');
  });
});
