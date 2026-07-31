type FocusRect = Pick<
  DOMRect,
  'left' | 'top' | 'right' | 'bottom' | 'width' | 'height'
>;

export const calculateAgentBuilderNodeFocusViewport = ({
  canvasRect,
  panelRect,
  node,
  padding = 32,
  maxZoom = 1.6,
}: {
  canvasRect: FocusRect;
  panelRect: FocusRect | null;
  node: { x: number; y: number; width: number; height: number };
  padding?: number;
  maxZoom?: number;
}) => {
  const intersection = panelRect
    ? {
        left: Math.max(canvasRect.left, panelRect.left),
        top: Math.max(canvasRect.top, panelRect.top),
        right: Math.min(canvasRect.right, panelRect.right),
        bottom: Math.min(canvasRect.bottom, panelRect.bottom),
      }
    : null;
  const overlaps = Boolean(
    intersection &&
    intersection.right > intersection.left &&
    intersection.bottom > intersection.top,
  );
  const regions =
    overlaps && intersection
      ? [
          {
            left: canvasRect.left,
            top: canvasRect.top,
            right: canvasRect.right,
            bottom: intersection.top,
          },
          {
            left: canvasRect.left,
            top: intersection.bottom,
            right: canvasRect.right,
            bottom: canvasRect.bottom,
          },
          {
            left: canvasRect.left,
            top: canvasRect.top,
            right: intersection.left,
            bottom: canvasRect.bottom,
          },
          {
            left: intersection.right,
            top: canvasRect.top,
            right: canvasRect.right,
            bottom: canvasRect.bottom,
          },
        ]
      : [
          {
            left: canvasRect.left,
            top: canvasRect.top,
            right: canvasRect.right,
            bottom: canvasRect.bottom,
          },
        ];
  const candidates = regions
    .map((region) => {
      const width = Math.max(0, region.right - region.left);
      const height = Math.max(0, region.bottom - region.top);
      const zoom = Math.min(
        maxZoom,
        Math.max(0, width - padding * 2) / Math.max(1, node.width),
        Math.max(0, height - padding * 2) / Math.max(1, node.height),
      );
      return { region, width, height, zoom };
    })
    .filter((candidate) => candidate.zoom > 0)
    .sort(
      (left, right) =>
        right.zoom - left.zoom ||
        right.width * right.height - left.width * left.height,
    );
  const selected = candidates[0] ?? {
    region: {
      left: canvasRect.left,
      top: canvasRect.top,
      right: canvasRect.right,
      bottom: canvasRect.bottom,
    },
    zoom: Math.min(maxZoom, 1),
  };
  const targetX =
    (selected.region.left + selected.region.right) / 2 - canvasRect.left;
  const targetY =
    (selected.region.top + selected.region.bottom) / 2 - canvasRect.top;
  return {
    x: targetX - (node.x + node.width / 2) * selected.zoom,
    y: targetY - (node.y + node.height / 2) * selected.zoom,
    zoom: selected.zoom,
  };
};
