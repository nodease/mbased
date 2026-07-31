export type MembershipState = 'invited' | 'active' | 'suspended' | 'removed';

export type OrganizationAuthState = 'member' | 'manager';

export type OrganizationResponse = {
  id: string;
  name: string;
  is_manager: boolean;
};

export type OrganizationSummary = {
  id: string;
  name: string;
  membership_state: MembershipState;
  organization_auth_state: OrganizationAuthState;
  is_active: boolean;
};

export type OrganizationMember = {
  id: string;
  organization_id: string;
  user_id: string;
  user_email: string;
  user_name: string;
  membership_state: MembershipState;
  organization_auth_state: OrganizationAuthState;
  invited_by: string | null;
  invited_at: string | null;
  accepted_at: string | null;
  removed_at: string | null;
  created_at: string;
  updated_at: string;
};

export type MemberCurrentMonthUsage = {
  total_cost: number;
  workflow_execution_cost: number;
  agent_builder_cost: number;
  usage_data_complete: boolean;
  unresolved_provider_call_count: number;
};

export type OrganizationMemberListItem = OrganizationMember & {
  current_month_usage: MemberCurrentMonthUsage;
};

export type OrganizationMemberInviteRequest = {
  user_id: string;
  organization_auth_state?: OrganizationAuthState;
};

export type OrganizationMemberUpdateRequest = {
  membership_state?: Extract<MembershipState, 'active' | 'suspended'> | null;
  organization_auth_state?: OrganizationAuthState | null;
};

export type OrganizationMemberRemoveResponse = {
  status: string;
  removed_team_memberships: number;
  revoked_user_permissions: {
    workflow: number;
    llm_credential: number;
    knowledge_base: number;
    audit: number;
  };
};

export type PermissionRequestCreateRequest = {
  reason: string;
  requested_permission?: 'app.create';
};

export type PermissionRequestResponse = {
  id: string;
  user?: {
    id: string;
    name: string;
    email: string;
  } | null;
  requested_permission: 'app.create';
  reason: string;
  status: 'pending' | 'approved' | 'rejected';
  created_at: string;
  decided_by: string | null;
  decided_at: string | null;
};
