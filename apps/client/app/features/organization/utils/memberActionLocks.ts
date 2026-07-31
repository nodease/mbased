type MemberActionLockInput = {
  isSelf: boolean;
  isSelfUnknown: boolean;
  isLastActiveManager: boolean;
  actionPending: boolean;
};

type LockedAction = {
  disabled: boolean;
  title?: string;
};

type MemberActionLocks = {
  statusAction: LockedAction;
  memberUpdateAction: LockedAction;
  managerOrRemoveAction: LockedAction;
};

const getBlockTitle = ({
  isSelf,
  isSelfUnknown,
}: Pick<MemberActionLockInput, 'isSelf' | 'isSelfUnknown'>) => {
  if (isSelfUnknown) {
    return '현재 사용자 확인 전에는 위험 작업을 할 수 없습니다.';
  }
  if (isSelf) {
    return '자기 자신에게는 이 작업을 할 수 없습니다.';
  }
  return '마지막 관리자는 변경할 수 없습니다.';
};

const lock = (isBlocked: boolean, actionPending: boolean, title: string) => ({
  disabled: actionPending || isBlocked,
  title: isBlocked ? title : undefined,
});

export function getMemberActionLocks(
  input: MemberActionLockInput,
): MemberActionLocks {
  const { isSelf, isSelfUnknown, isLastActiveManager, actionPending } = input;
  const blockTitle = getBlockTitle({ isSelf, isSelfUnknown });
  const blocksSelfMutation = isSelfUnknown || isSelf;
  const blocksManagerSafety = blocksSelfMutation || isLastActiveManager;

  return {
    statusAction: lock(blocksManagerSafety, actionPending, blockTitle),
    memberUpdateAction: lock(blocksSelfMutation, actionPending, blockTitle),
    managerOrRemoveAction: lock(blocksManagerSafety, actionPending, blockTitle),
  };
}
