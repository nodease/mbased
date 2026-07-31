'use client';

import { useRouter } from 'next/navigation';
import { LayoutDashboard } from 'lucide-react';

export default function Logo({ collapsed }: { collapsed?: boolean }) {
  const router = useRouter();

  return (
    <div
      className={`w-full flex items-center ${
        collapsed ? 'justify-center pt-8 pb-4' : 'px-4 pt-8 pb-4'
      }`}
    >
      <button
        onClick={() => router.push('/dashboard')}
        className="flex cursor-pointer items-center gap-3 transition-opacity hover:opacity-80"
        aria-label="Nodease dashboard"
      >
        {collapsed ? (
          <span className="grid h-8 w-8 place-items-center rounded-lg bg-slate-950 text-white">
            <LayoutDashboard className="h-4 w-4" />
          </span>
        ) : (
          <>
            <span className="grid h-9 w-9 place-items-center rounded-lg bg-slate-950 text-white">
              <LayoutDashboard className="h-5 w-5" />
            </span>
            <span className="text-sm font-black text-slate-950">Nodease</span>
          </>
        )}
      </button>
    </div>
  );
}
