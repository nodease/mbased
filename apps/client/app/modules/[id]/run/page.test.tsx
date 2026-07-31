import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import AuthenticatedDeploymentRunPage from './page';
import { authApi } from '@/app/features/auth/api/authApi';
import { workflowApi } from '@/app/features/workflow/api/workflowApi';

const { routerPush, routerReplace } = vi.hoisted(() => ({
  routerPush: vi.fn(),
  routerReplace: vi.fn(),
}));

vi.mock('next/navigation', () => ({
  useParams: () => ({ id: 'workflow-1' }),
  useRouter: () => ({ push: routerPush, replace: routerReplace }),
  useSearchParams: () => new URLSearchParams('deploymentId=deployment-1'),
}));

vi.mock('@/app/features/workflow/api/workflowApi', () => ({
  workflowApi: {
    getDeploymentRunInfo: vi.fn(),
    runDeployment: vi.fn(),
  },
}));

vi.mock('@/app/features/auth/api/authApi', () => ({
  authApi: {
    me: vi.fn(),
  },
}));

const mockedAuthApi = vi.mocked(authApi);
const mockedWorkflowApi = vi.mocked(workflowApi);

describe('AuthenticatedDeploymentRunPage', () => {
  beforeEach(() => {
    mockedAuthApi.me.mockResolvedValue({
      user: {
        id: 'user-1',
        name: '김서연',
        email: 'seoyeon.kim@nodease.demo',
        emailVerified: true,
        role: 'user',
        isActive: true,
        createdAt: '2026-07-14T00:00:00Z',
        updatedAt: '2026-07-14T00:00:00Z',
      },
      session: {
        token: '[REDACTED]',
        expiresAt: '2026-07-15T00:00:00Z',
      },
    });
    mockedWorkflowApi.getDeploymentRunInfo.mockResolvedValue({
      deployment_id: 'deployment-1',
      app_id: 'app-1',
      workflow_id: 'workflow-1',
      name: '사내 문서 질문 응답 봇',
      version: 1,
      type: 'internal_chatbot',
      input_schema: {
        variables: [
          {
            name: 'question',
            type: 'paragraph',
            label: '질문',
          },
        ],
      },
      output_schema: {
        outputs: [{ variable: 'final_answer', label: '최종 답변' }],
      },
    });
    mockedWorkflowApi.runDeployment.mockResolvedValue({
      status: 'success',
      results: {
        final_answer: '개발팀 신입 연봉 기준은 사내 문서 기준을 따릅니다.',
      },
    });
  });

  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
    window.history.replaceState({}, '', '/');
  });

  it('내부 챗봇을 사용자 선택기 없는 대화형 화면으로 실행하고 질문과 응답을 누적한다', async () => {
    render(<AuthenticatedDeploymentRunPage />);

    expect(
      await screen.findByRole('heading', { name: '사내 문서 질문 응답 봇' }),
    ).toBeVisible();
    expect(screen.getByRole('main')).toHaveClass('h-full', 'overflow-y-auto');
    expect(screen.queryByRole('combobox')).not.toBeInTheDocument();
    expect(await screen.findByText('김서연')).toBeVisible();
    expect(mockedAuthApi.me).toHaveBeenCalledOnce();
    expect(screen.getByRole('region', { name: '대화 내용' })).toBeVisible();
    expect(screen.getByLabelText('질문')).toHaveAttribute('rows', '1');
    expect(
      screen.getByRole('button', { name: '전송' }).closest('form'),
    ).toHaveClass('items-center');
    const questionInput = screen.getByLabelText('질문');
    Object.defineProperty(questionInput, 'scrollHeight', {
      configurable: true,
      value: 96,
    });
    fireEvent.change(questionInput, {
      target: { value: '첫째 줄\n둘째 줄\n셋째 줄' },
    });
    expect(questionInput).toHaveClass('overflow-hidden');
    expect(questionInput).toHaveStyle({ height: '96px' });
    expect(mockedWorkflowApi.getDeploymentRunInfo).toHaveBeenCalledWith(
      'deployment-1',
    );

    fireEvent.change(screen.getByLabelText('질문'), {
      target: { value: '개발팀 신입 연봉 기준을 알려줘' },
    });
    fireEvent.click(screen.getByRole('button', { name: '전송' }));

    await waitFor(() => {
      expect(mockedWorkflowApi.runDeployment).toHaveBeenCalledWith(
        'deployment-1',
        {
          question: '개발팀 신입 연봉 기준을 알려줘',
        },
        expect.stringMatching(
          /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/,
        ),
      );
    });
    expect(
      await screen.findByText(
        '개발팀 신입 연봉 기준은 사내 문서 기준을 따릅니다.',
      ),
    ).toBeVisible();
    expect(screen.getByText('개발팀 신입 연봉 기준을 알려줘')).toBeVisible();
    expect(screen.getByText('최종 답변')).toBeVisible();
    expect(screen.getByLabelText('질문')).toHaveValue('');
    expect(screen.getByLabelText('질문')).toHaveStyle({ height: '40px' });

    mockedWorkflowApi.runDeployment.mockResolvedValueOnce({
      status: 'success',
      results: { final_answer: 'VPN 신청은 사내 포털에서 할 수 있습니다.' },
    });
    fireEvent.change(screen.getByLabelText('질문'), {
      target: { value: 'VPN은 어디서 신청해?' },
    });
    fireEvent.click(screen.getByRole('button', { name: '전송' }));

    expect(
      await screen.findByText('VPN 신청은 사내 포털에서 할 수 있습니다.'),
    ).toBeVisible();
    expect(screen.getByText('개발팀 신입 연봉 기준을 알려줘')).toBeVisible();
    expect(screen.getByText('VPN은 어디서 신청해?')).toBeVisible();
  });

  it('내부 챗봇 응답을 Markdown으로 표시하고 기본 UI 크기를 사용한다', async () => {
    mockedWorkflowApi.runDeployment.mockResolvedValueOnce({
      status: 'success',
      results: {
        final_answer:
          '**핵심 안내**\n\n- 재무팀 첫 주 일정\n- 영업팀 첫 주 일정',
      },
    });

    render(<AuthenticatedDeploymentRunPage />);
    const heading = await screen.findByRole('heading', {
      name: '사내 문서 질문 응답 봇',
    });
    expect(heading).toHaveClass('text-2xl');

    const questionInput = screen.getByLabelText('질문');
    expect(questionInput).toHaveClass('text-sm', 'min-h-10');
    expect(screen.getByRole('button', { name: '전송' })).toHaveClass(
      'h-10',
      'w-10',
    );

    fireEvent.change(questionInput, {
      target: { value: '첫 주 일정을 알려줘' },
    });
    fireEvent.click(screen.getByRole('button', { name: '전송' }));

    const strongText = await screen.findByText('핵심 안내');
    expect(strongText.tagName).toBe('STRONG');
    expect(screen.getByRole('list')).toBeVisible();
    expect(screen.getByText('재무팀 첫 주 일정')).toBeVisible();
  });

  it('내부 챗봇은 초록 강조와 최종 응답 바깥 카드를 제거하고 응답을 아이콘 시작선까지 넓힌다', async () => {
    mockedWorkflowApi.runDeployment.mockResolvedValueOnce({
      status: 'success',
      results: {
        final_answer: '개발팀 신입 연봉 기준은 사내 문서 기준을 따릅니다.',
        __nodease_citations: {
          version: 1,
          items: [
            {
              citation_id: 'evidence-1',
              evidence_rank: 1,
              label: '개발팀 온보딩 문서',
              page_number: 3,
              section: '보상 기준',
              content_preview: '신입 보상 기준은 직무 등급에 따라 결정합니다.',
            },
          ],
        },
      },
    });
    render(<AuthenticatedDeploymentRunPage />);
    const questionInput = await screen.findByLabelText('질문');

    fireEvent.change(questionInput, {
      target: { value: '개발팀 신입 연봉 기준을 알려줘' },
    });
    fireEvent.click(screen.getByRole('button', { name: '전송' }));

    const responseText = await screen.findByText(
      '개발팀 신입 연봉 기준은 사내 문서 기준을 따릅니다.',
    );
    const responseContainer = responseText.closest('div');
    const finalResponseSection = screen
      .getByRole('heading', { name: '최종 응답' })
      .closest('section');
    const assistantResponse = finalResponseSection?.parentElement;
    const greenClassNames = Array.from(
      screen.getByRole('main').querySelectorAll<HTMLElement>('[class]'),
    ).flatMap((element) =>
      Array.from(element.classList).filter(
        (className) =>
          className.includes('emerald-') || className.includes('green-'),
      ),
    );
    const assistantResponseElements = assistantResponse
      ? [
          assistantResponse,
          ...Array.from(
            assistantResponse.querySelectorAll<HTMLElement>('[class]'),
          ),
        ]
      : [];
    const darkClassNames = assistantResponseElements.flatMap((element) =>
      Array.from(element.classList).filter((className) =>
        className.startsWith('dark:'),
      ),
    );

    for (const removedCardClassName of [
      'rounded-lg',
      'border',
      'bg-emerald-50',
      'p-4',
    ]) {
      expect(finalResponseSection).not.toHaveClass(removedCardClassName);
    }
    expect(responseContainer).toHaveClass('col-span-2');
    expect(screen.getByText('참조 문서 (1)')).toBeVisible();
    expect(screen.getByText('개발팀 온보딩 문서')).toBeInTheDocument();
    expect(greenClassNames).toEqual([]);
    expect(darkClassNames).toEqual([]);
  });

  it('Enter는 질문을 전송하고 Shift+Enter는 줄바꿈을 위해 전송하지 않는다', async () => {
    render(<AuthenticatedDeploymentRunPage />);
    const questionInput = await screen.findByLabelText('질문');

    fireEvent.change(questionInput, {
      target: { value: '첫 번째 질문' },
    });
    fireEvent.keyDown(questionInput, {
      key: 'Enter',
      code: 'Enter',
      shiftKey: true,
    });
    expect(mockedWorkflowApi.runDeployment).not.toHaveBeenCalled();

    fireEvent.keyDown(questionInput, {
      key: 'Enter',
      code: 'Enter',
    });

    await waitFor(() => {
      expect(mockedWorkflowApi.runDeployment).toHaveBeenCalledWith(
        'deployment-1',
        { question: '첫 번째 질문' },
        expect.any(String),
      );
    });
  });

  it('WebKit의 한글 조합 확정 Enter는 질문을 전송하지 않는다', async () => {
    render(<AuthenticatedDeploymentRunPage />);
    const questionInput = await screen.findByLabelText('질문');

    fireEvent.change(questionInput, {
      target: { value: '한글 입력 중' },
    });
    fireEvent.keyDown(questionInput, {
      key: 'Enter',
      code: 'Enter',
      isComposing: false,
      keyCode: 229,
    });

    expect(mockedWorkflowApi.runDeployment).not.toHaveBeenCalled();
  });

  it('내부 실행 화면의 뒤로가기는 운영 현황이 아닌 대시보드로 이동한다', async () => {
    render(<AuthenticatedDeploymentRunPage />);

    await screen.findByRole('heading', { name: '사내 문서 질문 응답 봇' });
    fireEvent.click(
      screen.getByRole('button', { name: '대시보드로 돌아가기' }),
    );

    expect(routerPush).toHaveBeenCalledWith('/dashboard');
  });

  it('사용자 정보 조회가 실패해도 권한 상태와 실행 화면을 유지한다', async () => {
    mockedAuthApi.me.mockRejectedValueOnce(new Error('profile unavailable'));

    render(<AuthenticatedDeploymentRunPage />);

    expect(
      await screen.findByRole('heading', { name: '사내 문서 질문 응답 봇' }),
    ).toBeVisible();
    expect(screen.getByText('사용자 권한 적용')).toBeVisible();
    expect(screen.queryByText('김서연')).not.toBeInTheDocument();
  });

  it('비로그인 사용자는 원래 내부 실행 링크를 보존한 로그인 화면으로 이동한다', async () => {
    window.history.replaceState(
      {},
      '',
      '/modules/workflow-1/run?deploymentId=deployment-1&tab=history#result',
    );
    mockedWorkflowApi.getDeploymentRunInfo.mockRejectedValueOnce({
      response: { status: 401 },
    });

    render(<AuthenticatedDeploymentRunPage />);

    await waitFor(() => {
      expect(routerReplace).toHaveBeenCalledWith(
        '/auth/login?next=%2Fmodules%2Fworkflow-1%2Frun%3FdeploymentId%3Ddeployment-1%26tab%3Dhistory%23result',
      );
    });
  });

  it('URL workflow와 run-info workflow가 다르면 실행을 막는다', async () => {
    mockedWorkflowApi.getDeploymentRunInfo.mockResolvedValueOnce({
      deployment_id: 'deployment-1',
      app_id: 'app-1',
      workflow_id: 'other-workflow',
      name: '다른 워크플로우 배포',
      version: 1,
      type: 'chatbot',
      input_schema: { variables: [] },
      output_schema: { outputs: [] },
    });

    render(<AuthenticatedDeploymentRunPage />);

    expect(
      await screen.findByText(
        '요청한 workflow와 배포 정보가 일치하지 않습니다.',
      ),
    ).toBeVisible();

    expect(
      screen.queryByRole('button', { name: '전송' }),
    ).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '실행' }));
    expect(mockedWorkflowApi.runDeployment).not.toHaveBeenCalled();
  });

  it('문서화되지 않은 backend detail 원문을 실행 오류로 표시하지 않는다', async () => {
    mockedWorkflowApi.runDeployment.mockRejectedValueOnce({
      response: {
        status: 400,
        data: { detail: 'source_url=https://private.example/internal' },
      },
    });

    render(<AuthenticatedDeploymentRunPage />);
    await screen.findByRole('heading', { name: '사내 문서 질문 응답 봇' });

    fireEvent.change(screen.getByLabelText('질문'), {
      target: { value: '질문' },
    });
    fireEvent.click(screen.getByRole('button', { name: '전송' }));

    expect(
      await screen.findByText('실행 입력이 올바르지 않습니다.'),
    ).toBeVisible();
    expect(
      screen.queryByText('source_url=https://private.example/internal'),
    ).not.toBeInTheDocument();
  });
});
