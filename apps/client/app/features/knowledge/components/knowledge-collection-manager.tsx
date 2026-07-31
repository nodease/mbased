'use client';

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  knowledgeApi,
  KnowledgeCollectionItemsResponse,
  KnowledgeCollectionLinkCandidate,
  KnowledgeCollectionPermissionResponse,
  KnowledgeCollectionResponse,
  KnowledgeCollectionVisibility,
  KnowledgeDelegationSubjectsResponse,
  KnowledgeDomainAction,
  KnowledgeDomainPermissionListResponse,
} from '@/app/features/knowledge/api/knowledgeApi';
import {
  CollectionDetailPanel,
  CollectionSidebar,
  DomainDelegationPanel,
  type CollectionCapabilities,
  type CollectionFormState,
  type DomainGrantFormState,
  type GrantFormState,
} from './knowledge-collection-manager-panels';
import { KnowledgeCollectionSyncPanel } from './knowledge-collection-sync-panel';

const errorText = (error: unknown) => {
  if (
    typeof error === 'object' &&
    error !== null &&
    'response' in error &&
    typeof (error as { response?: { data?: { error?: { message?: string } } } })
      .response?.data?.error?.message === 'string'
  ) {
    return (error as { response: { data: { error: { message: string } } } })
      .response.data.error.message;
  }
  return '요청을 처리하지 못했습니다.';
};

const CLOSED_COLLECTION_CAPABILITIES: CollectionCapabilities = {
  can_create_collection: false,
  can_change_public_visibility: false,
  can_manage_catalog: false,
  can_delegate_permissions: false,
  can_manage_lifecycle: false,
  can_manage_sync: false,
  can_manage_domain_permissions: false,
};

