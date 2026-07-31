export type SecurityAlertRuleId =
  | 'repeated_permission_denied'
  | 'multi_resource_permission_probe'
  | 'repeated_policy_block';

export type SecurityAlertSeverity = 'medium' | 'high';
export type SecurityAlertStatus = 'open' | 'acknowledged' | 'resolved';
export type SecurityAlertResolutionType =
  | 'mitigated'
  | 'false_positive'
  | 'accepted_risk';
export type SecurityAlertActorState =
  | 'active'
  | 'suspended'
  | 'removed'
  | 'deleted';

export const SECURITY_ALERT_OPERATIONS = {
  LIST: 'security_alert.list',
  SUMMARY: 'security_alert.summary',
  DETAIL: 'security_alert.detail',
  EVIDENCE_LIST: 'security_alert.evidence.list',
  ACKNOWLEDGE: 'security_alert.acknowledge',
  RESOLVE: 'security_alert.resolve',
  REOPEN: 'security_alert.reopen',
} as const;

export type SecurityAlertOperation =
  (typeof SECURITY_ALERT_OPERATIONS)[keyof typeof SECURITY_ALERT_OPERATIONS];

export function isSecurityAlertOperation(
  value: string,
): value is SecurityAlertOperation {
  return (Object.values(SECURITY_ALERT_OPERATIONS) as string[]).includes(value);
}

export type SecurityAlertSafeActor = {
  id: string | null;
  display_name: string | null;
  state: SecurityAlertActorState;
};

export type SecurityAlertListItem = {
  id: string;
  organization_id: string;
  rule_id: SecurityAlertRuleId;
  rule_version: string;
  severity: SecurityAlertSeverity;
  status: SecurityAlertStatus;
  policy_reason: string | null;
  actor: SecurityAlertSafeActor;
  occurrence_count: number;
  first_detected_at: string;
  last_detected_at: string;
  version: number;
  created_at: string;
  updated_at: string;
};

export type SecurityAlertAcknowledgement = {
  by: SecurityAlertSafeActor;
  at: string;
};

export type SecurityAlertResolution = {
  type: SecurityAlertResolutionType;
  reason: string;
  by: SecurityAlertSafeActor;
  at: string;
};

export type SecurityAlertDetail = SecurityAlertListItem & {
  evidence_count: number;
  acknowledged: SecurityAlertAcknowledgement | null;
  resolution: SecurityAlertResolution | null;
};

export type SecurityAlertListResponse = {
  total: number;
  items: SecurityAlertListItem[];
};

export type SecurityAlertSummaryItem = {
  id: string;
  rule_id: SecurityAlertRuleId;
  severity: SecurityAlertSeverity;
  actor: SecurityAlertSafeActor;
  occurrence_count: number;
  last_detected_at: string;
};

export type SecurityAlertSummaryResponse = {
  open_count: number;
  high_open_count: number;
  recent_items: SecurityAlertSummaryItem[];
};

export type SecurityAlertAuditLogItem = {
  id: string;
  occurred_at: string;
  actor_id: string | null;
  actor_type: string;
  category: string;
  action: string;
  target_type: string | null;
  target_id: string | null;
  status: 'success' | 'failure';
  request_id: string | null;
  required_permission?: string | null;
  requested_operation?: SecurityAlertOperation | null;
  denial_reason?: string | null;
};

export type SecurityAlertAuditLogListResponse = {
  total: number;
  items: SecurityAlertAuditLogItem[];
};

export type SecurityAlertListParams = {
  page?: number;
  limit?: number;
  severity?: SecurityAlertSeverity;
  status?: SecurityAlertStatus;
  ruleId?: SecurityAlertRuleId;
  actorId?: string;
  startAt?: string;
  endAt?: string;
};

export type ResolveSecurityAlertInput = {
  expectedVersion: number;
  resolutionType: SecurityAlertResolutionType;
  reason: string;
};
