import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import LoginPage from './page';
import { authApi } from '@/app/features/auth/api/authApi';

const { routerPush } = vi.hoisted(() => ({
  routerPush: vi.fn(),
}));

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: routerPush }),
}));

vi.mock('@/app/features/auth/api/authApi', () => ({
  authApi: {
    login: vi.fn(),
    googleLogin: vi.fn(),
  },
}));

vi.mock('sonner', () => ({
  toast: {
    error: vi.fn(),
  },
}));

const mockedAuthApi = vi.mocked(authApi);

const submitLogin = () => {
  fireEvent.change(screen.getByLabelText('이메일'), {
    target: { value: 'member@example.com' },
  });
  fireEvent.change(screen.getByLabelText('비밀번호'), {
    target: { value: 'password' },
  });
  fireEvent.click(screen.getByRole('button', { name: '로그인' }));
};

describe('LoginPage auth return', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockedAuthApi.login.mockResolvedValue({} as never);
  });

  afterEach(() => {
    cleanup();
    window.history.replaceState({}, '', '/auth/login');
  });

  it('로그인 성공 후 next의 내부 실행 링크로 복귀한다', async () => {
    window.history.replaceState(
      {},
      '',
      '/auth/login?next=%2Fmodules%2Fworkflow-1%2Frun%3FdeploymentId%3Ddeployment-1',
    );
    render(<LoginPage />);

    submitLogin();

    await waitFor(() => {
      expect(routerPush).toHaveBeenCalledWith(
        '/modules/workflow-1/run?deploymentId=deployment-1',
      );
    });
  });

  it.each([
    'https%3A%2F%2Fevil.example%2Fsteal',
    '%2F%2Fevil.example%2Fsteal',
  ])('외부 next URL(%s)은 무시하고 대시보드로 이동한다', async (next) => {
    window.history.replaceState(
      {},
      '',
      `/auth/login?next=${next}`,
    );
    render(<LoginPage />);

    submitLogin();

    await waitFor(() => {
      expect(routerPush).toHaveBeenCalledWith('/dashboard');
    });
  });

  it('Google 로그인에도 검증된 next 복귀 경로를 전달한다', () => {
    window.history.replaceState(
      {},
      '',
      '/auth/login?next=%2Fmodules%2Fworkflow-1%2Frun%3FdeploymentId%3Ddeployment-1',
    );
    render(<LoginPage />);

    fireEvent.click(screen.getByRole('button', { name: '구글로 로그인' }));

    expect(mockedAuthApi.googleLogin).toHaveBeenCalledWith(
      '/modules/workflow-1/run?deploymentId=deployment-1',
    );
  });

  it('Google 로그인은 외부 next URL을 대시보드로 제한한다', () => {
    window.history.replaceState(
      {},
      '',
      '/auth/login?next=https%3A%2F%2Fevil.example%2Fsteal',
    );
    render(<LoginPage />);

    fireEvent.click(screen.getByRole('button', { name: '구글로 로그인' }));

    expect(mockedAuthApi.googleLogin).toHaveBeenCalledWith('/dashboard');
  });

  it('로그인 제한은 서버의 고정된 generic 메시지만 표시한다', async () => {
    mockedAuthApi.login.mockRejectedValueOnce({
      response: {
        status: 429,
        data: {
          detail: '로그인 시도가 너무 많습니다. 잠시 후 다시 시도해주세요.',
        },
      },
    });
    render(<LoginPage />);

    submitLogin();

    expect(
      await screen.findByText(
        '로그인 시도가 너무 많습니다. 잠시 후 다시 시도해주세요.',
      ),
    ).toBeInTheDocument();
    expect(screen.queryByText(/account|network|threshold/i)).toBeNull();
  });
});
