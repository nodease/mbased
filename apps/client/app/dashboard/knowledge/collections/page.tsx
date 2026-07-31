'use client';

import Link from 'next/link';
import { ArrowLeft } from 'lucide-react';
import KnowledgeCollectionManager from '@/app/features/knowledge/components/knowledge-collection-manager';

export default function KnowledgeCollectionsPage() {
  return (
    <div className="min-h-full bg-white p-8">
      <div className="mb-6 flex items-center justify-between gap-4">
        <div>
          <Link
            href="/dashboard/knowledge"
            className="mb-3 inline-flex items-center gap-2 text-sm font-semibold text-slate-500 hover:text-slate-900"
          >
            <ArrowLeft className="h-4 w-4" />
            지식 관리
          </Link>
          <h1 className="text-2xl font-bold text-slate-900">
            Knowledge Collections
          </h1>
        </div>
      </div>
      <KnowledgeCollectionManager />
    </div>
  );
}
