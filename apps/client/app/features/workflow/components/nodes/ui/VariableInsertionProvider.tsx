'use client';

import { ReactNode, useCallback, useMemo, useRef, useState } from 'react';

import { DraggedOutputVariable } from '../../../utils/nodeVariablePorts';
import {
  RegisteredVariableTarget,
  VariableInsertionContext,
  VariableInsertionHandler,
  VariableInsertionTarget,
} from './variableInsertionContext';

export function VariableInsertionProvider({
  children,
}: {
  children: ReactNode;
}) {
  const targetsRef = useRef(new Map<string, RegisteredVariableTarget>());
  const [activeTarget, setActiveTargetState] =
    useState<VariableInsertionTarget | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  const setActiveTarget = useCallback(
    (target: VariableInsertionTarget | null) => {
      setActiveTargetState(target);
      if (target) setMessage(null);
    },
    [],
  );

  const clearMessage = useCallback(() => setMessage(null), []);

  const registerTarget = useCallback(
    (target: VariableInsertionTarget, handler: VariableInsertionHandler) => {
      const registration = { target, handler };
      targetsRef.current.set(target.id, registration);

      return () => {
        const currentRegistration = targetsRef.current.get(target.id);
        if (currentRegistration !== registration) return;

        targetsRef.current.delete(target.id);
        window.setTimeout(() => {
          setActiveTargetState((current) =>
            current?.id === target.id && !targetsRef.current.has(target.id)
              ? null
              : current,
          );
        }, 0);
      };
    },
    [],
  );

  const insertOutput = useCallback(
    (output: DraggedOutputVariable) => {
      if (!activeTarget) {
        setMessage('먼저 가운데 설정에서 변수를 넣을 필드를 선택하세요.');
        return false;
      }

      const registeredTarget = targetsRef.current.get(activeTarget.id);
      if (!registeredTarget) {
        setMessage(
          '선택한 입력 위치를 찾을 수 없습니다. 다시 필드를 선택하세요.',
        );
        setActiveTargetState(null);
        return false;
      }

      const inserted = registeredTarget.handler(output);
      if (!inserted) {
        setMessage('이 필드에는 해당 변수를 넣을 수 없습니다.');
        return false;
      }

      setMessage(`${registeredTarget.target.label}에 변수를 추가했습니다.`);
      return true;
    },
    [activeTarget],
  );

  const value = useMemo(
    () => ({
      activeTarget,
      message,
      setActiveTarget,
      clearMessage,
      registerTarget,
      insertOutput,
    }),
    [
      activeTarget,
      clearMessage,
      insertOutput,
      message,
      registerTarget,
      setActiveTarget,
    ],
  );

  return (
    <VariableInsertionContext.Provider value={value}>
      {children}
    </VariableInsertionContext.Provider>
  );
}
