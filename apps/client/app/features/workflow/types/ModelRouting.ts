/** Judge-first + 점진적 로컬 학습에서 실행 가능한 유일한 active policy. */
export interface JudgeFirstActivePolicy {
  strategy_id?: 'judge_bootstrap_incremental_v1';
  policy_version?: string;
  default_model_id?: string;
  fallback_model_id?: string | null;
  judge_model_id?: string;
  candidate_model_ids?: string[];
}
