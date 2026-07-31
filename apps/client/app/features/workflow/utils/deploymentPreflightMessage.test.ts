import { describe, expect, it } from 'vitest';

import type { DeploymentPreflightResponse } from '../types/Deployment';
import { formatDeploymentPreflightMessage } from './deploymentPreflightMessage';

function preflight(
  overrides: Partial<DeploymentPreflightResponse['safe_summary']> = {},
): DeploymentPreflightResponse {
  return {
    status: 'blocked',
    audience: 'anonymous_public',
    safe_summary: {
      blocked_reason: 'private_collection_requires_execution_subject',
      affected_node_count: 1,
      affected_kb_count_bucket: '0',
      affected_collection_count_bucket: '1',
      candidate_budget_limited: false,
      ...overrides,
    },
    required_actions: [
      {
        action: 'remove_private_collection_or_use_authenticated_run',
        label: 'Private Collection을 제거하거나 인증 실행 경로를 사용하세요',
      },
    ],
    warnings: [],
    nodes: [],
  };
}

describe('formatDeploymentPreflightMessage', () => {
  it('Collection blocker를 identity 없이 표시한다', () => {
    const message = formatDeploymentPreflightMessage(preflight());

    expect(message).toContain('영향 Collection 수: 1');
    expect(message).toContain('Private Collection을 제거');
    expect(message).not.toContain('collection-id');
  });

  it('candidate budget 경고를 표시한다', () => {
    const message = formatDeploymentPreflightMessage(
      preflight({ candidate_budget_limited: true }),
    );

    expect(message).toContain('최대 후보 수로 제한될 수 있습니다');
  });

  it('이전 Gateway 응답에는 Collection 수를 임의 표시하지 않는다', () => {
    const legacy = preflight({
      affected_collection_count_bucket: undefined,
      candidate_budget_limited: undefined,
    });

    const message = formatDeploymentPreflightMessage(legacy);

    expect(message).not.toContain('undefined');
    expect(message).not.toContain('영향 Collection 수');
  });
});
