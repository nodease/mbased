import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import KnowledgeCollectionManager from './knowledge-collection-manager';
import { DomainDelegationPanel } from './knowledge-collection-manager-panels';

const knowledgeApiMock = vi.hoisted(() => ({
  getKnowledgeCollectionsResponse: vi.fn(),
  getKnowledgeDomainCapabilities: vi.fn(),
  getKnowledgeDomainPermissions: vi.fn(),
  getKnowledgeDomainDelegationSubjects: vi.fn(),
  getKnowledgeCollectionItems: vi.fn(),
  getKnowledgeCollectionLinkCandidates: vi.fn(),
  getKnowledgeCollectionPermissions: vi.fn(),
  getKnowledgeCollectionDelegationSubjects: vi.fn(),
  createKnowledgeCollection: vi.fn(),
  updateKnowledgeCollection: vi.fn(),
  archiveKnowledgeCollection: vi.fn(),
  restoreKnowledgeCollection: vi.fn(),
  linkKnowledgeCollectionItem: vi.fn(),
  unlinkKnowledgeCollectionItem: vi.fn(),
  grantKnowledgeCollectionPermission: vi.fn(),
  grantKnowledgeCollectionPermissionBundle: vi.fn(),
  revokeKnowledgeCollectionPermissionBundle: vi.fn(),
  mutateKnowledgeCollectionPermissionBundles: vi.fn(),
  reorderKnowledgeCollectionItems: vi.fn(),
  grantKnowledgeDomainPermission: vi.fn(),
  revokeKnowledgeDomainPermission: vi.fn(),
  revokeKnowledgeCollectionPermission: vi.fn(),
  updateKnowledgeCollectionVisibility: vi.fn(),
}));

vi.mock('@/app/features/knowledge/api/knowledgeApi', () => ({
  knowledgeApi: knowledgeApiMock,
}));

