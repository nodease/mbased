import { describe, expect, it } from 'vitest';

import type { ModuleOperationRow } from '../api/moduleOperationsApi';
import {
  buildModuleRunHref,
  canRunDeployedModule,
  getModuleRunDisabledReason,
} from '../utils/moduleRunNavigation';

const baseRow = (overrides: Partial<ModuleOperationRow> = {}) =>
  ({
    app: {
      id: 'app-1',
      name: '사내 문서 질문 응답 봇',
      workflow_id: 'workflow-1',
      created_at: '2026-07-08T00:00:00Z',
      updated_at: '2026-07-08T00:00:00Z',
    },
    permission: {
      workflow_id: 'workflow-1',
      auth_state: 'operator',
      can_read: true,
      can_write: false,
      can_execute: true,
      can_deploy: false,
      can_manage: false,
    },
    permissionStatus: 'loaded',
    permissionSources: [],
    deployment: {
      state: 'active',
      deployment_id: 'deployment-1',
      is_active: true,
      type: 'chatbot',
    },
    deploymentState: 'active',
    latestRun: { state: 'not_started' },
    dataQuality: {
      permissionSourcesUnavailable: false,
      latestRunUnavailable: false,
    },
    ...overrides,
  }) as ModuleOperationRow;

describe('module run navigation', () => {
  it('활성 배포와 execute 권한이 있으면 내부 실행 URL을 만든다', () => {
    const row = baseRow();

    expect(canRunDeployedModule(row)).toBe(true);
    expect(getModuleRunDisabledReason(row)).toBeNull();
    expect(buildModuleRunHref(row)).toBe(
      '/modules/workflow-1/run?deploymentId=deployment-1',
    );
  });

  it('execute 권한이 없으면 실행을 막는다', () => {
    const row = baseRow({
      permission: {
        workflow_id: 'workflow-1',
        auth_state: 'viewer',
        can_read: true,
        can_write: false,
        can_execute: false,
        can_deploy: false,
        can_manage: false,
      },
    });

    expect(canRunDeployedModule(row)).toBe(false);
    expect(getModuleRunDisabledReason(row)).toBe('실행 권한 필요');
  });

  it('활성 배포가 없으면 실행 URL을 만들지 않는다', () => {
    const row = baseRow({
      deployment: { state: 'undeployed' },
      deploymentState: 'undeployed',
    });

    expect(canRunDeployedModule(row)).toBe(false);
    expect(getModuleRunDisabledReason(row)).toBe('활성 배포 없음');
    expect(buildModuleRunHref(row)).toBeNull();
  });
});
