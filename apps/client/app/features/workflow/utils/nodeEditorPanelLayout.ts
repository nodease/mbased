export type NodeEditorPanelWidths = {
  left: number;
  center: number;
  right: number;
};

export type HorizontalResizeHandle = 'left-center' | 'center-right';

export const NODE_EDITOR_PANEL_WIDTHS = {
  default: { left: 340, center: 720, right: 340 },
  defaultRatio: { left: 0.28, center: 0.52, right: 0.2 },
  min: { left: 280, center: 420, right: 280 },
  max: { left: 720, center: 1280, right: 520 },
  resizeHandleWidth: 8,
  keyboardStep: 24,
} as const;

const PANEL_GROW_ORDER: Array<keyof NodeEditorPanelWidths> = [
  'center',
  'left',
  'right',
];

export const sumPanelWidths = (widths: NodeEditorPanelWidths) =>
  widths.left + widths.center + widths.right;

const clampPanelWidth = (panel: keyof NodeEditorPanelWidths, width: number) =>
  Math.min(
    NODE_EDITOR_PANEL_WIDTHS.max[panel],
    Math.max(NODE_EDITOR_PANEL_WIDTHS.min[panel], width),
  );

const getAvailablePanelWidth = (layoutWidth: number) =>
  Math.max(
    layoutWidth - NODE_EDITOR_PANEL_WIDTHS.resizeHandleWidth * 2,
    sumPanelWidths(NODE_EDITOR_PANEL_WIDTHS.min),
  );

export const getNodeEditorMaxLayoutWidth = (viewportWidth: number) =>
  viewportWidth * 0.9;

export const fitPanelWidths = (
  widths: NodeEditorPanelWidths,
  layoutWidth: number,
): NodeEditorPanelWidths => {
  const availableWidth = getAvailablePanelWidth(layoutWidth);
  const next = {
    left: clampPanelWidth('left', widths.left),
    center: clampPanelWidth('center', widths.center),
    right: clampPanelWidth('right', widths.right),
  };

  let overflow = sumPanelWidths(next) - availableWidth;
  if (overflow > 0) {
    for (const panel of ['center', 'right', 'left'] as const) {
      const reducible = next[panel] - NODE_EDITOR_PANEL_WIDTHS.min[panel];
      const reduction = Math.min(reducible, overflow);
      next[panel] -= reduction;
      overflow -= reduction;
      if (overflow <= 0) break;
    }
  }

  let underflow = availableWidth - sumPanelWidths(next);
  if (underflow > 0) {
    for (const panel of PANEL_GROW_ORDER) {
      const growable = NODE_EDITOR_PANEL_WIDTHS.max[panel] - next[panel];
      const growth = Math.min(growable, underflow);
      next[panel] += growth;
      underflow -= growth;
      if (underflow <= 0) break;
    }
  }

  return next;
};

export const getDefaultPanelWidthsForLayout = (
  layoutWidth: number,
): NodeEditorPanelWidths => {
  const availableWidth = getAvailablePanelWidth(layoutWidth);

  return fitPanelWidths(
    {
      left: availableWidth * NODE_EDITOR_PANEL_WIDTHS.defaultRatio.left,
      center: availableWidth * NODE_EDITOR_PANEL_WIDTHS.defaultRatio.center,
      right: availableWidth * NODE_EDITOR_PANEL_WIDTHS.defaultRatio.right,
    },
    layoutWidth,
  );
};

export const resizePanelWidths = (
  handle: HorizontalResizeHandle,
  startWidths: NodeEditorPanelWidths,
  deltaX: number,
  layoutWidth: number,
): NodeEditorPanelWidths => {
  const start = fitPanelWidths(startWidths, layoutWidth);

  if (handle === 'left-center') {
    const nextLeft = clampPanelWidth('left', start.left + deltaX);
    const appliedDelta = nextLeft - start.left;
    return fitPanelWidths(
      {
        left: nextLeft,
        center: start.center - appliedDelta / 2,
        right: start.right - appliedDelta / 2,
      },
      layoutWidth,
    );
  }

  const boundedDelta = Math.max(
    NODE_EDITOR_PANEL_WIDTHS.min.center - start.center,
    Math.min(start.right - NODE_EDITOR_PANEL_WIDTHS.min.right, deltaX),
  );

  return fitPanelWidths(
    {
      left: start.left,
      center: start.center + boundedDelta,
      right: start.right - boundedDelta,
    },
    layoutWidth,
  );
};
