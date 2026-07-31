// BudgetStatusBadge(공용, FR-052) 계약 테스트 — TDD red phase.
// docs/features/budget-management/component_spec.md:
// status별 라벨(정상/위험/초과)과 사용률 % 정수 반올림 표시(표시 직전 1회 반올림).
import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';

import { BudgetStatusBadge } from './BudgetStatusBadge';

describe('BudgetStatusBadge', () => {
  it('at_risk 상태는 위험 라벨과 반올림된 사용률을 표시한다', () => {
    render(<BudgetStatusBadge status="at_risk" usageRatio={0.923457} />);

    expect(screen.getByText('위험')).toBeInTheDocument();
    expect(screen.getByText('92%')).toBeInTheDocument();
  });

  it('exceeded 상태는 초과 라벨과 100%가 넘는 사용률을 표시한다', () => {
    render(<BudgetStatusBadge status="exceeded" usageRatio={1.204} />);

    expect(screen.getByText('초과')).toBeInTheDocument();
    expect(screen.getByText('120%')).toBeInTheDocument();
  });

  it('normal 상태는 정상 라벨을 표시한다', () => {
    render(<BudgetStatusBadge status="normal" usageRatio={0.5} />);

    expect(screen.getByText('정상')).toBeInTheDocument();
    expect(screen.getByText('50%')).toBeInTheDocument();
  });

  it('usageRatio가 없으면 상태 라벨만 표시한다', () => {
    render(<BudgetStatusBadge status="exceeded" />);

    expect(screen.getByText('초과')).toBeInTheDocument();
    expect(screen.queryByText(/%/)).not.toBeInTheDocument();
  });
});
