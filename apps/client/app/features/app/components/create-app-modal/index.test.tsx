import { afterEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { AxiosError, type AxiosResponse } from 'axios';

vi.mock('../../api/appApi', () => ({
  appApi: {
    createApp: vi.fn(),
  },
}));

vi.mock('@/lib/apiClient', () => ({
  apiBaseUrl: '',
  apiClient: {
    post: vi.fn(),
  },
  publicApiClient: {
    post: vi.fn(),
  },
}));

vi.mock('sonner', () => ({
  toast: {
    success: vi.fn(),
    error: vi.fn(),
  },
}));

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn() }),
}));

import { toast } from 'sonner';
import { apiClient } from '@/lib/apiClient';
import { appApi } from '../../api/appApi';
import CreateAppModal from './index';

const mockedCreateApp = vi.mocked(appApi.createApp);
const mockedPost = vi.mocked(apiClient.post);

const apiError = (status: number, detail?: string) => {
  const error = new AxiosError('Request failed');
  error.response = {
    status,
    data: detail !== undefined ? { detail } : {},
  } as AxiosResponse;
  return error;
};

const submittedRequest = {
  id: 'req-1',
  user: null,
  requested_permission: 'app.create',
  reason: '온보딩 워크플로우를 만들어야 합니다.',
  status: 'pending' as const,
  created_at: '2026-07-04T10:00:00+09:00',
  decided_by: null,
  decided_at: null,
};

/** 이름을 입력하고 생성을 눌러 App 생성 요청을 트리거한다. */
const submitCreateApp = () => {
  fireEvent.change(screen.getByPlaceholderText('앱 이름을 입력하세요'), {
    target: { value: '온보딩 봇' },
  });
  fireEvent.click(screen.getByRole('button', { name: '생성' }));
};

/** App 생성 403 차단을 거쳐 권한 신청 view를 연다 (ORG-TC-E009 전제). */
const openPermissionRequestView = async () => {
  mockedCreateApp.mockRejectedValue(apiError(403, 'Forbidden'));
  render(<CreateAppModal onSuccess={vi.fn()} onClose={vi.fn()} />);
  submitCreateApp();
  await screen.findByText('앱 생성 권한 신청');
};

afterEach(() => {
  vi.clearAllMocks();
});

describe('CreateAppModal 권한 신청 연결', () => {
  it('App 생성 403 차단 시 실패 토스트 대신 권한 신청 UI를 표시한다 (ORG-TC-E009)', async () => {
    await openPermissionRequestView();

    expect(
      screen.getByText(
        'App 생성 권한이 없습니다. 관리자에게 앱 생성 권한을 신청할 수 있습니다.',
      ),
    ).toBeInTheDocument();
    expect(screen.getByLabelText(/신청 사유/)).toBeInTheDocument();
    expect(toast.error).not.toHaveBeenCalled();
  });

  it('중복 이름 400 실패는 기존 실패 토스트를 유지한다 (ORG-TC-E013)', async () => {
    mockedCreateApp.mockRejectedValue(
      apiError(400, 'App with this name already exists.'),
    );
    render(<CreateAppModal onSuccess={vi.fn()} onClose={vi.fn()} />);

    submitCreateApp();

    await waitFor(() =>
      expect(toast.error).toHaveBeenCalledWith('이미 존재하는 앱 이름입니다.'),
    );
    expect(screen.queryByText('앱 생성 권한 신청')).not.toBeInTheDocument();
  });

  it('403이 아닌 일반 실패는 기존 실패 토스트를 유지한다 (ORG-TC-E013)', async () => {
    mockedCreateApp.mockRejectedValue(apiError(500));
    render(<CreateAppModal onSuccess={vi.fn()} onClose={vi.fn()} />);

    submitCreateApp();

    await waitFor(() =>
      expect(toast.error).toHaveBeenCalledWith('앱 생성에 실패했습니다.'),
    );
    expect(screen.queryByText('앱 생성 권한 신청')).not.toBeInTheDocument();
  });

  it('blank 신청 사유는 API를 호출하지 않고 안내를 표시한다 (ORG-TC-E010)', async () => {
    await openPermissionRequestView();

    fireEvent.click(screen.getByRole('button', { name: '권한 신청' }));

    expect(
      await screen.findByText('신청 사유를 입력해주세요.'),
    ).toBeInTheDocument();
    expect(mockedPost).not.toHaveBeenCalled();
  });

  it('신청 사유 입력 후 app.create 고정으로 POST /permission-requests를 호출한다 (ORG-TC-E010)', async () => {
    mockedPost.mockResolvedValue({ data: submittedRequest });
    await openPermissionRequestView();

    fireEvent.change(screen.getByLabelText(/신청 사유/), {
      target: { value: '온보딩 워크플로우를 만들어야 합니다.' },
    });
    fireEvent.click(screen.getByRole('button', { name: '권한 신청' }));

    await waitFor(() =>
      expect(mockedPost).toHaveBeenCalledWith('/permission-requests', {
        requested_permission: 'app.create',
        reason: '온보딩 워크플로우를 만들어야 합니다.',
      }),
    );
  });

  it('신청 성공 시 신청 완료 안내를 표시한다 (ORG-TC-E011)', async () => {
    mockedPost.mockResolvedValue({ data: submittedRequest });
    await openPermissionRequestView();

    fireEvent.change(screen.getByLabelText(/신청 사유/), {
      target: { value: '온보딩 워크플로우를 만들어야 합니다.' },
    });
    fireEvent.click(screen.getByRole('button', { name: '권한 신청' }));

    expect(
      await screen.findByText(/권한 신청이 접수되었습니다/),
    ).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '닫기' })).toBeInTheDocument();
  });

  it('이미 권한 보유 409는 보유 안내를 표시한다 (ORG-TC-E012)', async () => {
    mockedPost.mockRejectedValue(
      apiError(409, 'App creation permission already granted'),
    );
    await openPermissionRequestView();

    fireEvent.change(screen.getByLabelText(/신청 사유/), {
      target: { value: '권한이 필요합니다.' },
    });
    fireEvent.click(screen.getByRole('button', { name: '권한 신청' }));

    expect(
      await screen.findByText(/이미 App 생성 권한이 있습니다/),
    ).toBeInTheDocument();
  });

  it('pending 신청 중복 409는 대기 안내를 표시한다 (ORG-TC-E012)', async () => {
    mockedPost.mockRejectedValue(
      apiError(409, 'Pending permission request already exists'),
    );
    await openPermissionRequestView();

    fireEvent.change(screen.getByLabelText(/신청 사유/), {
      target: { value: '권한이 필요합니다.' },
    });
    fireEvent.click(screen.getByRole('button', { name: '권한 신청' }));

    expect(
      await screen.findByText(/이미 처리 대기 중인 권한 신청이 있습니다/),
    ).toBeInTheDocument();
  });

  it('그 외 신청 실패는 일반 실패 메시지를 표시한다 (ORG-TC-E012)', async () => {
    mockedPost.mockRejectedValue(apiError(500));
    await openPermissionRequestView();

    fireEvent.change(screen.getByLabelText(/신청 사유/), {
      target: { value: '권한이 필요합니다.' },
    });
    fireEvent.click(screen.getByRole('button', { name: '권한 신청' }));

    expect(
      await screen.findByText(/권한 신청에 실패했습니다/),
    ).toBeInTheDocument();
  });
});
