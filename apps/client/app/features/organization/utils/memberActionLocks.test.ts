import { describe, expect, it } from 'vitest';
import { getMemberActionLocks } from './memberActionLocks';

describe('getMemberActionLocks', () => {
  it('disables status actions for the current user', () => {
    const locks = getMemberActionLocks({
      isSelf: true,
      isSelfUnknown: false,
      isLastActiveManager: false,
      actionPending: false,
    });

    expect(locks.statusAction).toEqual({
      disabled: true,
      title: '자기 자신에게는 이 작업을 할 수 없습니다.',
    });
  });

  it('disables status and manager safety actions for the last active manager', () => {
    const locks = getMemberActionLocks({
      isSelf: false,
      isSelfUnknown: false,
      isLastActiveManager: true,
      actionPending: false,
    });

    expect(locks.statusAction).toEqual({
      disabled: true,
      title: '마지막 관리자는 변경할 수 없습니다.',
    });
    expect(locks.managerOrRemoveAction).toEqual({
      disabled: true,
      title: '마지막 관리자는 변경할 수 없습니다.',
    });
    expect(locks.memberUpdateAction).toEqual({
      disabled: false,
      title: undefined,
    });
  });

  it('disables every member action while the current user is unknown', () => {
    const locks = getMemberActionLocks({
      isSelf: false,
      isSelfUnknown: true,
      isLastActiveManager: false,
      actionPending: false,
    });

    expect(locks.statusAction.disabled).toBe(true);
    expect(locks.memberUpdateAction.disabled).toBe(true);
    expect(locks.managerOrRemoveAction.disabled).toBe(true);
    expect(locks.statusAction.title).toBe(
      '현재 사용자 확인 전에는 위험 작업을 할 수 없습니다.',
    );
  });

  it('keeps the status action disabled during pending work without changing the tooltip', () => {
    const locks = getMemberActionLocks({
      isSelf: false,
      isSelfUnknown: false,
      isLastActiveManager: false,
      actionPending: true,
    });

    expect(locks.statusAction).toEqual({
      disabled: true,
      title: undefined,
    });
  });
});
