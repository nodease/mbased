import { describe, expect, it } from 'vitest';
import {
  ADMIN_TAB_ITEMS,
  buildAdminTabUrl,
  buildOrganizationStructureViewUrl,
  isAdminTabVisible,
  parseAdminUrlState,
} from './adminUrlState';

const ALERT_ID = '123e4567-e89b-42d3-a456-426614174000';

describe('parseAdminUrlState', () => {
  it('Security Alert deep link의 tab과 UUID alertId를 복원한다', () => {
    const state = parseAdminUrlState(
      new URLSearchParams(`tab=security-alerts&alertId=${ALERT_ID}`),
    );

    expect(state).toEqual({
      tab: 'security-alerts',
      alertId: ALERT_ID,
      notice: null,
      normalizedQuery: null,
    });
  });

  it('지원하지 않는 tab은 조직 구성의 멤버 보기로 정규화하고 alertId를 제거한다', () => {
    const state = parseAdminUrlState(
      new URLSearchParams(`tab=unknown&alertId=${ALERT_ID}`),
    );

    expect(state.tab).toBe('organization-structure');
    expect(state).toEqual(
      expect.objectContaining({ organizationView: 'members' }),
    );
    expect(state.alertId).toBeNull();
    expect(state.normalizedQuery?.toString()).toBe(
      'tab=organization-structure&view=members',
    );
  });

  it('UUID가 아닌 alertId는 제거하고 safe 안내를 반환한다', () => {
    const state = parseAdminUrlState(
      new URLSearchParams('tab=security-alerts&alertId=not-a-uuid'),
    );

    expect(state.tab).toBe('security-alerts');
    expect(state.alertId).toBeNull();
    expect(state.notice).toBe('알림을 찾을 수 없습니다.');
    expect(state.normalizedQuery?.toString()).toBe('tab=security-alerts');
  });

  it('기존 권한 신청 주소는 통합된 권한 탭으로 정규화한다', () => {
    const state = parseAdminUrlState(
      new URLSearchParams('tab=permission-requests'),
    );

    expect(state.tab).toBe('permissions');
    expect(state.normalizedQuery?.toString()).toBe('tab=permissions');
  });

  it('제거된 조직 설정 주소는 조직 구성의 멤버 보기로 정규화한다', () => {
    const state = parseAdminUrlState(new URLSearchParams('tab=organization'));

    expect(state.tab).toBe('organization-structure');
    expect(state).toEqual(
      expect.objectContaining({ organizationView: 'members' }),
    );
    expect(state.normalizedQuery?.toString()).toBe(
      'tab=organization-structure&view=members',
    );
  });

  it.each([
    ['members', 'members'],
    ['teams', 'teams'],
  ])(
    '기존 %s 탭 주소를 조직 구성의 %s 보기로 정규화한다',
    (legacyTab, expectedView) => {
      const state = parseAdminUrlState(
        new URLSearchParams(`tab=${legacyTab}`),
      );

      expect(state.tab).toBe('organization-structure');
      expect(state).toEqual(
        expect.objectContaining({ organizationView: expectedView }),
      );
      expect(state.normalizedQuery?.toString()).toBe(
        `tab=organization-structure&view=${expectedView}`,
      );
    },
  );

  it('조직 구성 deep link의 팀 보기를 복원한다', () => {
    const state = parseAdminUrlState(
      new URLSearchParams('tab=organization-structure&view=teams'),
    );

    expect(state.tab).toBe('organization-structure');
    expect(state).toEqual(
      expect.objectContaining({ organizationView: 'teams' }),
    );
    expect(state.normalizedQuery).toBeNull();
  });
});

describe('ADMIN_TAB_ITEMS', () => {
  it('권한 신청 메뉴를 제거하고 권한 메뉴만 제공한다', () => {
    expect(ADMIN_TAB_ITEMS).toContainEqual({ key: 'permissions', label: '권한' });
    expect(ADMIN_TAB_ITEMS.map(({ key }) => String(key))).not.toContain(
      'permission-requests',
    );
  });

  it('멤버와 팀 메뉴를 조직 구성 메뉴 하나로 제공한다', () => {
    expect(ADMIN_TAB_ITEMS).toContainEqual({
      key: 'organization-structure',
      label: '조직 구성',
    });
    expect(ADMIN_TAB_ITEMS.map(({ key }) => String(key))).not.toContain(
      'members',
    );
    expect(ADMIN_TAB_ITEMS.map(({ key }) => String(key))).not.toContain('teams');
  });

  it('조직 설정 메뉴를 제공하지 않는다', () => {
    expect(ADMIN_TAB_ITEMS.map(({ key }) => String(key))).not.toContain(
      'organization',
    );
    expect(ADMIN_TAB_ITEMS.map(({ label }) => label)).not.toContain(
      '조직 설정',
    );
  });
});

describe('buildAdminTabUrl', () => {
  it('선택한 tab을 URL에 반영하고 다른 tab이면 alertId를 제거한다', () => {
    const url = buildAdminTabUrl(
      '/dashboard/admin',
      new URLSearchParams(`tab=security-alerts&alertId=${ALERT_ID}`),
      'audit',
    );

    expect(url).toBe('/dashboard/admin?tab=audit');
  });

  it('Security Alert tab을 다시 선택하면 유효한 alertId를 유지한다', () => {
    const url = buildAdminTabUrl(
      '/dashboard/admin',
      new URLSearchParams(`tab=security-alerts&alertId=${ALERT_ID}`),
      'security-alerts',
    );

    expect(url).toBe(
      `/dashboard/admin?tab=security-alerts&alertId=${ALERT_ID}`,
    );
  });

  it('조직 구성 tab을 선택하면 기존 보기를 유지하거나 멤버 보기를 기본값으로 사용한다', () => {
    expect(
      buildAdminTabUrl(
        '/dashboard/admin',
        new URLSearchParams('tab=audit'),
        'organization-structure',
      ),
    ).toBe('/dashboard/admin?tab=organization-structure&view=members');

    expect(
      buildAdminTabUrl(
        '/dashboard/admin',
        new URLSearchParams('tab=audit&view=teams'),
        'organization-structure',
      ),
    ).toBe('/dashboard/admin?tab=organization-structure&view=teams');
  });
});

describe('buildOrganizationStructureViewUrl', () => {
  it('선택한 조직 구성 보기를 URL에 반영하고 alertId를 제거한다', () => {
    const url = buildOrganizationStructureViewUrl(
      '/dashboard/admin',
      new URLSearchParams(`tab=security-alerts&alertId=${ALERT_ID}`),
      'teams',
    );

    expect(url).toBe('/dashboard/admin?tab=organization-structure&view=teams');
  });
});

describe('isAdminTabVisible', () => {
  it('보안 알림 탭은 manager에게만 표시한다', () => {
    expect(isAdminTabVisible('security-alerts', true)).toBe(true);
    expect(isAdminTabVisible('security-alerts', false)).toBe(false);
    expect(isAdminTabVisible('organization-structure', false)).toBe(true);
  });
});
