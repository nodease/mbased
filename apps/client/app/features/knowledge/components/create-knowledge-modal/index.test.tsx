import {
  createEvent,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import CreateKnowledgeModal from './index';
import { toast } from 'sonner';

const routerPushMock = vi.hoisted(() => vi.fn());
const knowledgeApiMock = vi.hoisted(() => ({
  getPresignedUploadUrl: vi.fn(),
  uploadKnowledgeBase: vi.fn(),
  uploadToS3: vi.fn(),
}));
const connectorApiMock = vi.hoisted(() => ({
  createConnector: vi.fn(),
  deleteConnector: vi.fn(),
  testConnection: vi.fn(),
}));

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: routerPushMock, refresh: vi.fn() }),
}));

vi.mock('sonner', () => ({
  toast: {
    success: vi.fn(),
    error: vi.fn(),
  },
}));

vi.mock('@/app/features/knowledge/api/knowledgeApi', () => ({
  knowledgeApi: knowledgeApiMock,
}));

vi.mock('@/app/features/knowledge/api/connectorApi', () => ({
  connectorApi: connectorApiMock,
}));

const fetchMock = vi.fn();

const renderOpenFileSourceModal = () =>
  render(
    <CreateKnowledgeModal
      isOpen
      onClose={vi.fn()}
      knowledgeBaseId="kb-1"
      initialTab="FILE"
    />,
  );

const fileDropEvent = (
  target: Window | Element,
  dataTransfer: DataTransfer | object,
) => createEvent.drop(target, { dataTransfer });

const fillValidDbConfig = (container: HTMLElement) => {
  const textInputs = screen.getAllByRole('textbox');
  fireEvent.change(textInputs[0], { target: { value: 'Test DB' } });
  fireEvent.change(textInputs[1], { target: { value: 'db.internal' } });
  fireEvent.change(textInputs[2], { target: { value: 'test' } });
  fireEvent.change(textInputs[3], { target: { value: 'test-user' } });

  const passwordInput = container.querySelector('input[type="password"]');
  expect(passwordInput).toBeInTheDocument();
  fireEvent.change(passwordInput!, {
    target: { value: 'placeholder-password' },
  });
};

