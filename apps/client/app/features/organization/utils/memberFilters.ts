import type { OrganizationMember } from '../types/Organization';

export function filterActiveOrganizationMembers(
  members: OrganizationMember[],
  excludedUserIds: string[] = [],
) {
  const excluded = new Set(excludedUserIds);
  return members.filter(
    (member) =>
      member.membership_state === 'active' && !excluded.has(member.user_id),
  );
}
