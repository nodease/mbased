export type ActorMembershipState = 'active' | 'suspended';
export type ActorOrganizationAuthState = 'member' | 'manager';
export type ActorResourceType =
  | 'workflow'
  | 'knowledge_base'
  | 'llm_credential'
  | 'mail_credential';
export type ActorResourceAuthState =
  | 'none'
  | 'viewer'
  | 'operator'
  | 'builder'
  | 'manager';
export type ActorGrantAuthState = Exclude<ActorResourceAuthState, 'none'>;

export type AccessControl = {
  allowed: boolean;
  reason: string | null;
};

export type MemberAccessProfile = {
  member: {
    membership_id: string;
    user_id: string;
    name: string;
    email: string;
    user_active: boolean;
    membership_state: ActorMembershipState;
    organization_auth_state: ActorOrganizationAuthState;
    updated_at: string;
  };
  control: {
    is_self: boolean;
    is_last_active_manager: boolean;
    manager_override: boolean;
    actions: {
      membership_suspend: AccessControl;
      membership_reactivate: AccessControl;
      organization_role_set: {
        member: AccessControl;
        manager: AccessControl;
      };
      team_membership_add: AccessControl;
      team_membership_remove: AccessControl;
      direct_permission_grant: AccessControl;
      direct_permission_revoke: AccessControl;
      app_creation_grant: AccessControl;
      app_creation_revoke: AccessControl;
    };
  };
  effective_access_enabled: boolean;
  team_membership_count: number;
  app_creation: {
    effective: boolean;
    effective_source: 'manager_override' | 'direct' | 'none';
    direct_permission: {
      permission_id: string;
      assigned_at: string;
    } | null;
  };
  permission_counts: {
    direct: number;
    team_inherited: number;
  };
};

export type MemberTeamMembership = {
  team_membership_id: string;
  team_id: string;
  name: string;
  is_active: boolean;
  assigned_at: string;
  inherited_resource_counts: {
    workflow: number;
    knowledge_base: number;
    llm_credential: number;
    mail_credential?: number;
    total: number;
  };
};

export type MemberTeamMembershipList = {
  total: number;
  items: MemberTeamMembership[];
};

export type MemberResourceAccess = {
  resource_type: ActorResourceType;
  resource_id: string;
  resource_name: string;
  effective_auth_state: ActorResourceAuthState;
  direct_permission: {
    permission_id: string;
    auth_state: ActorResourceAuthState;
    assigned_at: string;
  } | null;
  team_sources: Array<{
    team_membership_id: string;
    team_id: string;
    team_name: string;
    auth_state: ActorGrantAuthState;
  }>;
};

export type MemberResourceAccessList = {
  total: number;
  items: MemberResourceAccess[];
};

type CommonAction = {
  expected_membership_id: string;
  expected_user_active: boolean;
  expected_membership_state: ActorMembershipState;
  expected_organization_auth_state: ActorOrganizationAuthState;
  reason?: string | null;
};

export type MemberAccessAction = CommonAction &
  (
    | { action: 'membership.suspend' }
    | { action: 'membership.reactivate' }
    | {
        action: 'organization_role.set';
        role: ActorOrganizationAuthState;
      }
    | {
        action: 'team_membership.add';
        team_id: string;
        expected_absent: true;
      }
    | {
        action: 'team_membership.remove';
        team_id: string;
        expected_team_membership_id: string;
      }
    | {
        action: 'direct_permission.grant';
        resource_type: ActorResourceType;
        resource_id: string;
        auth_state: ActorGrantAuthState;
        expected_absent: true;
      }
    | {
        action: 'direct_permission.grant';
        resource_type: ActorResourceType;
        resource_id: string;
        auth_state: ActorGrantAuthState;
        expected_permission_id: string;
        expected_auth_state: ActorResourceAuthState;
      }
    | {
        action: 'direct_permission.revoke';
        resource_type: ActorResourceType;
        resource_id: string;
        expected_permission_id: string;
        expected_auth_state: ActorResourceAuthState;
      }
    | { action: 'app_creation.grant'; expected_absent: true }
    | {
        action: 'app_creation.revoke';
        expected_permission_id: string;
      }
  );

export type MemberAccessActionResponse = {
  status: 'applied' | 'unchanged';
  action: string;
  target_type: string;
  target_id: string | null;
  effective_access_changed: boolean | null;
  affected_resource_source_count: number | null;
};

export type ActorAccessTeamCatalogItem = {
  id: string;
  name: string;
  is_active: boolean;
};

export type ActorAccessResourceCatalogItem = {
  id: string;
  name: string;
  resourceType: ActorResourceType;
};
