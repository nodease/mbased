import type { NextConfig } from 'next';
import { dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

import { resolvePublicApiBaseUrl } from './lib/publicApiOrigin';

const clientRoot = dirname(fileURLToPath(import.meta.url));

// NEXT_PUBLIC_* values are embedded at build time. Reject an internal or
// plaintext production origin before creating a deployable browser bundle.
resolvePublicApiBaseUrl(process.env.NEXT_PUBLIC_API_URL, process.env.NODE_ENV);

const nextConfig: NextConfig = {
  output: 'standalone',
  turbopack: {
    root: clientRoot,
  },

  async headers() {
    return [
      // 1. 공유 페이지: 기존 제품 계약 유지
      {
        source: '/shared/:path*',
        headers: [
          {
            key: 'Content-Security-Policy',
            value: 'frame-ancestors http: https: file: data:',
          },
        ],
      },
      // 2. 나머지 페이지: 임베딩 및 공유 페이지를 '제외한' 모든 경로
      // 정규식 설명: (?!embed|shared) -> embed나 shared로 시작하지 않는 모든 경로
      {
        source: '/((?!embed|shared).*)',
        headers: [
          {
            key: 'X-Frame-Options',
            value: 'SAMEORIGIN',
          },
          {
            key: 'Content-Security-Policy',
            value: "frame-ancestors 'self'",
          },
        ],
      },
    ];
  },
  async rewrites() {
    // Development/direct-Next fallback only. Supported production entrypoints
    // route /api to Gateway at Ingress/Nginx before the request reaches Next.js.
    const backendUrl = process.env.API_URL || 'http://localhost:8000';

    return [
      {
        source: '/api/:path*',
        // 로컬 개발: Gateway 직접 실행(http://localhost:8000)
        // Next.js 직접 실행 테스트: .env.local에 API_URL 설정
        destination: `${backendUrl}/api/:path*`,
      },
    ];
  },
  experimental: {
    serverActions: {
      bodySizeLimit: '50mb', //파일 업로드 제한 50mb
    },
    // Proxy client body size limit for large file uploads.
    proxyClientMaxBodySize: '50mb',
    // Planner initial + semantic repair calls share a 7-minute proxy budget.
    proxyTimeout: 420000,
  },
};

export default nextConfig;
