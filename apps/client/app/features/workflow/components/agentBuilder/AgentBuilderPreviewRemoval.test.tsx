import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

describe('Agent Builder Preview removal', () => {
  it('does not keep the legacy Preview state or apply controls in active UI', () => {
    const panel = readFileSync(resolve(__dirname, 'AgentBuilderPanel.tsx'), 'utf8');
    const canvas = readFileSync(resolve(__dirname, '../editor/NodeCanvas.tsx'), 'utf8');
    const store = readFileSync(
      resolve(__dirname, '../../store/useWorkflowStore.ts'),
      'utf8',
    );

    expect(panel).not.toContain('draft_preview');
    expect(panel).not.toContain('preview_prompt');
    expect(panel).not.toContain('적용 및 저장');
    expect(canvas).not.toContain('AgentBuilderPreview');
    expect(store).not.toContain('agentBuilderPreview');
  });
});
