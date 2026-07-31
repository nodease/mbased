import type { OrganizationMember } from '../types/Organization';
import { filterActiveOrganizationMembers } from '../utils/memberFilters';

type ActiveOrganizationMemberPickerProps = {
  members: OrganizationMember[];
  value: string;
  onChange: (userId: string) => void;
  excludedUserIds?: string[];
  disabled?: boolean;
  placeholder?: string;
  emptyLabel?: string;
  className?: string;
};

export function ActiveOrganizationMemberPicker({
  members,
  value,
  onChange,
  excludedUserIds = [],
  disabled = false,
  placeholder = '멤버 선택',
  emptyLabel = '선택 가능한 활성 멤버 없음',
  className = '',
}: ActiveOrganizationMemberPickerProps) {
  const options = filterActiveOrganizationMembers(members, excludedUserIds);

  return (
    <select
      value={value}
      onChange={(event) => onChange(event.target.value)}
      disabled={disabled || options.length === 0}
      className={`h-10 min-w-0 rounded-md border border-gray-300 px-3 text-sm disabled:cursor-not-allowed disabled:bg-gray-50 disabled:text-gray-400 ${className}`}
    >
      <option value="">{options.length === 0 ? emptyLabel : placeholder}</option>
      {options.map((member) => (
        <option key={member.id} value={member.user_id}>
          {member.user_name} ({member.user_email})
        </option>
      ))}
    </select>
  );
}
