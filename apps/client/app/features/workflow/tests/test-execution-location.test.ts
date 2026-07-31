import { describe, expect, it } from 'vitest';

import { copyTestExecutionLocationQueryParams } from '../utils/testExecutionLocation';

describe('test execution location', () => {
  it('보고 화면 이동 시 실행 비교 복원에 필요한 query를 함께 전달한다', () => {
    const source = new URLSearchParams({
      tab: 'logs',
      testRun: 'current-run',
      testNode: 'llm-triage',
      testComparison: '1',
      comparisonBaseline: 'baseline-run',
      comparisonNode: 'llm-triage',
    });
    const target = new URLSearchParams({ tab: 'monitoring' });

    copyTestExecutionLocationQueryParams(source, target);

    expect(target.toString()).toBe(
      'tab=monitoring&testRun=current-run&testNode=llm-triage&testComparison=1&comparisonBaseline=baseline-run&comparisonNode=llm-triage',
    );
  });
});
