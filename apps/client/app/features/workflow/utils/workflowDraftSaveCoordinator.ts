export type WorkflowDraftSaveOwner =
  | 'agent_builder'
  | 'autosync'
  | 'test_preflight'
  | 'undo_redo'
  | 'version_restore';

export type ReleaseWorkflowDraftSave = () => void;

type WorkflowDraftSaveWaiter = {
  owner: WorkflowDraftSaveOwner;
  resolve: (release: ReleaseWorkflowDraftSave) => void;
};

type WorkflowDraftSaveQueue = {
  owner: WorkflowDraftSaveOwner;
  waiters: WorkflowDraftSaveWaiter[];
};

const queues = new Map<string, WorkflowDraftSaveQueue>();

const createRelease = (
  workflowId: string,
  queue: WorkflowDraftSaveQueue,
): ReleaseWorkflowDraftSave => {
  let released = false;
  return () => {
    if (released) return;
    released = true;

    const next = queue.waiters.shift();
    if (!next) {
      if (queues.get(workflowId) === queue) queues.delete(workflowId);
      return;
    }

    queue.owner = next.owner;
    next.resolve(createRelease(workflowId, queue));
  };
};

export const tryAcquireWorkflowDraftSave = (
  workflowId: string,
  owner: WorkflowDraftSaveOwner,
): ReleaseWorkflowDraftSave | null => {
  if (queues.has(workflowId)) return null;

  const queue: WorkflowDraftSaveQueue = { owner, waiters: [] };
  queues.set(workflowId, queue);
  return createRelease(workflowId, queue);
};

export const acquireWorkflowDraftSave = async (
  workflowId: string,
  owner: WorkflowDraftSaveOwner,
): Promise<ReleaseWorkflowDraftSave> => {
  const immediate = tryAcquireWorkflowDraftSave(workflowId, owner);
  if (immediate) return immediate;

  const queue = queues.get(workflowId);
  if (!queue) return acquireWorkflowDraftSave(workflowId, owner);

  return new Promise<ReleaseWorkflowDraftSave>((resolve) => {
    queue.waiters.push({ owner, resolve });
  });
};

export const getWorkflowDraftSaveOwner = (
  workflowId: string,
): WorkflowDraftSaveOwner | null => queues.get(workflowId)?.owner ?? null;

export const startWorkflowExecutionFromPreflight = <T>(
  workflowId: string,
  releasePreflight: ReleaseWorkflowDraftSave,
  start: () => Promise<T>,
):
  | { started: true; value: Promise<T> }
  | { started: false; blockedBy: WorkflowDraftSaveOwner | null } => {
  const queue = queues.get(workflowId);
  if (!queue || queue.owner !== 'test_preflight') {
    releasePreflight();
    return { started: false, blockedBy: queue?.owner ?? null };
  }

  const queuedSave = queue.waiters[0];
  if (queuedSave) {
    releasePreflight();
    return { started: false, blockedBy: queuedSave.owner };
  }

  try {
    const value = start().catch((error) => {
      releasePreflight();
      throw error;
    });
    return { started: true, value };
  } catch (error) {
    releasePreflight();
    throw error;
  }
};

export const clearWorkflowDraftSaveCoordinatorForTests = () => {
  queues.clear();
};
