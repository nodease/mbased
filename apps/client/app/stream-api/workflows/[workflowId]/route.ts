import { NextRequest } from 'next/server';

const CONTEXT_HEADER_ALLOWLIST = [
  'Origin',
  'Sec-Fetch-Site',
  'X-CSRF-Token',
  'X-Organization-Id',
  'X-Request-Id',
  'X-Correlation-Id',
];
const CSRF_TOKEN_PATTERN = /^[A-Za-z0-9._-]{1,256}$/;

const withoutCsrfCookie = (cookieHeader: string) =>
  cookieHeader
    .split(';')
    .map((cookie) => cookie.trim())
    .filter((cookie) => !cookie.toLowerCase().startsWith('csrf_token='))
    .filter(Boolean)
    .join('; ');

const normalizeBackendUrl = (url: string) =>
  url.replace(/\/+$/, '').replace(/\/api\/v1$/i, '');

const resolveBackendUrl = () => {
  const apiUrl = process.env.API_URL?.trim();
  if (apiUrl) {
    return normalizeBackendUrl(apiUrl);
  }

  if (process.env.NODE_ENV === 'production') {
    throw new Error('API_URL must be configured for the production server');
  }

  return normalizeBackendUrl('http://127.0.0.1:8000');
};

/**
 * 워크플로우 스트리밍 실행을 위한 프록시 API Route
 *
 * Next.js의 rewrites는 SSE 응답을 버퍼링하기 때문에,
 * API Route를 통해 직접 스트리밍 프록시를 구현합니다.
 */
export async function POST(
  request: NextRequest,
  { params }: { params: Promise<{ workflowId: string }> },
) {
  const resolvedParams = await params;
  const workflowId = resolvedParams.workflowId;

  // 로컬 dev에서 일반 API와 stream proxy가 같은 Gateway를 보도록 fallback 순서를 맞춘다.
  const backendUrl = resolveBackendUrl();

  // Content-Type 확인
  const contentType = request.headers.get('content-type') || 'application/json';
  const isFormData = contentType.includes('multipart/form-data');

  // 요청 바디 읽기
  let body: BodyInit;
  if (isFormData) {
    // FormData는 그대로 전달
    body = await request.formData();
  } else {
    // JSON은 텍스트로 전달
    body = await request.text();
  }

  const headers = new Headers();
  const cookie = request.headers.get('cookie') || '';
  const csrfToken = request.headers.get('X-CSRF-Token');
  const forwardedCookie = withoutCsrfCookie(cookie);
  if (csrfToken && CSRF_TOKEN_PATTERN.test(csrfToken)) {
    headers.set(
      'Cookie',
      [forwardedCookie, `csrf_token=${csrfToken}`].filter(Boolean).join('; '),
    );
  } else if (forwardedCookie) {
    headers.set('Cookie', forwardedCookie);
  }
  if (!isFormData) {
    headers.set('Content-Type', 'application/json');
  }

  for (const headerName of CONTEXT_HEADER_ALLOWLIST) {
    const value = request.headers.get(headerName);
    if (value) {
      headers.set(headerName, value);
    }
  }

  // FastAPI로 요청 전달
  const response = await fetch(
    `${backendUrl}/api/v1/workflows/${workflowId}/stream`,
    {
      method: 'POST',
      headers,
      body,
    },
  );

  // 에러 응답 처리
  if (!response.ok) {
    const errorText = await response.text().catch(() => '');
    let errorData: unknown = {};
    if (errorText) {
      try {
        errorData = JSON.parse(errorText);
      } catch {
        errorData = { detail: errorText };
      }
    }
    return new Response(JSON.stringify(errorData), {
      status: response.status,
      headers: { 'Content-Type': 'application/json' },
    });
  }

  // 스트리밍 응답 전달 (버퍼링 없이)
  return new Response(response.body, {
    headers: {
      'Content-Type': 'text/event-stream',
      'Cache-Control': 'no-cache, no-transform',
      Connection: 'keep-alive',
      'X-Accel-Buffering': 'no', // Nginx 버퍼링 비활성화
    },
  });
}