beforeEach(() => {
  vi.clearAllMocks();
  knowledgeApiMock.getPresignedUploadUrl.mockResolvedValue({
    use_backend_proxy: true,
  });
  knowledgeApiMock.uploadKnowledgeBase.mockResolvedValue({
    knowledge_base_id: 'kb-1',
    document_id: 'doc-1',
    status: 'pending',
    message: 'ok',
  });
  connectorApiMock.createConnector.mockResolvedValue({
    id: 'connection-1',
    success: true,
    message: 'ok',
  });
  connectorApiMock.deleteConnector.mockResolvedValue({ success: true });
  fetchMock.mockResolvedValue({
    ok: true,
    json: async () => [],
  });
  vi.stubGlobal('fetch', fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('CreateKnowledgeModal file drag and drop', () => {
  it('blocks a DB source save when the connection name is blank', async () => {
    const alertSpy = vi.spyOn(window, 'alert').mockImplementation(() => {});
    render(
      <CreateKnowledgeModal
        isOpen
        onClose={vi.fn()}
        knowledgeBaseId="kb-1"
        initialTab="DB"
      />,
    );
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());

    fireEvent.change(screen.getByPlaceholderText('예: 운영 DB'), {
      target: { value: '   ' },
    });
    fireEvent.click(screen.getByRole('button', { name: '소스 추가' }));

    expect(alertSpy).toHaveBeenCalledWith('DB 연결 이름을 입력해주세요.');
    expect(connectorApiMock.createConnector).not.toHaveBeenCalled();
    expect(knowledgeApiMock.uploadKnowledgeBase).not.toHaveBeenCalled();
    alertSpy.mockRestore();
  });

  it('prevents browser file drop defaults inside the modal', async () => {
    const { container } = renderOpenFileSourceModal();
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    const modalOverlay = container.firstElementChild;
    expect(modalOverlay).toBeInTheDocument();

    const fileDrop = fileDropEvent(modalOverlay!, {
      types: ['Files'],
      files: [],
    });

    expect(fireEvent(modalOverlay!, fileDrop)).toBe(false);
    expect(fileDrop.defaultPrevented).toBe(true);

    const textDrop = fileDropEvent(modalOverlay!, {
      types: ['text/plain'],
      files: [],
    });

    expect(fireEvent(modalOverlay!, textDrop)).toBe(true);
    expect(textDrop.defaultPrevented).toBe(false);
  });

  it('keeps drag upload in the drop zone without opening the file', async () => {
    const { container } = renderOpenFileSourceModal();
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());

    const fileInput = container.querySelector('input[type="file"]');
    expect(fileInput).toBeInTheDocument();

    const dropZone = fileInput?.parentElement;
    expect(dropZone).toBeInTheDocument();

    const upload = new File(['hello'], 'guide.md', {
      type: 'text/markdown',
    });
    const dataTransfer = {
      types: ['Files'],
      files: [upload],
      dropEffect: 'none',
    };
    const drop = fileDropEvent(dropZone!, dataTransfer);

    expect(fireEvent(dropZone!, drop)).toBe(false);
    expect(drop.defaultPrevented).toBe(true);
    expect(dataTransfer.dropEffect).toBe('copy');
    expect(await screen.findByText('guide.md')).toBeInTheDocument();
  });

  it('returns to the source list after adding a file source', async () => {
    const onClose = vi.fn();
    const { container } = render(
      <CreateKnowledgeModal
        isOpen
        onClose={onClose}
        knowledgeBaseId="kb-1"
        initialTab="FILE"
      />,
    );
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());

    const fileInput = container.querySelector('input[type="file"]');
    expect(fileInput).toBeInTheDocument();
    const upload = new File(['hello'], 'guide.md', {
      type: 'text/markdown',
    });
    fireEvent.change(fileInput!, {
      target: { files: [upload] },
    });

    const buttons = screen.getAllByRole('button');
    fireEvent.click(buttons[buttons.length - 1]);

    await waitFor(() => {
      expect(knowledgeApiMock.uploadKnowledgeBase).toHaveBeenCalled();
    });
    expect(knowledgeApiMock.getPresignedUploadUrl).toHaveBeenCalledWith(
      'guide.md',
      'text/markdown',
      'kb-1',
    );
    expect(onClose).toHaveBeenCalled();
    expect(routerPushMock).toHaveBeenCalledWith('/dashboard/knowledge/kb-1');
  });

  it('closes and shows a fixed collection hint when the slot became occupied', async () => {
    const onClose = vi.fn();
    knowledgeApiMock.getPresignedUploadUrl.mockRejectedValueOnce({
      response: {
        status: 409,
        data: {
          detail: {
            error: { code: 'knowledge.document_slot_occupied' },
          },
        },
      },
    });
    const { container } = render(
      <CreateKnowledgeModal
        isOpen
        onClose={onClose}
        knowledgeBaseId="kb-1"
        initialTab="FILE"
      />,
    );
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());

    const fileInput = container.querySelector('input[type="file"]');
    fireEvent.change(fileInput!, {
      target: {
        files: [new File(['policy'], 'policy.md', { type: 'text/markdown' })],
      },
    });
    const buttons = screen.getAllByRole('button');
    fireEvent.click(buttons[buttons.length - 1]);

    await waitFor(() => {
      expect(toast.error).toHaveBeenCalledWith(
        '이 지식 베이스에는 이미 소스가 있습니다. 새 지식 베이스를 만든 뒤 Collection에서 묶어주세요.',
      );
    });
    expect(onClose).toHaveBeenCalledTimes(1);
    expect(knowledgeApiMock.uploadKnowledgeBase).not.toHaveBeenCalled();
  });

  it('refreshes the detail flow when the canonical registration loses a race', async () => {
    const onClose = vi.fn();
    knowledgeApiMock.uploadKnowledgeBase.mockRejectedValueOnce({
      response: {
        status: 409,
        data: {
          detail: {
            error: { code: 'knowledge.document_slot_occupied' },
          },
        },
      },
    });
    const { container } = render(
      <CreateKnowledgeModal
        isOpen
        onClose={onClose}
        knowledgeBaseId="kb-1"
        initialTab="FILE"
      />,
    );
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());

    const fileInput = container.querySelector('input[type="file"]');
    fireEvent.change(fileInput!, {
      target: {
        files: [new File(['policy'], 'policy.md', { type: 'text/markdown' })],
      },
    });
    const buttons = screen.getAllByRole('button');
    fireEvent.click(buttons[buttons.length - 1]);

    await waitFor(() => {
      expect(knowledgeApiMock.uploadKnowledgeBase).toHaveBeenCalledTimes(1);
      expect(toast.error).toHaveBeenCalledWith(
        '이 지식 베이스에는 이미 소스가 있습니다. 새 지식 베이스를 만든 뒤 Collection에서 묶어주세요.',
      );
    });
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('shows a sanitized upload failure message', async () => {
    const alertSpy = vi.spyOn(window, 'alert').mockImplementation(() => {});
    const consoleErrorSpy = vi
      .spyOn(console, 'error')
      .mockImplementation(() => {});
    knowledgeApiMock.uploadKnowledgeBase.mockRejectedValueOnce({
      response: {
        status: 500,
        data: { detail: 'raw-response-payload-should-not-be-logged' },
      },
      config: {
        headers: { 'X-Test-Debug': 'request-config-should-not-be-logged' },
      },
    });
    const { container } = renderOpenFileSourceModal();
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());

    const fileInput = container.querySelector('input[type="file"]');
    const upload = new File(['hello'], 'guide.md', {
      type: 'text/markdown',
    });
    fireEvent.change(fileInput!, {
      target: { files: [upload] },
    });

    const buttons = screen.getAllByRole('button');
    fireEvent.click(buttons[buttons.length - 1]);

    await waitFor(() => {
      expect(toast.error).toHaveBeenCalledWith(
        '요청 처리에 실패했습니다. (HTTP 500)',
      );
    });
    expect(alertSpy).not.toHaveBeenCalled();
    expect(consoleErrorSpy).not.toHaveBeenCalled();
  });

  it('removes a newly created DB connector when canonical registration loses the slot race', async () => {
    knowledgeApiMock.uploadKnowledgeBase.mockRejectedValueOnce({
      response: {
        status: 409,
        data: {
          detail: {
            error: { code: 'knowledge.document_slot_occupied' },
          },
        },
      },
    });
    const { container } = render(
      <CreateKnowledgeModal
        isOpen
        onClose={vi.fn()}
        knowledgeBaseId="kb-1"
        initialTab="DB"
      />,
    );
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    fillValidDbConfig(container);
    fireEvent.click(screen.getByRole('button', { name: '소스 추가' }));

    await waitFor(() => {
      expect(connectorApiMock.createConnector).toHaveBeenCalledTimes(1);
      expect(connectorApiMock.deleteConnector).toHaveBeenCalledWith(
        'connection-1',
      );
    });
  });

  it('keeps the DB connector when the document commit outcome is ambiguous', async () => {
    knowledgeApiMock.uploadKnowledgeBase.mockRejectedValueOnce({
      response: {
        status: 503,
        data: {
          detail: {
            error: {
              code: 'knowledge.document_registration_unavailable',
            },
          },
        },
      },
    });
    const { container } = render(
      <CreateKnowledgeModal
        isOpen
        onClose={vi.fn()}
        knowledgeBaseId="kb-1"
        initialTab="DB"
      />,
    );
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    fillValidDbConfig(container);
    fireEvent.click(screen.getByRole('button', { name: '소스 추가' }));

    await waitFor(() => {
      expect(knowledgeApiMock.uploadKnowledgeBase).toHaveBeenCalledTimes(1);
    });
    expect(connectorApiMock.deleteConnector).not.toHaveBeenCalled();
  });
});
