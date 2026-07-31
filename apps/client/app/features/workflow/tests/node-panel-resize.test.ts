import { describe, expect, it } from 'vitest';
import {
  fitPanelWidths,
  getDefaultPanelWidthsForLayout,
  getNodeEditorMaxLayoutWidth,
  NODE_EDITOR_PANEL_WIDTHS,
  resizePanelWidths,
  sumPanelWidths,
} from '../utils/nodeEditorPanelLayout';

describe('workflow test cases: 노드 조작 편의성', () => {
  it('3패널 layout 계산에서 각 패널 폭은 최소/최대 폭 제약을 넘지 않는다', () => {
    const fitted = fitPanelWidths(
      { left: 100, center: 2000, right: 100 },
      1200,
    );

    expect(fitted.left).toBeGreaterThanOrEqual(
      NODE_EDITOR_PANEL_WIDTHS.min.left,
    );
    expect(fitted.center).toBeLessThanOrEqual(
      NODE_EDITOR_PANEL_WIDTHS.max.center,
    );
    expect(fitted.right).toBeGreaterThanOrEqual(
      NODE_EDITOR_PANEL_WIDTHS.min.right,
    );
  });

  it('왼쪽 패널 폭을 늘리면 가운데와 오른쪽 패널 폭이 비슷한 비율로 줄어든다', () => {
    const start = fitPanelWidths(NODE_EDITOR_PANEL_WIDTHS.default, 1400);
    const next = resizePanelWidths('left-center', start, 120, 1400);

    expect(next.left).toBeGreaterThan(start.left);
    expect(next.center).toBeLessThan(start.center);
    expect(next.right).toBeLessThan(start.right);
    expect(
      Math.abs(start.center - next.center - (start.right - next.right)),
    ).toBeLessThanOrEqual(1);
  });

  it('전체 편집 영역은 viewport width의 90%를 초과하지 않는다', () => {
    expect(getNodeEditorMaxLayoutWidth(1600)).toBe(1440);
  });

  it('기본 패널 폭은 사용 가능한 전체 폭의 비율을 기준으로 계산된다', () => {
    const layoutWidth = 1800;
    const availableWidth =
      layoutWidth - NODE_EDITOR_PANEL_WIDTHS.resizeHandleWidth * 2;
    const fitted = getDefaultPanelWidthsForLayout(layoutWidth);

    expect(sumPanelWidths(fitted)).toBeCloseTo(availableWidth, 0);
    expect(fitted.left / availableWidth).toBeCloseTo(
      NODE_EDITOR_PANEL_WIDTHS.defaultRatio.left,
      1,
    );
    expect(fitted.right / availableWidth).toBeCloseTo(
      NODE_EDITOR_PANEL_WIDTHS.defaultRatio.right,
      1,
    );
  });

  it('패널 폭 계산은 실제 렌더된 편집 영역 폭이 바뀌면 그 폭을 기준으로 다시 clamp된다', () => {
    const wide = fitPanelWidths(NODE_EDITOR_PANEL_WIDTHS.default, 1400);
    const narrow = fitPanelWidths(wide, 1000);

    expect(sumPanelWidths(narrow)).toBeLessThanOrEqual(
      1000 - NODE_EDITOR_PANEL_WIDTHS.resizeHandleWidth * 2,
    );
    expect(narrow.center).toBeLessThan(wide.center);
  });

  it('노드 상세 편집 화면을 열면 3패널이 기본 비율로 표시된다', () => {
    const fitted = getDefaultPanelWidthsForLayout(1200);

    expect(fitted.left).toBeGreaterThan(0);
    expect(fitted.center).toBeGreaterThan(0);
    expect(fitted.right).toBeGreaterThan(0);
    expect(fitted.center).toBeGreaterThan(fitted.left);
    expect(fitted.left).toBeGreaterThan(fitted.right);
  });
  it.todo(
    '패널을 최대/최소 폭까지 드래그해도 UI가 겹치거나 화면 밖으로 밀려나지 않는다',
  );
  it.todo(
    '노드 상세 편집 화면을 닫았다가 같은 세션에서 다시 열었을 때 세션 내 비율 유지 정책이 의도대로 동작한다',
  );
  it.todo(
    'read-only 사용자는 패널 리사이즈는 할 수 있지만 node data 수정/저장은 할 수 없다',
  );
  it.todo(
    '드래그 중 마우스가 편집 영역 밖으로 나가도 pointer capture 또는 window event 처리로 리사이즈가 끊기지 않는다',
  );
  it.todo('드래그 종료 후 텍스트 선택 상태나 캔버스 pan 상태가 남지 않는다');
});
