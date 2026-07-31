export type AuditLogStatus = 'success' | 'failure';

export type AuditDisplayReference = {
  label: string;
  source: 'event_snapshot' | 'current_resource';
};

export type AuditLogItem = {
  id: string;
  occurred_at: string;
  actor_id: string | null;
  actor_display?: AuditDisplayReference | null;
  actor_type: string;
  category: string;
  action: string;
  target_type: string | null;
  target_id: string | null;
  target_display?: AuditDisplayReference | null;
  status: AuditLogStatus;
  request_id?: string | null;
};

export type AuditLogListResponse = {
  total: number | null;
  next_cursor?: string | null;
  items: AuditLogItem[];
};

export type AuditLogDetailResponse = AuditLogItem & {
  audit_metadata: Record<string, unknown>;
  resolved_references?: Record<string, AuditDisplayReference>;
  change_summary?: {
    before: Record<string, unknown> | null;
    after: Record<string, unknown> | null;
  } | null;
};

export type AuditLogSearchFilters = {
  actorId?: string;
  action?: string;
  targetType?: string;
  targetId?: string;
  status?: AuditLogStatus;
  startAt?: string;
  endAt?: string;
};
