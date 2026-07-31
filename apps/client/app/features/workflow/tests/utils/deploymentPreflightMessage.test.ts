import { describe, expect, it } from 'vitest';

import type { DeploymentPreflightResponse } from '../../types/Deployment';
import {
  deploymentApiErrorMessage,
  formatDeploymentPreflightMessage,
} from '../../utils/deploymentPreflightMessage';

const blockedPreflight: DeploymentPreflightResponse = {
  status: 'blocked',
  audience: 'authenticated_user',
  safe_summary: {
    blocked_reason: 'mail_credential_unavailable',
    affected_node_count: 1,
    affected_kb_count_bucket: '0',
  },
  required_actions: [
    {
      action: 'select_available_mail_credential',
      label: '사용 가능한 Mail credential을 선택하세요',
    },
  ],
  warnings: [],
  nodes: [],
};

describe('configuration preflight message', () => {
  it('uses a surface-neutral readiness label', () => {
    expect(formatDeploymentPreflightMessage(blockedPreflight)).toBe(
      [
        '실행 준비 검사에서 차단되었습니다. (mail_credential_unavailable)',
        '필요 조치: 사용 가능한 Mail credential을 선택하세요',
      ].join('\n'),
    );
  });

  it('uses the fixed reason when a typed response omits blocked_reason', () => {
    const withoutReason: DeploymentPreflightResponse = {
      ...blockedPreflight,
      safe_summary: {
        ...blockedPreflight.safe_summary,
        blocked_reason: undefined,
      },
    };

    expect(formatDeploymentPreflightMessage(withoutReason)).toContain(
      '(deployment_preflight_blocked)',
    );
  });

  it('extracts the safe preflight action from workflow 409 errors', () => {
    const message = deploymentApiErrorMessage({
      response: {
        status: 409,
        data: {
          detail: {
            error: {
              code: 'workflow.configuration_preflight.blocked',
              preflight: blockedPreflight,
            },
          },
        },
      },
    });

    expect(message).toContain('mail_credential_unavailable');
    expect(message).toContain('사용 가능한 Mail credential을 선택하세요');
    expect(message).not.toContain('credential-id');
  });

  it('uses the fixed fallback for malformed blocked preflight payloads', () => {
    const message = deploymentApiErrorMessage(
      {
        response: {
          data: {
            detail: {
              error: {
                code: 'workflow.configuration_preflight.blocked',
                preflight: { status: 'blocked' },
              },
            },
          },
        },
      },
      '실행 준비 상태를 확인할 수 없습니다.',
    );

    expect(message).toBe('실행 준비 상태를 확인할 수 없습니다.');
  });

  it('extracts safe actions from the standard API error envelope', () => {
    const message = deploymentApiErrorMessage({
      response: {
        status: 409,
        data: {
          error: {
            code: 'deployment.app_auth_secret_required',
            message: 'Issue an App authentication secret before activation.',
            details: {
              required_actions: [
                'issue_app_auth_secret',
                'untrusted-action-detail',
              ],
            },
          },
        },
      },
    });

    expect(message).toBe(
      ['App Secret이 필요합니다.', '필요 조치: App Secret을 발급하세요'].join(
        '\n',
      ),
    );
    expect(message).not.toContain('untrusted-action-detail');
  });

  it('shows a safe wait state for lifecycle rollout errors', () => {
    const message = deploymentApiErrorMessage({
      response: {
        status: 503,
        data: {
          error: {
            code: 'app.auth_secret_lifecycle_unavailable',
            message: 'internal rollout detail',
            details: {},
          },
        },
      },
    });

    expect(message).toBe('App Secret 기능을 현재 사용할 수 없습니다.');
    expect(message).not.toContain('internal rollout detail');
  });

  it('keeps legacy nested API messages compatible', () => {
    expect(
      deploymentApiErrorMessage({
        response: {
          data: {
            detail: {
              error: { message: '기존 오류 메시지' },
            },
          },
        },
      }),
    ).toBe('기존 오류 메시지');
  });
});
