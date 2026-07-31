import { DocumentApiEditConfigSummary } from '@/app/features/knowledge/types/Knowledge';

interface ApiSourceViewerProps {
  apiConfig?: DocumentApiEditConfigSummary | null;
}

export default function ApiSourceViewer({ apiConfig }: ApiSourceViewerProps) {
  return (
    <div className="w-full h-full bg-white rounded-lg shadow-sm border border-gray-200 dark:border-gray-700 dark:bg-gray-800 overflow-auto p-6">
      {apiConfig ? (
        <dl className="grid grid-cols-1 gap-4 text-sm sm:grid-cols-2">
          <div>
            <dt className="text-gray-500 dark:text-gray-400">소스</dt>
            <dd className="mt-1 font-medium text-gray-900 dark:text-gray-100">
              {apiConfig.safe_label}
            </dd>
          </div>
          <div>
            <dt className="text-gray-500 dark:text-gray-400">요청 방식</dt>
            <dd className="mt-1 font-medium text-gray-900 dark:text-gray-100">
              {apiConfig.method}
            </dd>
          </div>
          <div>
            <dt className="text-gray-500 dark:text-gray-400">헤더 설정</dt>
            <dd className="mt-1 font-medium text-gray-900 dark:text-gray-100">
              {apiConfig.has_headers ? '설정됨' : '없음'}
            </dd>
          </div>
          <div>
            <dt className="text-gray-500 dark:text-gray-400">본문 설정</dt>
            <dd className="mt-1 font-medium text-gray-900 dark:text-gray-100">
              {apiConfig.has_body ? '설정됨' : '없음'}
            </dd>
          </div>
        </dl>
      ) : (
        <p className="text-sm text-gray-600 dark:text-gray-300">
          API 소스 설정은 수정 권한과 안전한 설정 복원이 확인된 경우에만 표시됩니다.
        </p>
      )}
    </div>
  );
}
