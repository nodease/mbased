import { useContext } from 'react';

import {
  noopVariableInsertionContext,
  VariableInsertionContext,
} from './variableInsertionContext';

export const useVariableInsertion = () =>
  useContext(VariableInsertionContext) ?? noopVariableInsertionContext;
