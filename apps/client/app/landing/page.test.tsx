import { render, screen, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import LandingPage from './page';

describe('LandingPage header', () => {
  it('Nodease 텍스트 브랜드와 인증 링크만 표시하고 이미지와 GitHub 링크를 노출하지 않는다', () => {
    const { container } = render(<LandingPage />);
    const header = screen.getByRole('navigation');

    expect(screen.getByRole('link', { name: 'Nodease' })).toHaveAttribute(
      'href',
      '/',
    );
    expect(within(header).getByRole('link', { name: 'Sign in' })).toHaveAttribute(
      'href',
      '/auth/login',
    );
    expect(
      within(header).getByRole('link', { name: 'Start for free' }),
    ).toHaveAttribute('href', '/auth/signup');
    expect(
      screen.queryByRole('img', { name: 'nodease' }),
    ).not.toBeInTheDocument();
    expect(
      container.querySelector('a[href*="github.com"]'),
    ).not.toBeInTheDocument();
  });
});
