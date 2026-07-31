import { createContext } from 'react';

import { DraggedOutputVariable } from '../../../utils/nodeVariablePorts';

export type VariableInsertionTargetKind = 'text' | 'selector' | 'mapping';

export type VariableInsertionTarget = {
  id: string;
  kind: VariableInsertionTargetKind;
  label: string;
};

export type VariableInsertionHandler = (
  output: DraggedOutputVariable,
) => boolean;

export type RegisteredVariableTarget = {
  target: VariableInsertionTarget;
  handler: VariableInsertionHandler;
};

export type VariableInsertionContextValue = {
  activeTarget: VariableInsertionTarget | null;
  message: string | null;
  setActiveTarget: (target: VariableInsertionTarget | null) => void;
  clearMessage: () => void;
  registerTarget: (
    target: VariableInsertionTarget,
    handler: VariableInsertionHandler,
  ) => () => void;
  insertOutput: (output: DraggedOutputVariable) => boolean;
};

export const noopVariableInsertionContext: VariableInsertionContextValue = {
  activeTarget: null,
  message: null,
  setActiveTarget: () => undefined,
  clearMessage: () => undefined,
  registerTarget: () => () => undefined,
  insertOutput: () => false,
};

export const VariableInsertionContext =
  createContext<VariableInsertionContextValue | null>(null);
