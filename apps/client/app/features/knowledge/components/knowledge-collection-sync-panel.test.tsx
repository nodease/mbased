import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { KnowledgeCollectionSyncPanel } from './knowledge-collection-sync-panel';
import {
  knowledgeApi,
  type KnowledgeCollectionResponse,
} from '@/app/features/knowledge/api/knowledgeApi';

vi.mock('@/app/features/knowledge/api/knowledgeApi', () => ({
  knowledgeApi: {
    getLatestKnowledgeCollectionSyncJob: vi.fn(),
    getKnowledgeCollectionSyncJob: vi.fn(),
    requestKnowledgeCollectionSync: vi.fn(),
  },
}));

const collection = (
  overrides: Partial<KnowledgeCollectionResponse> = {},
): KnowledgeCollectionResponse => ({
  id: '11111111-1111-4111-8111-111111111111',
  organization_id: '22222222-2222-4222-8222-222222222222',
  name: '사내 문서',
  description: null,
  is_system_managed: false,
  sync_state: 'manual',
  lifecycle_state: 'active',
  visibility: 'private',
  linked_kb_count_bucket: '2-10',
  active_kb_count_bucket: '2-10',
  can_read: true,
  can_route: false,
  can_manage: false,
  can_sync: true,
  sync_supported: true,
  safe_metadata: { safe_label: '사내 문서' },
  created_at: '2026-07-14T00:00:00Z',
  updated_at: '2026-07-14T00:00:00Z',
  ...overrides,
});

describe('KnowledgeCollectionSyncPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(
      knowledgeApi.getLatestKnowledgeCollectionSyncJob,
    ).mockResolvedValue({
      job: null,
    });
    vi.stubGlobal('crypto', {
      randomUUID: () => '33333333-3333-4333-8333-333333333333',
    });
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it('권한이 없으면 상태 조회와 실행 surface를 노출하지 않는다', () => {
    render(
      <KnowledgeCollectionSyncPanel
        collection={collection({ can_sync: false })}
        canManageSync={false}
      />,
    );

    expect(screen.queryByText('Collection 동기화')).not.toBeInTheDocument();
    expect(
      knowledgeApi.getLatestKnowledgeCollectionSyncJob,
    ).not.toHaveBeenCalled();
  });

  it('한 번의 사용자 동작을 하나의 idempotency key 요청으로 제한한다', async () => {
    vi.mocked(knowledgeApi.requestKnowledgeCollectionSync).mockResolvedValue({
      job: {
        job_id: '44444444-4444-4444-8444-444444444444',
        collection_id: '11111111-1111-4111-8111-111111111111',
        status: 'queued',
        progress: 'none',
        safe_reason_code: null,
        retryable: true,
        requested_at: '2026-07-14T00:00:00Z',
      },
      reused: false,
      dispatch_deferred: false,
    });
    render(
      <KnowledgeCollectionSyncPanel
        collection={collection()}
        canManageSync={false}
      />,
    );
    await waitFor(() =>
      expect(
        knowledgeApi.getLatestKnowledgeCollectionSyncJob,
      ).toHaveBeenCalled(),
    );

    const button = screen.getByRole('button', { name: '지금 동기화' });
    fireEvent.click(button);
    fireEvent.click(button);

    await waitFor(() =>
      expect(knowledgeApi.requestKnowledgeCollectionSync).toHaveBeenCalledTimes(
        1,
      ),
    );
    expect(knowledgeApi.requestKnowledgeCollectionSync).toHaveBeenCalledWith(
      '11111111-1111-4111-8111-111111111111',
      '33333333-3333-4333-8333-333333333333',
    );
    expect(await screen.findByText('상태: 대기 중')).toBeInTheDocument();
  });

  it('active job을 polling하고 terminal safe 상태만 표시한다', async () => {
    vi.useFakeTimers();
    vi.mocked(
      knowledgeApi.getLatestKnowledgeCollectionSyncJob,
    ).mockResolvedValue({
      job: {
        job_id: '44444444-4444-4444-8444-444444444444',
        collection_id: '11111111-1111-4111-8111-111111111111',
        status: 'running',
        progress: 'progressing',
        safe_reason_code: null,
        retryable: true,
        requested_at: '2026-07-14T00:00:00Z',
        started_at: '2026-07-14T00:00:01Z',
      },
    });
    vi.mocked(knowledgeApi.getKnowledgeCollectionSyncJob).mockResolvedValue({
      job_id: '44444444-4444-4444-8444-444444444444',
      collection_id: '11111111-1111-4111-8111-111111111111',
      status: 'partially_failed',
      progress: 'complete',
      safe_reason_code: 'sync.targets_changed',
      retryable: false,
      requested_at: '2026-07-14T00:00:00Z',
      started_at: '2026-07-14T00:00:01Z',
      completed_at: '2026-07-14T00:00:05Z',
    });
    render(
      <KnowledgeCollectionSyncPanel
        collection={collection()}
        canManageSync={false}
      />,
    );

    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(screen.getByText('상태: 동기화 중')).toBeInTheDocument();

    await act(async () => {
      vi.advanceTimersByTime(3000);
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(screen.getByText('상태: 일부 실패')).toBeInTheDocument();
    expect(
      screen.getByText('요청 후 대상 구성이 변경되었습니다.'),
    ).toBeInTheDocument();
  });

  it('지원하지 않는 Collection은 실행 요청을 보내지 않는다', async () => {
    render(
      <KnowledgeCollectionSyncPanel
        collection={collection({ sync_supported: false })}
        canManageSync={false}
      />,
    );
    await waitFor(() =>
      expect(
        knowledgeApi.getLatestKnowledgeCollectionSyncJob,
      ).toHaveBeenCalled(),
    );

    expect(screen.getByRole('button', { name: '지금 동기화' })).toBeDisabled();
    expect(
      screen.getByText(
        '현재는 직접 관리하는 Collection의 DB 문서 동기화만 지원합니다.',
      ),
    ).toBeInTheDocument();
    expect(knowledgeApi.requestKnowledgeCollectionSync).not.toHaveBeenCalled();
  });
});
