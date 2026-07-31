import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { NodeOptionsDisplay } from './index';

describe('NodeOptionsDisplay', () => {
  it('Mail credential 식별자 대신 연결 상태만 표시한다', () => {
    render(
      <NodeOptionsDisplay
        nodeType="mailNode"
        options={{ credential_id: 'credential-sensitive-id' }}
      />,
    );

    expect(screen.getByText('연결됨')).toBeInTheDocument();
    expect(screen.queryByText('credential-sensitive-id')).not.toBeInTheDocument();
  });

  it('Mail credential이 없으면 연결 필요 상태를 표시한다', () => {
    render(
      <NodeOptionsDisplay nodeType="mailNode" options={{ credential_id: null }} />,
    );

    expect(screen.getByText('연결 필요')).toBeInTheDocument();
  });
});
