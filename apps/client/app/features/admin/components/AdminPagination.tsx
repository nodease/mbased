'use client';

type AdminPaginationProps = {
  page: number;
  totalPages: number;
  total: number;
  hasNext?: boolean;
  onPageChange: (page: number) => void;
};

export function AdminPagination({
  page,
  totalPages,
  total,
  hasNext,
  onPageChange,
}: AdminPaginationProps) {
  return (
    <div className="flex flex-wrap items-center justify-between gap-3 border-t border-slate-100 px-5 py-3 text-sm">
      <span className="text-slate-500">
        {total}개 중 page {page}/{totalPages}
      </span>
      <div className="flex gap-2">
        <button
          type="button"
          onClick={() => onPageChange(Math.max(1, page - 1))}
          disabled={page <= 1}
          className="h-8 rounded-md border border-slate-200 px-3 text-xs font-semibold text-slate-600 disabled:cursor-not-allowed disabled:opacity-40"
        >
          이전
        </button>
        <button
          type="button"
          onClick={() =>
            onPageChange(
              hasNext === undefined
                ? Math.min(totalPages, page + 1)
                : page + 1,
            )
          }
          disabled={hasNext === undefined ? page >= totalPages : !hasNext}
          className="h-8 rounded-md border border-slate-200 px-3 text-xs font-semibold text-slate-600 disabled:cursor-not-allowed disabled:opacity-40"
        >
          다음
        </button>
      </div>
    </div>
  );
}
