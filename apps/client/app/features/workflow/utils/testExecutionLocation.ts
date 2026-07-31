export const TEST_RUN_QUERY_KEY = 'testRun';
export const TEST_NODE_QUERY_KEY = 'testNode';
export const TEST_COMPARISON_QUERY_KEY = 'testComparison';
export const TEST_COMPARISON_BASELINE_QUERY_KEY = 'comparisonBaseline';
export const TEST_COMPARISON_NODE_QUERY_KEY = 'comparisonNode';
export const TEST_COMPARISON_ENABLED_VALUE = '1';

const TEST_EXECUTION_LOCATION_QUERY_KEYS = [
  TEST_RUN_QUERY_KEY,
  TEST_NODE_QUERY_KEY,
  TEST_COMPARISON_QUERY_KEY,
  TEST_COMPARISON_BASELINE_QUERY_KEY,
  TEST_COMPARISON_NODE_QUERY_KEY,
] as const;

type SearchParamsReader = Pick<URLSearchParams, 'get'>;
type SearchParamsWriter = Pick<URLSearchParams, 'set'>;

export const copyTestExecutionLocationQueryParams = (
  source: SearchParamsReader,
  target: SearchParamsWriter,
) => {
  for (const key of TEST_EXECUTION_LOCATION_QUERY_KEYS) {
    const value = source.get(key);
    if (value) {
      target.set(key, value);
    }
  }
};
