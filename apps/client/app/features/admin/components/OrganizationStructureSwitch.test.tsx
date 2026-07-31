import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { OrganizationStructureSwitch } from './OrganizationStructureSwitch';

describe('OrganizationStructureSwitch', () => {
  it('멤버와 팀의 전체 목록 수와 현재 선택 상태를 표시한다', () => {
    render(
      <OrganizationStructureSwitch
        view="members"
        memberCount={24}
        teamCount={6}
        onChange={vi.fn()}
      />,
    );

    expect(screen.getByRole('button', { name: '멤버 24' })).toHaveAttribute(
      'aria-pressed',
      'true',
    );
    expect(screen.getByRole('button', { name: '팀 6' })).toHaveAttribute(
      'aria-pressed',
      'false',
    );
  });

  it('팀 버튼을 누르면 팀 보기 변경을 요청한다', () => {
    const onChange = vi.fn();
    render(
      <OrganizationStructureSwitch
        view="members"
        memberCount={24}
        teamCount={6}
        onChange={onChange}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: '팀 6' }));

    expect(onChange).toHaveBeenCalledWith('teams');
  });

  it('이미 선택된 버튼을 다시 누르면 보기 변경을 요청하지 않는다', () => {
    const onChange = vi.fn();
    render(
      <OrganizationStructureSwitch
        view="members"
        memberCount={24}
        teamCount={6}
        onChange={onChange}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: '멤버 24' }));

    expect(onChange).not.toHaveBeenCalled();
  });

  it('작은 화면에서도 compact한 동일 너비 버튼과 키보드 focus를 제공한다', () => {
    render(
      <OrganizationStructureSwitch
        view="members"
        memberCount={24}
        teamCount={6}
        onChange={vi.fn()}
      />,
    );

    const group = screen.getByRole('group', { name: '조직 구성 보기' });
    const memberButton = screen.getByRole('button', { name: '멤버 24' });
    const teamButton = screen.getByRole('button', { name: '팀 6' });

    expect(group).toHaveClass('inline-grid', 'w-fit', 'grid-cols-2');
    expect(group).not.toHaveClass('w-full');
    expect(memberButton).toHaveAttribute('type', 'button');
    expect(teamButton).toHaveAttribute('type', 'button');

    teamButton.focus();
    expect(teamButton).toHaveFocus();
  });
});
