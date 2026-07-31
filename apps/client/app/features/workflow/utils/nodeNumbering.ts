import type { Features } from '../types/Workflow';
import type { AppNode } from '../types/Nodes';

export const NODE_NUMBER_FEATURE_KEY = 'nextNodeDisplayNumber';

const isValidNodeNumber = (value: unknown): value is number =>
  typeof value === 'number' && Number.isInteger(value) && value > 0;

const SINGLE_DIGIT_NODE_NUMBERS = [1, 2, 3, 4, 5, 6, 7, 8, 9];
const PREFERRED_TENS_DIGITS = [2, 3, 4, 5, 6, 7, 8, 9, 1];
const PREFERRED_ONES_DIGITS = [1, 2, 3, 4, 5, 6, 7, 8, 9, 0];
const TWO_DIGIT_MIN = 10;
const TWO_DIGIT_MAX = 99;

export const shouldAssignNodeDisplayNumber = (node: AppNode) =>
  node.type !== 'note';

const withoutNodeDisplayNumber = <T extends AppNode>(node: T): T => {
  if (!('displayNumber' in node.data)) {
    return node;
  }

  const nextNode = {
    ...node,
    data: { ...node.data },
  } as T;
  delete nextNode.data.displayNumber;
  return nextNode;
};

const getUsedNodeNumbers = (nodes: AppNode[]) => {
  const used = new Set<number>();

  nodes.forEach((node) => {
    if (!shouldAssignNodeDisplayNumber(node)) {
      return;
    }

    const value = node.data?.displayNumber;
    if (isValidNodeNumber(value)) {
      used.add(value);
    }
  });

  return used;
};

const getPreferredOrderIndex = (order: number[], value: number) => {
  const index = order.indexOf(value);
  return index === -1 ? Number.MAX_SAFE_INTEGER : index;
};

const getLeadingDigitLoad = (usedNumbers: Set<number>, leadingDigit: number) => {
  const leadingDigitText = String(leadingDigit);
  let load = 0;

  usedNumbers.forEach((value) => {
    if (String(value).startsWith(leadingDigitText)) {
      load += 1;
    }
  });

  return load;
};

const getNextAvailableNodeNumber = (usedNumbers: Set<number>) => {
  const singleDigit = SINGLE_DIGIT_NODE_NUMBERS.find(
    (value) => !usedNumbers.has(value),
  );
  if (singleDigit) {
    return singleDigit;
  }

  const twoDigitCandidates = Array.from(
    { length: TWO_DIGIT_MAX - TWO_DIGIT_MIN + 1 },
    (_, index) => index + TWO_DIGIT_MIN,
  ).filter((value) => !usedNumbers.has(value));

  if (twoDigitCandidates.length > 0) {
    return twoDigitCandidates.sort((a, b) => {
      const aTens = Math.floor(a / 10);
      const bTens = Math.floor(b / 10);
      const aOnes = a % 10;
      const bOnes = b % 10;

      return (
        getLeadingDigitLoad(usedNumbers, aTens) -
          getLeadingDigitLoad(usedNumbers, bTens) ||
        getPreferredOrderIndex(PREFERRED_TENS_DIGITS, aTens) -
          getPreferredOrderIndex(PREFERRED_TENS_DIGITS, bTens) ||
        getPreferredOrderIndex(PREFERRED_ONES_DIGITS, aOnes) -
          getPreferredOrderIndex(PREFERRED_ONES_DIGITS, bOnes) ||
        a - b
      );
    })[0];
  }

  let nextNumber = 100;
  while (usedNumbers.has(nextNumber)) {
    nextNumber += 1;
  }
  return nextNumber;
};

export const getNextNodeDisplayNumber = (nodes: AppNode[]) => {
  return getNextAvailableNodeNumber(getUsedNodeNumbers(nodes));
};

export const withNodeDisplayNumber = <T extends AppNode>(
  node: T,
  displayNumber: number,
): T => ({
  ...node,
  data: {
    ...node.data,
    displayNumber,
  },
});

export const createNumberedNode = <T extends AppNode>(
  node: T,
  nodes: AppNode[],
) => {
  if (!shouldAssignNodeDisplayNumber(node)) {
    return {
      node: withoutNodeDisplayNumber(node),
      nextNodeDisplayNumber: getNextNodeDisplayNumber(nodes),
    };
  }

  const displayNumber = getNextNodeDisplayNumber(nodes);
  return {
    node: withNodeDisplayNumber(node, displayNumber),
    nextNodeDisplayNumber: displayNumber + 1,
  };
};

export const assignNewNodeDisplayNumbers = <T extends AppNode>(
  nodesToNumber: T[],
  existingNodes: AppNode[],
  features?: Features,
) => {
  const usedNodeNumbers = getUsedNodeNumbers(existingNodes);

  const nodes = nodesToNumber.map((node) => {
    if (!shouldAssignNodeDisplayNumber(node)) {
      return withoutNodeDisplayNumber(node);
    }

    const nextNodeDisplayNumber = getNextAvailableNodeNumber(usedNodeNumbers);
    const numberedNode = withNodeDisplayNumber(node, nextNodeDisplayNumber);
    usedNodeNumbers.add(nextNodeDisplayNumber);
    return numberedNode;
  });

  return {
    nodes,
    features: {
      ...(features || {}),
      [NODE_NUMBER_FEATURE_KEY]: getNextAvailableNodeNumber(usedNodeNumbers),
    },
  };
};

export const assignMissingNodeDisplayNumbers = (
  nodes: AppNode[],
  features?: Features,
) => {
  let changed = false;
  const usedNodeNumbers = new Set<number>();

  const numberedNodes = nodes.map((node) => {
    if (!shouldAssignNodeDisplayNumber(node)) {
      const nodeWithoutNumber = withoutNodeDisplayNumber(node);
      changed = changed || nodeWithoutNumber !== node;
      return nodeWithoutNumber;
    }

    const currentNodeNumber = node.data?.displayNumber;

    if (
      isValidNodeNumber(currentNodeNumber) &&
      !usedNodeNumbers.has(currentNodeNumber)
    ) {
      usedNodeNumbers.add(currentNodeNumber);
      return node;
    }

    changed = true;
    const nextNodeDisplayNumber = getNextAvailableNodeNumber(usedNodeNumbers);
    const numberedNode = withNodeDisplayNumber(node, nextNodeDisplayNumber);
    usedNodeNumbers.add(nextNodeDisplayNumber);
    return numberedNode;
  });

  const savedNext = features?.[NODE_NUMBER_FEATURE_KEY];
  const nextNodeDisplayNumber = getNextAvailableNodeNumber(usedNodeNumbers);
  const shouldUpdateFeature =
    !isValidNodeNumber(savedNext) || savedNext !== nextNodeDisplayNumber;

  return {
    nodes: changed ? numberedNodes : nodes,
    features: shouldUpdateFeature
      ? {
          ...(features || {}),
          [NODE_NUMBER_FEATURE_KEY]: nextNodeDisplayNumber,
        }
      : features || {},
  };
};
