import { budgetRunBlockMessage } from '../../budget/utils/budgetGuard';
import type { ModuleOperationRow } from '../api/moduleOperationsApi';

export const getModuleRunDisabledReason = (
  row: ModuleOperationRow,
): string | null => {
  if (row.permissionStatus !== 'loaded') {
    return '실행 권한 확인 필요';
  }
  if (!row.permission?.can_execute) {
    return '실행 권한 필요';
  }
  if (!row.app.workflow_id) {
    return '연결된 workflow 없음';
  }
  if (row.deploymentState !== 'active' || !row.deployment.deployment_id) {
    return '활성 배포 없음';
  }

  return budgetRunBlockMessage(row.app.budget_status);
};

export const canRunDeployedModule = (row: ModuleOperationRow) =>
  getModuleRunDisabledReason(row) === null;

export const buildModuleRunHref = (row: ModuleOperationRow): string | null => {
  if (!row.app.workflow_id || !row.deployment.deployment_id) return null;

  const query = new URLSearchParams({
    deploymentId: row.deployment.deployment_id,
  });
  return `/modules/${row.app.workflow_id}/run?${query.toString()}`;
};
