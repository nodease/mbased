import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { AutomaticOptimizationManagementModal } from './AutomaticOptimizationManagementModal';

afterEach(cleanup);

describe('AutomaticOptimizationManagementModal', () => {
  it('keeps the deployment target nodes while updating cadence and budget', async () => {
    const onSave = vi.fn().mockResolvedValue(undefined);

    render(
      <AutomaticOptimizationManagementModal
        workflowName="고객 티켓 처리"
        summary={{
          enabled: true,
          status: 'collecting',
          node_ids: ['llm-triage'],
          node_count: 1,
          collected_runs: 14,
          check_every_runs: 50,
          validation_spend_usd: 0.18,
          monthly_validation_budget_usd: 3,
        }}
        onClose={vi.fn()}
        onSave={onSave}
      />,
    );

    expect(screen.getByText('14 / 50회')).toBeVisible();
    fireEvent.change(screen.getByRole('slider', { name: '자동 점검 주기' }), {
      target: { value: '70' },
    });
    fireEvent.change(screen.getByRole('slider', { name: '월간 검증 예산' }), {
      target: { value: '5' },
    });
    fireEvent.click(screen.getByRole('button', { name: '저장' }));

    await waitFor(() =>
      expect(onSave).toHaveBeenCalledWith({
        enabled: true,
        node_ids: ['llm-triage'],
        check_every_runs: 70,
        monthly_validation_budget_usd: 5,
      }),
    );
  });
});
