export type AdminTab =
  | 'organization-structure'
  | 'permissions'
  | 'usage'
  | 'credentials'
  | 'knowledge'
  | 'security-alerts'
  | 'audit';

export type OrganizationStructureView = 'members' | 'teams';

export const ADMIN_TAB_ITEMS: ReadonlyArray<{
  key: AdminTab;
  label: string;
}> = [
  { key: 'organization-structure', label: '조직 구성' },
  { key: 'permissions', label: '권한' },
  { key: 'usage', label: '비용' },
  { key: 'credentials', label: 'LLM Credentials' },
  { key: 'knowledge', label: '지식 기반' },
  { key: 'security-alerts', label: '보안 알림' },
  { key: 'audit', label: '감사 로그' },
];

const ADMIN_TABS = new Set(ADMIN_TAB_ITEMS.map(({ key }) => key));
const ORGANIZATION_STRUCTURE_VIEWS = new Set<OrganizationStructureView>([
  'members',
  'teams',
]);

const UUID_PATTERN =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

export type AdminUrlState = {
  tab: AdminTab;
  organizationView?: OrganizationStructureView;
  alertId: string | null;
  notice: string | null;
  normalizedQuery: URLSearchParams | null;
};

export const parseAdminUrlState = (
  searchParams: URLSearchParams,
): AdminUrlState => {
  const rawTab = searchParams.get('tab');
  const rawView = searchParams.get('view');
  const legacyView: OrganizationStructureView | null =
    rawTab === 'members' || rawTab === 'teams' ? rawTab : null;
  const tab =
    rawTab === 'permission-requests'
      ? 'permissions'
      : legacyView
        ? 'organization-structure'
      : rawTab && ADMIN_TABS.has(rawTab as AdminTab)
        ? (rawTab as AdminTab)
        : 'organization-structure';
  const organizationView: OrganizationStructureView | undefined =
    tab === 'organization-structure'
      ? legacyView ||
        (ORGANIZATION_STRUCTURE_VIEWS.has(
          rawView as OrganizationStructureView,
        )
          ? (rawView as OrganizationStructureView)
          : 'members')
      : undefined;
  const normalizedQuery = new URLSearchParams(searchParams);
  let needsNormalization = false;

  if (rawTab === 'permission-requests') {
    normalizedQuery.set('tab', 'permissions');
    normalizedQuery.delete('view');
    needsNormalization = true;
  }

  if (
    legacyView ||
    (rawTab &&
      rawTab !== 'permission-requests' &&
      !ADMIN_TABS.has(rawTab as AdminTab))
  ) {
    normalizedQuery.set('tab', 'organization-structure');
    normalizedQuery.set('view', organizationView!);
    normalizedQuery.delete('alertId');
    needsNormalization = true;
  }

  if (
    rawTab === 'organization-structure' &&
    !ORGANIZATION_STRUCTURE_VIEWS.has(rawView as OrganizationStructureView)
  ) {
    normalizedQuery.set('view', 'members');
    needsNormalization = true;
  }

  if (tab !== 'organization-structure' && normalizedQuery.has('view')) {
    normalizedQuery.delete('view');
    needsNormalization = true;
  }

  const rawAlertId = normalizedQuery.get('alertId');
  if (tab !== 'security-alerts' && rawAlertId) {
    normalizedQuery.delete('alertId');
    needsNormalization = true;
  }

  if (tab === 'security-alerts' && rawAlertId && !UUID_PATTERN.test(rawAlertId)) {
    normalizedQuery.delete('alertId');
    return {
      tab,
      alertId: null,
      notice: '알림을 찾을 수 없습니다.',
      normalizedQuery,
    };
  }

  return {
    tab,
    ...(organizationView ? { organizationView } : {}),
    alertId: tab === 'security-alerts' ? rawAlertId : null,
    notice: null,
    normalizedQuery: needsNormalization ? normalizedQuery : null,
  };
};

export const buildAdminTabUrl = (
  pathname: string,
  searchParams: URLSearchParams,
  tab: AdminTab,
) => {
  const nextSearchParams = new URLSearchParams(searchParams);
  nextSearchParams.set('tab', tab);
  if (tab === 'organization-structure') {
    const currentView = nextSearchParams.get('view');
    if (
      !ORGANIZATION_STRUCTURE_VIEWS.has(
        currentView as OrganizationStructureView,
      )
    ) {
      nextSearchParams.set('view', 'members');
    }
  } else {
    nextSearchParams.delete('view');
  }
  if (tab !== 'security-alerts') nextSearchParams.delete('alertId');
  return `${pathname}?${nextSearchParams.toString()}`;
};

export const buildOrganizationStructureViewUrl = (
  pathname: string,
  searchParams: URLSearchParams,
  view: OrganizationStructureView,
) => {
  const nextSearchParams = new URLSearchParams(searchParams);
  nextSearchParams.set('tab', 'organization-structure');
  nextSearchParams.set('view', view);
  nextSearchParams.delete('alertId');
  return `${pathname}?${nextSearchParams.toString()}`;
};

export const isAdminTabVisible = (tab: AdminTab, isManager: boolean) =>
  tab !== 'security-alerts' || isManager;