export default function KnowledgeCollectionManager() {
  const [collections, setCollections] = useState<KnowledgeCollectionResponse[]>(
    [],
  );
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [lifecycleState, setLifecycleState] = useState<'active' | 'archived'>(
    'active',
  );
  const [selectedBulkIds, setSelectedBulkIds] = useState<string[]>([]);
  const [itemData, setItemData] =
    useState<KnowledgeCollectionItemsResponse | null>(null);
  const [candidates, setCandidates] = useState<
    KnowledgeCollectionLinkCandidate[]
  >([]);
  const [permissions, setPermissions] = useState<
    KnowledgeCollectionPermissionResponse[]
  >([]);
  const [subjects, setSubjects] = useState<KnowledgeDelegationSubjectsResponse>(
    {
      subjects: [],
      next_cursor: null,
    },
  );
  const [domainPermissions, setDomainPermissions] = useState<
    KnowledgeDomainPermissionListResponse['permissions']
  >([]);
  const [domainSubjects, setDomainSubjects] =
    useState<KnowledgeDelegationSubjectsResponse>({
      subjects: [],
      next_cursor: null,
    });
  const [collectionSubjectQuery, setCollectionSubjectQuery] = useState('');
  const [domainSubjectQuery, setDomainSubjectQuery] = useState('');
  const [isSubjectLoading, setIsSubjectLoading] = useState(false);
  const [isDomainSubjectLoading, setIsDomainSubjectLoading] = useState(false);
  const [subjectLoadFailed, setSubjectLoadFailed] = useState(false);
  const [domainSubjectLoadFailed, setDomainSubjectLoadFailed] = useState(false);
  const [subjectRetryVersion, setSubjectRetryVersion] = useState(0);
  const [domainSubjectRetryVersion, setDomainSubjectRetryVersion] = useState(0);
  const collectionSubjectRequestVersion = useRef(0);
  const domainSubjectRequestVersion = useRef(0);
  const [capabilities, setCapabilities] = useState<CollectionCapabilities>({
    ...CLOSED_COLLECTION_CAPABILITIES,
  });
  const [isLoading, setIsLoading] = useState(true);
  const [isDetailLoading, setIsDetailLoading] = useState(false);
  const [isSaving, setIsSaving] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [form, setForm] = useState<CollectionFormState>({
    name: '',
    description: '',
    safeLabel: '',
  });
  const [editForm, setEditForm] = useState<CollectionFormState>({
    name: '',
    description: '',
    safeLabel: '',
  });
  const [grantForm, setGrantForm] = useState<GrantFormState>({
    subject_type: 'team',
    subject_id: '',
    role_bundle: 'viewer',
  });
  const [acknowledgePublic, setAcknowledgePublic] = useState(false);
  const [domainGrantForm, setDomainGrantForm] = useState<DomainGrantFormState>({
    subject_type: 'team',
    subject_id: '',
    permission_action: 'catalog_manage',
  });

  const selectedCollection = useMemo(
    () =>
      collections.find((collection) => collection.id === selectedId) ?? null,
    [collections, selectedId],
  );

  const loadCollections = useCallback(async () => {
    setIsLoading(true);
    setErrorMessage(null);
    try {
      const [data, domainCapabilities] = await Promise.all([
        knowledgeApi.getKnowledgeCollectionsResponse({
          lifecycle_state: lifecycleState,
        }),
        knowledgeApi.getKnowledgeDomainCapabilities(),
      ]);
      setCollections(data.collections);
      setCapabilities({
        can_create_collection: domainCapabilities.can_create_collection,
        can_change_public_visibility:
          domainCapabilities.can_change_public_visibility,
        can_manage_catalog: domainCapabilities.can_create_collection,
        can_delegate_permissions: domainCapabilities.can_delegate_permissions,
        can_manage_lifecycle: domainCapabilities.can_manage_lifecycle,
        can_manage_sync: domainCapabilities.can_manage_sync,
        can_manage_domain_permissions:
          domainCapabilities.can_manage_domain_permissions,
      });
      if (domainCapabilities.can_manage_domain_permissions) {
        const permissionData =
          await knowledgeApi.getKnowledgeDomainPermissions();
        setDomainPermissions(permissionData.permissions);
      } else {
        setDomainPermissions([]);
        setDomainSubjects({ subjects: [], next_cursor: null });
      }
      setSelectedId((currentId) =>
        data.collections.some((collection) => collection.id === currentId)
          ? currentId
          : (data.collections[0]?.id ?? null),
      );
      setSelectedBulkIds((currentIds) =>
        currentIds.filter((id) =>
          data.collections.some(
            (collection) =>
              collection.id === id &&
              (collection.can_manage ||
                domainCapabilities.can_delegate_permissions),
          ),
        ),
      );
    } catch (error) {
      setCapabilities({ ...CLOSED_COLLECTION_CAPABILITIES });
      setErrorMessage(errorText(error));
    } finally {
      setIsLoading(false);
    }
  }, [lifecycleState]);

  const loadCollectionDetail = useCallback(
    async (
      collection: KnowledgeCollectionResponse,
      currentCapabilities: CollectionCapabilities,
    ) => {
      setIsDetailLoading(true);
      setErrorMessage(null);
      try {
        const canManageCatalog =
          collection.can_manage || currentCapabilities.can_manage_catalog;
        const canDelegate =
          collection.can_manage || currentCapabilities.can_delegate_permissions;
        const [nextItemData, candidateData, permissionData] = await Promise.all(
          [
            collection.can_read || canManageCatalog
              ? knowledgeApi.getKnowledgeCollectionItems(collection.id)
              : Promise.resolve(null),
            collection.lifecycle_state === 'active' && canManageCatalog
              ? knowledgeApi.getKnowledgeCollectionLinkCandidates(collection.id)
              : Promise.resolve({ candidates: [] }),
            canDelegate
              ? knowledgeApi.getKnowledgeCollectionPermissions(collection.id)
              : Promise.resolve({ permissions: [] }),
          ],
        );
        setItemData(nextItemData);
        setCandidates(candidateData.candidates);
        setPermissions(permissionData.permissions);
      } catch (error) {
        setItemData(null);
        setCandidates([]);
        setPermissions([]);
        setSubjects({ subjects: [], next_cursor: null });
        setErrorMessage(errorText(error));
      } finally {
        setIsDetailLoading(false);
      }
    },
    [],
  );

  useEffect(() => {
    loadCollections();
  }, [loadCollections]);

  useEffect(() => {
    if (!selectedCollection) return;
    setEditForm({
      name: selectedCollection.name,
      description: selectedCollection.description ?? '',
      safeLabel:
        typeof selectedCollection.safe_metadata.safe_label === 'string'
          ? selectedCollection.safe_metadata.safe_label
          : '',
    });
    loadCollectionDetail(selectedCollection, capabilities);
  }, [capabilities, loadCollectionDetail, selectedCollection]);

  useEffect(() => {
    setAcknowledgePublic(false);
    setCollectionSubjectQuery('');
    setSubjects({ subjects: [], next_cursor: null });
    setGrantForm((current) => ({ ...current, subject_id: '' }));
  }, [selectedId]);

  useEffect(() => {
    if (
      !selectedCollection ||
      !(selectedCollection.can_manage || capabilities.can_delegate_permissions)
    ) {
      collectionSubjectRequestVersion.current += 1;
      setSubjects({ subjects: [], next_cursor: null });
      setIsSubjectLoading(false);
      setSubjectLoadFailed(false);
      return;
    }
    let cancelled = false;
    const requestVersion = ++collectionSubjectRequestVersion.current;
    const timer = window.setTimeout(async () => {
      setIsSubjectLoading(true);
      setSubjectLoadFailed(false);
      setErrorMessage(null);
      try {
        const response =
          await knowledgeApi.getKnowledgeCollectionDelegationSubjects(
            selectedCollection.id,
            {
              subject_type: grantForm.subject_type,
              query: collectionSubjectQuery.trim() || undefined,
              limit: 25,
            },
          );
        if (
          !cancelled &&
          collectionSubjectRequestVersion.current === requestVersion
        ) {
          setSubjects(response);
          setSubjectLoadFailed(false);
        }
      } catch (error) {
        if (
          !cancelled &&
          collectionSubjectRequestVersion.current === requestVersion
        ) {
          setSubjects({ subjects: [], next_cursor: null });
          setSubjectLoadFailed(true);
          setErrorMessage(errorText(error));
        }
      } finally {
        if (
          !cancelled &&
          collectionSubjectRequestVersion.current === requestVersion
        ) {
          setIsSubjectLoading(false);
        }
      }
    }, 250);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
      if (collectionSubjectRequestVersion.current === requestVersion) {
        collectionSubjectRequestVersion.current += 1;
      }
    };
  }, [
    capabilities.can_delegate_permissions,
    collectionSubjectQuery,
    grantForm.subject_type,
    selectedCollection,
    subjectRetryVersion,
  ]);

  useEffect(() => {
    if (!capabilities.can_manage_domain_permissions) {
      domainSubjectRequestVersion.current += 1;
      setIsDomainSubjectLoading(false);
      setDomainSubjectLoadFailed(false);
      return;
    }
    let cancelled = false;
    const requestVersion = ++domainSubjectRequestVersion.current;
    const timer = window.setTimeout(async () => {
      setIsDomainSubjectLoading(true);
      setDomainSubjectLoadFailed(false);
      setErrorMessage(null);
      try {
        const response =
          await knowledgeApi.getKnowledgeDomainDelegationSubjects({
            subject_type: domainGrantForm.subject_type,
            query: domainSubjectQuery.trim() || undefined,
            limit: 25,
          });
        if (
          !cancelled &&
          domainSubjectRequestVersion.current === requestVersion
        ) {
          setDomainSubjects(response);
          setDomainSubjectLoadFailed(false);
        }
      } catch (error) {
        if (
          !cancelled &&
          domainSubjectRequestVersion.current === requestVersion
        ) {
          setDomainSubjects({ subjects: [], next_cursor: null });
          setDomainSubjectLoadFailed(true);
          setErrorMessage(errorText(error));
        }
      } finally {
        if (
          !cancelled &&
          domainSubjectRequestVersion.current === requestVersion
        ) {
          setIsDomainSubjectLoading(false);
        }
      }
    }, 250);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
      if (domainSubjectRequestVersion.current === requestVersion) {
        domainSubjectRequestVersion.current += 1;
      }
    };
  }, [
    capabilities.can_manage_domain_permissions,
    domainGrantForm.subject_type,
    domainSubjectQuery,
    domainSubjectRetryVersion,
  ]);

  const refreshSelected = async () => {
    await loadCollections();
    if (selectedCollection) {
      await loadCollectionDetail(selectedCollection, capabilities);
    }
  };

  const loadMoreCollectionSubjects = async () => {
    if (!selectedCollection || !subjects.next_cursor || isSubjectLoading)
      return;
    const requestVersion = ++collectionSubjectRequestVersion.current;
    setIsSubjectLoading(true);
    try {
      const response =
        await knowledgeApi.getKnowledgeCollectionDelegationSubjects(
          selectedCollection.id,
          {
            subject_type: grantForm.subject_type,
            query: collectionSubjectQuery.trim() || undefined,
            cursor: subjects.next_cursor,
            limit: 25,
          },
        );
      if (collectionSubjectRequestVersion.current === requestVersion) {
        setSubjects((current) => ({
          subjects: [
            ...current.subjects,
            ...response.subjects.filter(
              (candidate) =>
                !current.subjects.some(
                  (subject) => subject.subject_id === candidate.subject_id,
                ),
            ),
          ],
          next_cursor: response.next_cursor,
        }));
      }
    } catch (error) {
      if (collectionSubjectRequestVersion.current === requestVersion) {
        setSubjectLoadFailed(true);
        setErrorMessage(errorText(error));
      }
    } finally {
      if (collectionSubjectRequestVersion.current === requestVersion) {
        setIsSubjectLoading(false);
      }
    }
  };

  const loadMoreDomainSubjects = async () => {
    if (!domainSubjects.next_cursor || isDomainSubjectLoading) return;
    const requestVersion = ++domainSubjectRequestVersion.current;
    setIsDomainSubjectLoading(true);
    try {
      const response = await knowledgeApi.getKnowledgeDomainDelegationSubjects({
        subject_type: domainGrantForm.subject_type,
        query: domainSubjectQuery.trim() || undefined,
        cursor: domainSubjects.next_cursor,
        limit: 25,
      });
      if (domainSubjectRequestVersion.current === requestVersion) {
        setDomainSubjects((current) => ({
          subjects: [
            ...current.subjects,
            ...response.subjects.filter(
              (candidate) =>
                !current.subjects.some(
                  (subject) => subject.subject_id === candidate.subject_id,
                ),
            ),
          ],
          next_cursor: response.next_cursor,
        }));
      }
    } catch (error) {
      if (domainSubjectRequestVersion.current === requestVersion) {
        setDomainSubjectLoadFailed(true);
        setErrorMessage(errorText(error));
      }
    } finally {
      if (domainSubjectRequestVersion.current === requestVersion) {
        setIsDomainSubjectLoading(false);
      }
    }
  };

  const createCollection = async () => {
    if (!form.name.trim() || !form.safeLabel.trim()) return;
    setIsSaving(true);
    setErrorMessage(null);
    try {
      const created = await knowledgeApi.createKnowledgeCollection({
        name: form.name.trim(),
        description: form.description.trim() || null,
        safe_metadata: {
          safe_label: form.safeLabel.trim(),
        },
      });
      setForm({ name: '', description: '', safeLabel: '' });
      await loadCollections();
      setSelectedId(created.id);
    } catch (error) {
      setErrorMessage(errorText(error));
    } finally {
      setIsSaving(false);
    }
  };

  const updateCollection = async () => {
    if (
      !selectedCollection ||
      !editForm.name.trim() ||
      !editForm.safeLabel.trim()
    ) {
      return;
    }
    setIsSaving(true);
    setErrorMessage(null);
    try {
      await knowledgeApi.updateKnowledgeCollection(selectedCollection.id, {
        name: editForm.name.trim(),
        description: editForm.description.trim() || null,
        safe_metadata: {
          ...selectedCollection.safe_metadata,
          safe_label: editForm.safeLabel.trim(),
        },
      });
      await refreshSelected();
    } catch (error) {
      setErrorMessage(errorText(error));
    } finally {
      setIsSaving(false);
    }
  };

  const archiveCollection = async () => {
    if (!selectedCollection) return;
    setIsSaving(true);
    setErrorMessage(null);
    try {
      await knowledgeApi.archiveKnowledgeCollection(selectedCollection.id);
      setSelectedId(null);
      await loadCollections();
    } catch (error) {
      setErrorMessage(errorText(error));
    } finally {
      setIsSaving(false);
    }
  };

  const restoreCollection = async () => {
    if (!selectedCollection) return;
    setIsSaving(true);
    setErrorMessage(null);
    try {
      await knowledgeApi.restoreKnowledgeCollection(selectedCollection.id);
      setSelectedId(null);
      await loadCollections();
    } catch (error) {
      setErrorMessage(errorText(error));
    } finally {
      setIsSaving(false);
    }
  };

  const linkCandidate = async (candidateId: string) => {
    if (!selectedCollection) return;
    setIsSaving(true);
    setErrorMessage(null);
    try {
      const response = await knowledgeApi.linkKnowledgeCollectionItem(
        selectedCollection.id,
        {
          knowledge_base_id: candidateId,
          acknowledged_public_runtime_exposure:
            selectedCollection.visibility === 'public' && acknowledgePublic,
        },
      );
      setItemData(response);
      const candidateData =
        await knowledgeApi.getKnowledgeCollectionLinkCandidates(
          selectedCollection.id,
        );
      setCandidates(candidateData.candidates);
      await loadCollections();
    } catch (error) {
      setErrorMessage(errorText(error));
    } finally {
      setIsSaving(false);
    }
  };

  const reorderItems = async (
    orderedItems: { item_id: string; rank: number }[],
    expectedOrderRevision: string,
  ) => {
    if (!selectedCollection) return;
    setIsSaving(true);
    setErrorMessage(null);
    try {
      const response = await knowledgeApi.reorderKnowledgeCollectionItems(
        selectedCollection.id,
        orderedItems,
        expectedOrderRevision,
        selectedCollection.visibility === 'public' && acknowledgePublic,
      );
      setItemData(response);
    } catch (error) {
      setErrorMessage(errorText(error));
      throw error;
    } finally {
      setIsSaving(false);
    }
  };

  const unlinkItem = async (itemId: string) => {
    if (!selectedCollection) return;
    setIsSaving(true);
    setErrorMessage(null);
    try {
      await knowledgeApi.unlinkKnowledgeCollectionItem(
        selectedCollection.id,
        itemId,
        selectedCollection.visibility === 'public' && acknowledgePublic,
      );
      await loadCollectionDetail(selectedCollection, capabilities);
      await loadCollections();
    } catch (error) {
      setErrorMessage(errorText(error));
    } finally {
      setIsSaving(false);
    }
  };

  const grantPermission = async () => {
    if (!selectedCollection || !grantForm.subject_id.trim()) return;
    setIsSaving(true);
    setErrorMessage(null);
    try {
      await knowledgeApi.grantKnowledgeCollectionPermissionBundle(
        selectedCollection.id,
        {
          ...grantForm,
          subject_id: grantForm.subject_id.trim(),
        },
      );
      setGrantForm({
        subject_type: 'team',
        subject_id: '',
        role_bundle: 'viewer',
      });
      await loadCollections();
    } catch (error) {
      setErrorMessage(errorText(error));
    } finally {
      setIsSaving(false);
    }
  };

  const revokePermissionBundle = async () => {
    if (!selectedCollection || !grantForm.subject_id.trim()) return;
    setIsSaving(true);
    setErrorMessage(null);
    try {
      await knowledgeApi.revokeKnowledgeCollectionPermissionBundle(
        selectedCollection.id,
        {
          ...grantForm,
          subject_id: grantForm.subject_id.trim(),
        },
      );
      await loadCollections();
    } catch (error) {
      setErrorMessage(errorText(error));
    } finally {
      setIsSaving(false);
    }
  };

  const mutatePermissionBundleBulk = async (operation: 'grant' | 'revoke') => {
    if (selectedBulkIds.length === 0 || !grantForm.subject_id.trim()) return;
    setIsSaving(true);
    setErrorMessage(null);
    try {
      await knowledgeApi.mutateKnowledgeCollectionPermissionBundles({
        collection_ids: selectedBulkIds,
        operation,
        subject_type: grantForm.subject_type,
        subject_id: grantForm.subject_id.trim(),
        role_bundle: grantForm.role_bundle,
      });
      await loadCollections();
    } catch (error) {
      setErrorMessage(errorText(error));
    } finally {
      setIsSaving(false);
    }
  };

  const revokePermission = async (permissionId: string) => {
    if (!selectedCollection) return;
    setIsSaving(true);
    setErrorMessage(null);
    try {
      await knowledgeApi.revokeKnowledgeCollectionPermission(
        selectedCollection.id,
        permissionId,
      );
      await loadCollections();
    } catch (error) {
      setErrorMessage(errorText(error));
    } finally {
      setIsSaving(false);
    }
  };

  const updateVisibility = async (
    visibility: KnowledgeCollectionVisibility,
  ) => {
    if (!selectedCollection) return;
    setIsSaving(true);
    setErrorMessage(null);
    try {
      await knowledgeApi.updateKnowledgeCollectionVisibility(
        selectedCollection.id,
        {
          visibility,
          acknowledged_public_runtime_exposure:
            visibility === 'public' ? acknowledgePublic : true,
        },
      );
      setAcknowledgePublic(false);
      await refreshSelected();
    } catch (error) {
      setErrorMessage(errorText(error));
    } finally {
      setIsSaving(false);
    }
  };

  const toggleBulkCollection = (collectionId: string) => {
    setSelectedBulkIds((current) => {
      if (current.includes(collectionId)) {
        return current.filter((id) => id !== collectionId);
      }
      return current.length >= 50 ? current : [...current, collectionId];
    });
  };

  const updateCollectionSubjectQuery = (query: string) => {
    setCollectionSubjectQuery(query);
    setGrantForm((current) => ({ ...current, subject_id: '' }));
  };

  const updateDomainSubjectQuery = (query: string) => {
    setDomainSubjectQuery(query);
    setDomainGrantForm((current) => ({ ...current, subject_id: '' }));
  };

  const grantDomainPermission = async () => {
    if (!domainGrantForm.subject_id) return;
    setIsSaving(true);
    setErrorMessage(null);
    try {
      await knowledgeApi.grantKnowledgeDomainPermission(domainGrantForm);
      setDomainGrantForm({
        subject_type: 'team',
        subject_id: '',
        permission_action: 'catalog_manage',
      });
      await loadCollections();
    } catch (error) {
      setErrorMessage(errorText(error));
    } finally {
      setIsSaving(false);
    }
  };

  const revokeDomainPermission = async (
    subjectType: 'team' | 'user',
    subjectId: string,
    permissionAction: KnowledgeDomainAction,
  ) => {
    setIsSaving(true);
    setErrorMessage(null);
    try {
      await knowledgeApi.revokeKnowledgeDomainPermission({
        subject_type: subjectType,
        subject_id: subjectId,
        permission_action: permissionAction,
      });
      await loadCollections();
    } catch (error) {
      setErrorMessage(errorText(error));
    } finally {
      setIsSaving(false);
    }
  };

  return (
    <div className="space-y-6">
      {errorMessage && (
        <div className="rounded-md border border-red-200 bg-red-50 px-4 py-3 text-sm font-medium text-red-700">
          {errorMessage}
        </div>
      )}

      {capabilities.can_manage_domain_permissions && (
        <DomainDelegationPanel
          form={domainGrantForm}
          hasSubjectLoadError={domainSubjectLoadFailed}
          isSubjectLoading={isDomainSubjectLoading}
          isSaving={isSaving}
          permissions={domainPermissions}
          subjectQuery={domainSubjectQuery}
          subjects={domainSubjects}
          onGrant={grantDomainPermission}
          onLoadMoreSubjects={loadMoreDomainSubjects}
          onRetrySubjects={() =>
            setDomainSubjectRetryVersion((current) => current + 1)
          }
          onRevoke={revokeDomainPermission}
          onSubjectQueryChange={updateDomainSubjectQuery}
          setForm={setDomainGrantForm}
        />
      )}

      <section className="grid gap-4 lg:grid-cols-[360px_1fr]">
        <CollectionSidebar
          capabilities={capabilities}
          collections={collections}
          form={form}
          isLoading={isLoading}
          isSaving={isSaving}
          lifecycleState={lifecycleState}
          selectedBulkIds={selectedBulkIds}
          selectedId={selectedId}
          onCreate={createCollection}
          onSelect={setSelectedId}
          onLifecycleStateChange={setLifecycleState}
          onToggleBulkCollection={toggleBulkCollection}
          setForm={setForm}
        />

        <div className="min-h-[480px] rounded-lg border border-slate-200 bg-white">
          <KnowledgeCollectionSyncPanel
            collection={selectedCollection}
            canManageSync={capabilities.can_manage_sync}
          />
          <CollectionDetailPanel
            acknowledgePublic={acknowledgePublic}
            candidates={candidates}
            capabilities={capabilities}
            collection={selectedCollection}
            editForm={editForm}
            grantForm={grantForm}
            isDetailLoading={isDetailLoading}
            hasSubjectLoadError={subjectLoadFailed}
            isSubjectLoading={isSubjectLoading}
            isSaving={isSaving}
            itemData={itemData}
            permissions={permissions}
            selectedBulkCount={selectedBulkIds.length}
            subjectQuery={collectionSubjectQuery}
            subjects={subjects}
            onArchive={archiveCollection}
            onBulkPermission={mutatePermissionBundleBulk}
            onGrantPermission={grantPermission}
            onLinkCandidate={linkCandidate}
            onLoadMoreSubjects={loadMoreCollectionSubjects}
            onRetrySubjects={() =>
              setSubjectRetryVersion((current) => current + 1)
            }
            onReorderItems={reorderItems}
            onRevokePermission={revokePermission}
            onRevokePermissionBundle={revokePermissionBundle}
            onRestore={restoreCollection}
            onSubjectQueryChange={updateCollectionSubjectQuery}
            onUnlinkItem={unlinkItem}
            onUpdateCollection={updateCollection}
            onUpdateVisibility={updateVisibility}
            setAcknowledgePublic={setAcknowledgePublic}
            setEditForm={setEditForm}
            setGrantForm={setGrantForm}
          />
        </div>
      </section>
    </div>
  );
}