describe('KnowledgeCollectionManager', () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it('offers an explicit retry after delegation subject lookup fails', () => {
    const retry = vi.fn();

    render(
      <DomainDelegationPanel
        form={{
          subject_type: 'team',
          subject_id: '',
          permission_action: 'catalog_manage',
        }}
        hasSubjectLoadError
        isSubjectLoading={false}
        isSaving={false}
        permissions={[]}
        subjectQuery=""
        subjects={{ subjects: [], next_cursor: null }}
        onGrant={vi.fn()}
        onLoadMoreSubjects={vi.fn()}
        onRetrySubjects={retry}
        onRevoke={vi.fn()}
        onSubjectQueryChange={vi.fn()}
        setForm={vi.fn()}
      />,
    );

    fireEvent.click(
      screen.getByRole('button', { name: '대상 조회 다시 시도' }),
    );

    expect(retry).toHaveBeenCalledTimes(1);
  });

  it('keeps read-only item view available without calling manage-only APIs', async () => {
    knowledgeApiMock.getKnowledgeDomainCapabilities.mockResolvedValue({
      actions: [],
      can_manage_domain_permissions: false,
      can_create_collection: false,
      can_delegate_permissions: false,
      can_manage_lifecycle: false,
      can_manage_sync: false,
      can_change_public_visibility: false,
    });
    knowledgeApiMock.getKnowledgeCollectionsResponse.mockResolvedValue({
      collections: [
        {
          id: 'collection-1',
          organization_id: 'org-1',
          name: 'HR',
          description: '인사 문서',
          is_system_managed: false,
          sync_state: 'manual',
          lifecycle_state: 'active',
          visibility: 'private',
          linked_kb_count_bucket: '1',
          active_kb_count_bucket: '1',
          can_read: true,
          can_route: false,
          can_manage: false,
          can_sync: false,
          sync_supported: true,
          safe_metadata: { safe_label: '인사 정책' },
          created_at: '2026-07-07T00:00:00Z',
          updated_at: '2026-07-07T00:00:00Z',
        },
      ],
      can_create_collection: false,
      can_change_public_visibility: true,
    });
    knowledgeApiMock.getKnowledgeCollectionItems.mockResolvedValueOnce({
      items: [
        {
          item_id: 'item-1',
          knowledge_base_id: 'kb-1',
          safe_label: '휴가 정책',
          lifecycle_state: 'active',
          sync_state: 'manual',
          rank: 0,
          can_manage_kb: true,
          can_use_kb: true,
        },
      ],
      order_revision: `ord_v1_${'1'.repeat(64)}`,
      reorder_supported: true,
      safe_reason_code: null,
    });

    render(<KnowledgeCollectionManager />);

    expect(await screen.findByText('휴가 정책')).toBeInTheDocument();
    expect(screen.getByRole('textbox', { name: '안전 표시 이름' })).toHaveValue(
      '인사 정책',
    );
    expect(
      screen.getByRole('textbox', { name: '안전 표시 이름' }),
    ).toBeDisabled();
    expect(screen.queryByText('Collection 생성')).not.toBeInTheDocument();
    expect(
      screen.getByText(
        '공개 상태 전환은 organization manager만 수행할 수 있습니다.',
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        '권한 관리는 collection.manage 또는 Knowledge permission_delegate가 필요합니다.',
      ),
    ).toBeInTheDocument();
    expect(screen.queryByText('부여')).not.toBeInTheDocument();
    expect(screen.getByLabelText('KB 연결 해제')).toBeDisabled();
    await waitFor(() => {
      expect(
        knowledgeApiMock.getKnowledgeCollectionLinkCandidates,
      ).not.toHaveBeenCalled();
      expect(
        knowledgeApiMock.getKnowledgeCollectionPermissions,
      ).not.toHaveBeenCalled();
      expect(
        knowledgeApiMock.getKnowledgeCollectionDelegationSubjects,
      ).not.toHaveBeenCalled();
    });
  });

  it('uses delegated domain capability for collection creation and Team-first bundles', async () => {
    knowledgeApiMock.getKnowledgeDomainCapabilities.mockResolvedValue({
      actions: ['catalog_manage', 'permission_delegate', 'lifecycle_manage'],
      can_manage_domain_permissions: false,
      can_create_collection: true,
      can_delegate_permissions: true,
      can_manage_lifecycle: true,
      can_manage_sync: false,
      can_change_public_visibility: false,
    });
    knowledgeApiMock.getKnowledgeCollectionsResponse.mockResolvedValue({
      collections: [
        {
          id: 'collection-1',
          organization_id: 'org-1',
          name: 'HR',
          description: '인사 문서',
          is_system_managed: false,
          sync_state: 'manual',
          lifecycle_state: 'active',
          visibility: 'private',
          linked_kb_count_bucket: '0',
          active_kb_count_bucket: '0',
          can_read: false,
          can_route: false,
          can_manage: false,
          can_sync: false,
          sync_supported: true,
          safe_metadata: {},
          created_at: '2026-07-07T00:00:00Z',
          updated_at: '2026-07-07T00:00:00Z',
        },
      ],
      can_create_collection: false,
      can_change_public_visibility: false,
    });
    knowledgeApiMock.getKnowledgeCollectionItems.mockResolvedValue({
      items: [],
      order_revision: `ord_v1_${'2'.repeat(64)}`,
      reorder_supported: true,
      safe_reason_code: null,
    });
    knowledgeApiMock.getKnowledgeCollectionLinkCandidates.mockResolvedValue({
      candidates: [],
    });
    knowledgeApiMock.getKnowledgeCollectionPermissions.mockResolvedValue({
      permissions: [],
    });
    knowledgeApiMock.getKnowledgeCollectionDelegationSubjects.mockResolvedValue(
      {
        subjects: [
          {
            subject_type: 'team',
            subject_id: 'team-1',
            subject_safe_label: 'Knowledge 전담 Team',
          },
        ],
        next_cursor: null,
      },
    );
    knowledgeApiMock.grantKnowledgeCollectionPermissionBundle.mockResolvedValueOnce(
      {
        permissions: [],
      },
    );

    render(<KnowledgeCollectionManager />);

    expect(await screen.findByText('Knowledge 전담 Team')).toBeInTheDocument();
    expect(screen.getByText('Collection 생성')).toBeInTheDocument();
    const targetSelect = screen
      .getAllByRole('combobox')
      .find((element) => element.querySelector('option[value="team-1"]'));
    expect(targetSelect).toBeDefined();
    fireEvent.change(targetSelect!, { target: { value: 'team-1' } });
    fireEvent.click(screen.getByRole('button', { name: 'Bundle 부여' }));

    await waitFor(() =>
      expect(
        knowledgeApiMock.grantKnowledgeCollectionPermissionBundle,
      ).toHaveBeenCalledWith('collection-1', {
        subject_type: 'team',
        subject_id: 'team-1',
        role_bundle: 'viewer',
      }),
    );

    await waitFor(() =>
      expect(
        knowledgeApiMock.getKnowledgeCollectionsResponse,
      ).toHaveBeenCalledTimes(2),
    );
    const refreshedTargetSelect = screen
      .getAllByRole('combobox')
      .find((element) => element.querySelector('option[value="team-1"]'));
    expect(refreshedTargetSelect).toBeDefined();
    fireEvent.change(refreshedTargetSelect!, {
      target: { value: 'team-1' },
    });
    await waitFor(() =>
      expect(screen.getByRole('button', { name: 'Bundle 부여' })).toBeEnabled(),
    );
    fireEvent.click(screen.getByLabelText('HR bulk 권한 대상 선택'));
    fireEvent.click(
      await screen.findByRole('button', { name: '선택 KC 일괄 부여' }),
    );
    await waitFor(() =>
      expect(
        knowledgeApiMock.mutateKnowledgeCollectionPermissionBundles,
      ).toHaveBeenCalledWith({
        collection_ids: ['collection-1'],
        operation: 'grant',
        subject_type: 'team',
        subject_id: 'team-1',
        role_bundle: 'viewer',
      }),
    );
  });

  it('restores an archived manual Collection with delegated lifecycle authority', async () => {
    knowledgeApiMock.getKnowledgeDomainCapabilities.mockResolvedValue({
      actions: ['lifecycle_manage'],
      can_manage_domain_permissions: false,
      can_create_collection: false,
      can_delegate_permissions: false,
      can_manage_lifecycle: true,
      can_manage_sync: false,
      can_change_public_visibility: false,
    });
    knowledgeApiMock.getKnowledgeCollectionsResponse.mockImplementation(
      async (params?: { lifecycle_state?: string }) => ({
        collections:
          params?.lifecycle_state === 'archived'
            ? [
                {
                  id: 'collection-archived',
                  organization_id: 'org-1',
                  name: 'Archived HR',
                  description: '복구 대상',
                  is_system_managed: false,
                  sync_state: 'manual',
                  lifecycle_state: 'archived',
                  visibility: 'private',
                  linked_kb_count_bucket: '0',
                  active_kb_count_bucket: '0',
                  can_read: false,
                  can_route: false,
                  can_manage: false,
                  can_sync: false,
                  safe_metadata: { safe_label: '보관 인사 문서' },
                  created_at: '2026-07-07T00:00:00Z',
                  updated_at: '2026-07-07T00:00:00Z',
                },
              ]
            : [],
        can_create_collection: false,
        can_change_public_visibility: false,
      }),
    );
    knowledgeApiMock.restoreKnowledgeCollection.mockResolvedValue(undefined);

    render(<KnowledgeCollectionManager />);

    fireEvent.click(await screen.findByRole('tab', { name: 'archived' }));
    const restoreButton = await screen.findByRole('button', {
      name: 'Restore',
    });
    expect(restoreButton).toBeEnabled();
    fireEvent.click(restoreButton);

    await waitFor(() =>
      expect(knowledgeApiMock.restoreKnowledgeCollection).toHaveBeenCalledWith(
        'collection-archived',
      ),
    );
  });

  it('saves a complete reordered item set with the loaded revision', async () => {
    const revision = `ord_v1_${'4'.repeat(64)}`;
    knowledgeApiMock.getKnowledgeDomainCapabilities.mockResolvedValue({
      actions: [],
      can_manage_domain_permissions: false,
      can_create_collection: false,
      can_delegate_permissions: false,
      can_manage_lifecycle: false,
      can_manage_sync: false,
      can_change_public_visibility: false,
    });
    knowledgeApiMock.getKnowledgeCollectionsResponse.mockResolvedValue({
      collections: [
        {
          id: 'collection-order',
          organization_id: 'org-1',
          name: 'Ordered KC',
          description: null,
          is_system_managed: false,
          sync_state: 'manual',
          lifecycle_state: 'active',
          visibility: 'private',
          linked_kb_count_bucket: '2-10',
          active_kb_count_bucket: '2-10',
          can_read: true,
          can_route: true,
          can_manage: true,
          can_sync: false,
          safe_metadata: { safe_label: '정렬 KC' },
          created_at: '2026-07-07T00:00:00Z',
          updated_at: '2026-07-07T00:00:00Z',
        },
      ],
      can_create_collection: false,
      can_change_public_visibility: false,
    });
    const first = {
      item_id: 'item-1',
      knowledge_base_id: 'kb-1',
      safe_label: 'First KB',
      lifecycle_state: 'active',
      sync_state: 'manual',
      rank: 0,
      can_manage_kb: true,
      can_use_kb: true,
    };
    const second = {
      ...first,
      item_id: 'item-2',
      safe_label: 'Second KB',
      rank: 1,
    };
    knowledgeApiMock.getKnowledgeCollectionItems.mockResolvedValue({
      items: [first, second],
      order_revision: revision,
      reorder_supported: true,
      safe_reason_code: null,
    });
    knowledgeApiMock.getKnowledgeCollectionLinkCandidates.mockResolvedValue({
      candidates: [],
    });
    knowledgeApiMock.getKnowledgeCollectionPermissions.mockResolvedValue({
      permissions: [],
    });
    knowledgeApiMock.getKnowledgeCollectionDelegationSubjects.mockResolvedValue(
      {
        subjects: [],
        next_cursor: null,
      },
    );
    knowledgeApiMock.reorderKnowledgeCollectionItems.mockResolvedValue({
      items: [second, first],
      order_revision: `ord_v1_${'5'.repeat(64)}`,
      reorder_supported: true,
      safe_reason_code: null,
    });

    render(<KnowledgeCollectionManager />);

    fireEvent.click(
      await screen.findByRole('button', { name: 'First KB 아래로 이동' }),
    );
    fireEvent.click(screen.getByRole('button', { name: '순서 저장' }));

    await waitFor(() =>
      expect(
        knowledgeApiMock.reorderKnowledgeCollectionItems,
      ).toHaveBeenCalledWith(
        'collection-order',
        [
          { item_id: 'item-2', rank: 0 },
          { item_id: 'item-1', rank: 1 },
        ],
        revision,
        false,
      ),
    );
  });

  it('fails closed when delegated capability refresh fails', async () => {
    knowledgeApiMock.getKnowledgeDomainCapabilities
      .mockResolvedValueOnce({
        actions: ['catalog_manage'],
        can_manage_domain_permissions: false,
        can_create_collection: true,
        can_delegate_permissions: false,
        can_manage_lifecycle: false,
        can_manage_sync: false,
        can_change_public_visibility: false,
      })
      .mockRejectedValueOnce(new Error('transient capability failure'));
    knowledgeApiMock.getKnowledgeCollectionsResponse.mockResolvedValue({
      collections: [],
      can_create_collection: false,
      can_change_public_visibility: false,
    });
    knowledgeApiMock.createKnowledgeCollection.mockResolvedValue({
      id: 'collection-new',
    });

    render(<KnowledgeCollectionManager />);

    const nameInput = await screen.findByRole('textbox', { name: '관리용 이름' });
    const safeLabelInput = screen.getByRole('textbox', {
      name: '안전 표시 이름',
    });
    fireEvent.change(nameInput, { target: { value: '위임 Collection' } });
    fireEvent.change(safeLabelInput, { target: { value: '안전한 표시 이름' } });
    fireEvent.click(screen.getByRole('button', { name: '생성' }));

    expect(await screen.findByText('요청을 처리하지 못했습니다.')).toBeInTheDocument();
    expect(screen.queryByText('Collection 생성')).not.toBeInTheDocument();
  });

  it('requires a safe display label when creating a manual collection', async () => {
    knowledgeApiMock.getKnowledgeDomainCapabilities.mockResolvedValue({
      actions: ['catalog_manage'],
      can_manage_domain_permissions: false,
      can_create_collection: true,
      can_delegate_permissions: false,
      can_manage_lifecycle: false,
      can_manage_sync: false,
      can_change_public_visibility: false,
    });
    knowledgeApiMock.getKnowledgeCollectionsResponse.mockResolvedValue({
      collections: [],
      can_create_collection: true,
      can_change_public_visibility: false,
    });
    knowledgeApiMock.createKnowledgeCollection.mockResolvedValue({
      id: 'collection-new',
    });

    render(<KnowledgeCollectionManager />);

    const createButton = await screen.findByRole('button', { name: '생성' });
    const nameInput = screen.getByRole('textbox', { name: '관리용 이름' });
    const safeLabelInput = screen.getByRole('textbox', {
      name: '안전 표시 이름',
    });
    expect(createButton).toBeDisabled();

    fireEvent.change(nameInput, { target: { value: '  HR 관리 이름  ' } });
    expect(createButton).toBeDisabled();

    fireEvent.change(safeLabelInput, {
      target: { value: '  사내 인사 문서  ' },
    });
    fireEvent.click(createButton);

    await waitFor(() =>
      expect(knowledgeApiMock.createKnowledgeCollection).toHaveBeenCalledWith({
        name: 'HR 관리 이름',
        description: null,
        safe_metadata: { safe_label: '사내 인사 문서' },
      }),
    );
  });

  it('requires explicit remediation for a legacy collection and preserves metadata', async () => {
    knowledgeApiMock.getKnowledgeDomainCapabilities.mockResolvedValue({
      actions: [],
      can_manage_domain_permissions: false,
      can_create_collection: false,
      can_delegate_permissions: false,
      can_manage_lifecycle: false,
      can_manage_sync: false,
      can_change_public_visibility: false,
    });
    knowledgeApiMock.getKnowledgeCollectionsResponse.mockResolvedValue({
      collections: [
        {
          id: 'collection-legacy',
          organization_id: 'org-1',
          name: '관리 전용 원본 이름',
          description: '기존 Collection',
          is_system_managed: false,
          sync_state: 'manual',
          lifecycle_state: 'active',
          visibility: 'private',
          linked_kb_count_bucket: '0',
          active_kb_count_bucket: '0',
          can_read: true,
          can_route: true,
          can_manage: true,
          can_sync: false,
          sync_supported: true,
          safe_metadata: {
            collection_safe_topics: ['policy'],
          },
          created_at: '2026-07-07T00:00:00Z',
          updated_at: '2026-07-07T00:00:00Z',
        },
      ],
      can_create_collection: false,
      can_change_public_visibility: false,
    });
    knowledgeApiMock.getKnowledgeCollectionItems.mockResolvedValue({
      items: [],
      order_revision: `ord_v1_${'3'.repeat(64)}`,
      reorder_supported: true,
      safe_reason_code: null,
    });
    knowledgeApiMock.getKnowledgeCollectionLinkCandidates.mockResolvedValue({
      candidates: [],
    });
    knowledgeApiMock.getKnowledgeCollectionPermissions.mockResolvedValue({
      permissions: [],
    });
    knowledgeApiMock.getKnowledgeCollectionDelegationSubjects.mockResolvedValue(
      {
        subjects: [],
        next_cursor: null,
      },
    );
    knowledgeApiMock.updateKnowledgeCollection.mockResolvedValue({
      id: 'collection-legacy',
    });

    render(<KnowledgeCollectionManager />);

    expect(
      await screen.findByText(
        'Workflow에서 이 Collection을 구분할 수 있도록 안전 표시 이름을 입력하세요.',
      ),
    ).toBeInTheDocument();
    const safeLabelInput = screen.getByRole('textbox', {
      name: '안전 표시 이름',
    });
    const saveButton = screen.getByRole('button', { name: '저장' });
    expect(safeLabelInput).toHaveValue('');
    expect(saveButton).toBeDisabled();

    fireEvent.change(safeLabelInput, {
      target: { value: '  사내 정책 자료  ' },
    });
    fireEvent.click(saveButton);

    await waitFor(() =>
      expect(knowledgeApiMock.updateKnowledgeCollection).toHaveBeenCalledWith(
        'collection-legacy',
        {
          name: '관리 전용 원본 이름',
          description: '기존 Collection',
          safe_metadata: {
            collection_safe_topics: ['policy'],
            safe_label: '사내 정책 자료',
          },
        },
      ),
    );
  });
});
