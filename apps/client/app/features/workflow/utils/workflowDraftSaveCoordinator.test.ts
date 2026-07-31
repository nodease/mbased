import { afterEach, describe, expect, it } from 'vitest';

import {
  acquireWorkflowDraftSave,
  clearWorkflowDraftSaveCoordinatorForTests,
  getWorkflowDraftSaveOwner,
  startWorkflowExecutionFromPreflight,
  tryAcquireWorkflowDraftSave,
} from './workflowDraftSaveCoordinator';

describe('workflowDraftSaveCoordinator', () => {
  afterEach(() => {
    clearWorkflowDraftSaveCoordinatorForTests();
  });

  it('같은 workflow의 두 저장을 동시에 허용하지 않는다', () => {
    const releaseAgentBuilder = tryAcquireWorkflowDraftSave(
      'workflow-1',
      'agent_builder',
    );

    expect(releaseAgentBuilder).not.toBeNull();
    expect(getWorkflowDraftSaveOwner('workflow-1')).toBe('agent_builder');
    expect(
      tryAcquireWorkflowDraftSave('workflow-1', 'test_preflight'),
    ).toBeNull();

    releaseAgentBuilder?.();

    const releaseTest = tryAcquireWorkflowDraftSave(
      'workflow-1',
      'test_preflight',
    );
    expect(releaseTest).not.toBeNull();
    releaseTest?.();
  });

  it('대기 저장은 앞선 저장 해제 뒤에만 시작한다', async () => {
    const releaseTest = tryAcquireWorkflowDraftSave(
      'workflow-1',
      'test_preflight',
    );
    let acquired = false;
    const waiting = acquireWorkflowDraftSave(
      'workflow-1',
      'agent_builder',
    ).then((release) => {
      acquired = true;
      return release;
    });

    await Promise.resolve();
    expect(acquired).toBe(false);

    releaseTest?.();
    const releaseAgentBuilder = await waiting;
    expect(acquired).toBe(true);
    expect(getWorkflowDraftSaveOwner('workflow-1')).toBe('agent_builder');
    releaseAgentBuilder();
  });

  it('다른 workflow의 저장은 서로 차단하지 않는다', () => {
    const releaseFirst = tryAcquireWorkflowDraftSave(
      'workflow-1',
      'agent_builder',
    );
    const releaseSecond = tryAcquireWorkflowDraftSave(
      'workflow-2',
      'test_preflight',
    );

    expect(releaseFirst).not.toBeNull();
    expect(releaseSecond).not.toBeNull();
    releaseFirst?.();
    releaseSecond?.();
  });

  it.each([
    'agent_builder',
    'autosync',
    'undo_redo',
    'version_restore',
    'test_preflight',
  ] as const)(
    '%s 저장이 대기하면 test stream을 시작하지 않고 owner를 넘긴다',
    async (owner) => {
      const releaseTest = tryAcquireWorkflowDraftSave(
        'workflow-1',
        'test_preflight',
      );
      const waitingSave = acquireWorkflowDraftSave('workflow-1', owner);
      let started = false;

      const result = startWorkflowExecutionFromPreflight(
        'workflow-1',
        releaseTest!,
        () => {
          started = true;
          return Promise.resolve();
        },
      );

      expect(result).toEqual({ started: false, blockedBy: owner });
      expect(started).toBe(false);
      const releaseWaitingSave = await waitingSave;
      expect(getWorkflowDraftSaveOwner('workflow-1')).toBe(owner);
      releaseWaitingSave();
    },
  );

  it('대기 저장이 없으면 stream 시작 뒤에도 snapshot 확정 전까지 preflight owner를 유지한다', async () => {
    const releaseTest = tryAcquireWorkflowDraftSave(
      'workflow-1',
      'test_preflight',
    );

    const result = startWorkflowExecutionFromPreflight(
      'workflow-1',
      releaseTest!,
      () => {
        expect(getWorkflowDraftSaveOwner('workflow-1')).toBe('test_preflight');
        return Promise.resolve('stream-started');
      },
    );

    expect(result.started).toBe(true);
    if (!result.started) throw new Error('test stream did not start');
    await expect(result.value).resolves.toBe('stream-started');
    expect(getWorkflowDraftSaveOwner('workflow-1')).toBe('test_preflight');

    releaseTest?.();
    expect(getWorkflowDraftSaveOwner('workflow-1')).toBeNull();
  });

  it('stream 시작 뒤 들어온 저장은 snapshot 확정 lock 해제 전까지 대기한다', async () => {
    const releaseTest = tryAcquireWorkflowDraftSave(
      'workflow-1',
      'test_preflight',
    );
    const result = startWorkflowExecutionFromPreflight(
      'workflow-1',
      releaseTest!,
      () => Promise.resolve(),
    );
    expect(result.started).toBe(true);

    let acquired = false;
    const waitingAutosync = acquireWorkflowDraftSave(
      'workflow-1',
      'autosync',
    ).then((release) => {
      acquired = true;
      return release;
    });

    await Promise.resolve();
    expect(acquired).toBe(false);

    releaseTest?.();
    const releaseAutosync = await waitingAutosync;
    expect(acquired).toBe(true);
    expect(getWorkflowDraftSaveOwner('workflow-1')).toBe('autosync');
    releaseAutosync();
  });

  it('stream 시작 요청이 실패하면 preflight owner를 해제한다', async () => {
    const releaseTest = tryAcquireWorkflowDraftSave(
      'workflow-1',
      'test_preflight',
    );
    const result = startWorkflowExecutionFromPreflight(
      'workflow-1',
      releaseTest!,
      () => Promise.reject(new Error('stream start failed')),
    );

    expect(result.started).toBe(true);
    if (!result.started) throw new Error('test stream did not start');
    await expect(result.value).rejects.toThrow('stream start failed');
    expect(getWorkflowDraftSaveOwner('workflow-1')).toBeNull();
  });

  it('version restore participates in the same workflow save queue', async () => {
    const releaseAgentBuilder = tryAcquireWorkflowDraftSave(
      'workflow-1',
      'agent_builder',
    );
    let acquired = false;
    const waitingRestore = acquireWorkflowDraftSave(
      'workflow-1',
      'version_restore',
    ).then((release) => {
      acquired = true;
      return release;
    });

    await Promise.resolve();
    expect(acquired).toBe(false);

    releaseAgentBuilder?.();
    const releaseRestore = await waitingRestore;
    expect(acquired).toBe(true);
    expect(getWorkflowDraftSaveOwner('workflow-1')).toBe('version_restore');
    releaseRestore();
  });
});
