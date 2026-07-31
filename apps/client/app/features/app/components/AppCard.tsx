'use client';

import { useState, useRef, useEffect } from 'react';
import {
  MoreHorizontal,
  Pencil,
  Globe,
  Lock,
  ToggleLeft,
  ToggleRight,
  History,
} from 'lucide-react';
import { type App } from '../api/appApi';
import { Tag } from './Tag';
import { getModuleTags } from '../utils/tagUtils';
import DeploymentListModal from './DeploymentListModal';

interface AppCardProps {
  app: App;
  onClick: (app: App) => void;
  onEdit: (e: React.MouseEvent, app: App) => void;
  onToggleMarketplace: (app: App) => void;
  onToggleDeployment: (app: App) => void;
  onRefresh?: () => void;
}

export default function AppCard({
  app,
  onClick,
  onEdit,
  onToggleMarketplace,
  onToggleDeployment,
  onRefresh,
}: AppCardProps) {
  const [isMenuOpen, setIsMenuOpen] = useState(false);
  const [isDeploymentModalOpen, setIsDeploymentModalOpen] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(event.target as Node)) {
        setIsMenuOpen(false);
      }
    };

    if (isMenuOpen) {
      document.addEventListener('mousedown', handleClickOutside);
    }

    return () => {
      document.removeEventListener('mousedown', handleClickOutside);
    };
  }, [isMenuOpen]);

  const toggleMenu = (e: React.MouseEvent) => {
    e.stopPropagation();
    setIsMenuOpen(!isMenuOpen);
  };

  return (
    <div
      onClick={() => onClick(app)}
      className="group cursor-pointer rounded-lg border border-slate-200 bg-white px-6 py-6 shadow-sm transition-all hover:border-slate-300 hover:shadow-md"
    >
      {/* 카드 헤더 */}
      <div className="flex items-start justify-between mb-3">
        <div className="flex-1">
          <h3 className="mb-1 text-base font-black text-slate-950">
            {app.name}
          </h3>
          <p className="text-xs font-semibold leading-relaxed text-slate-500">
            {app.description}
          </p>
        </div>
        {/* 아이콘 */}
        <div
          className="ml-3 flex h-11 w-11 flex-shrink-0 items-center justify-center rounded-lg text-2xl shadow-sm"
          style={{ backgroundColor: app.icon?.background_color }}
        >
          {app.icon?.content}
        </div>
      </div>

      {/* 태그 영역 */}
      <div className="flex flex-wrap gap-1.5 mt-3 mb-2">
        {getModuleTags(app).map((tag, index) => (
          <Tag key={index} label={tag.label} type={tag.type} />
        ))}
      </div>

      <div className="mt-2 flex items-center justify-between text-xs font-semibold text-slate-400">
        <div className="flex items-center gap-1">
          {app.owner_name ? (
            <>
              <div className="w-4 h-4 bg-blue-100 rounded-full flex items-center justify-center">
                <span className="text-[8px] text-blue-600 font-medium">
                  {app.owner_name.charAt(0).toUpperCase()}
                </span>
              </div>
              <span className="text-slate-500">{app.owner_name}</span>
            </>
          ) : (
            <div className="w-4 h-4 bg-blue-100 rounded-full flex items-center justify-center">
              <span className="text-[8px] text-blue-600 font-medium">U</span>
            </div>
          )}
          <span>
            • Edited{' '}
            {new Date(app.updated_at).toLocaleDateString('en-US', {
              month: '2-digit',
              day: '2-digit',
              year: '2-digit',
            })}
          </span>
        </div>

        {/* 더보기 메뉴 */}
        <div className="relative" ref={menuRef}>
          <button
            onClick={toggleMenu}
            className="rounded-md p-1 text-slate-400 transition-colors hover:bg-slate-100 hover:text-slate-900"
            title="더보기"
          >
            <MoreHorizontal className="w-4 h-4" />
          </button>

          {isMenuOpen && (
            <div
              className="absolute right-0 bottom-full z-10 mb-2 w-56 animate-in rounded-lg border border-slate-200 bg-white py-1 shadow-lg fade-in zoom-in-95 duration-100"
              onClick={(e) => e.stopPropagation()}
            >
              {/* 앱 이름/설명 수정하기 */}
              <button
                onClick={(e) => {
                  setIsMenuOpen(false);
                  onEdit(e, app);
                }}
                className="w-full px-3 py-2 text-left text-sm text-slate-700 hover:bg-slate-50 flex items-center gap-2"
              >
                <Pencil className="w-3.5 h-3.5" />
                <span>앱 정보 수정</span>
              </button>

              {app.is_market ? (
                <button
                  onClick={(e) => {
                    e.stopPropagation();
                    setIsMenuOpen(false);
                    onToggleMarketplace(app);
                  }}
                  className="w-full px-3 py-2 text-left text-sm text-slate-700 hover:bg-slate-50 flex items-center gap-2"
                >
                  <Lock className="w-3.5 h-3.5" />
                  <span>마켓플레이스 비공개로 전환</span>
                </button>
              ) : (
                <button
                  onClick={(e) => {
                    e.stopPropagation();
                    if (app.forked_from) return; // 복제된 앱은 공개 불가
                    setIsMenuOpen(false);
                    onToggleMarketplace(app);
                  }}
                  className={`w-full px-3 py-2 text-left text-sm flex items-center gap-2 ${
                    app.forked_from
                      ? 'text-slate-400 cursor-not-allowed'
                      : 'text-slate-700 hover:bg-slate-50'
                  }`}
                  title={
                    app.forked_from
                      ? '복제된 앱은 마켓에 공개할 수 없습니다.'
                      : ''
                  }
                >
                  <Globe className="w-3.5 h-3.5" />
                  <span>
                    마켓플레이스에 공개
                    {app.forked_from && (
                      <span className="text-xs ml-1">(복제된 앱 불가)</span>
                    )}
                  </span>
                </button>
              )}

              <div className="my-1 border-t border-slate-100" />

              {/* 배포 토글 */}
              <button
                className={`w-full px-3 py-2 text-left text-sm flex items-center gap-2 ${
                  app.active_deployment_id
                    ? 'text-slate-700 hover:bg-slate-50'
                    : 'text-slate-400 cursor-not-allowed'
                }`}
                onClick={(e) => {
                  e.stopPropagation();
                  if (app.active_deployment_id) {
                    setIsMenuOpen(false);
                    onToggleDeployment(app);
                  }
                }}
                disabled={!app.active_deployment_id}
                title={
                  app.active_deployment_id
                    ? app.active_deployment_is_active
                      ? '배포 비활성화'
                      : '배포 활성화'
                    : '배포가 없습니다'
                }
              >
                {app.active_deployment_id ? (
                  app.active_deployment_is_active ? (
                    <>
                      <ToggleRight className="w-3.5 h-3.5 text-green-600" />
                      <span>배포 끄기</span>
                      <span className="ml-auto text-xs font-medium text-green-600">
                        ON
                      </span>
                    </>
                  ) : (
                    <>
                      <ToggleLeft className="w-3.5 h-3.5 text-slate-400" />
                      <span>배포 켜기</span>
                      <span className="ml-auto text-xs font-medium text-slate-400">
                        OFF
                      </span>
                    </>
                  )
                ) : (
                  <>
                    <ToggleLeft className="w-3.5 h-3.5" />
                    <span>배포 없음</span>
                  </>
                )}
              </button>

              {/* 서브 모듈 배포 목록 */}
              <button
                className="w-full px-3 py-2 text-left text-sm text-slate-700 hover:bg-slate-50 flex items-center gap-2"
                onClick={(e) => {
                  e.stopPropagation();
                  setIsMenuOpen(false);
                  setIsDeploymentModalOpen(true);
                }}
              >
                <History className="w-3.5 h-3.5" />
                <span>배포 목록 보기</span>
              </button>
            </div>
          )}
        </div>
      </div>

      {/* 배포 목록 모달 */}
      {isDeploymentModalOpen && (
        <DeploymentListModal
          appId={app.id}
          appName={app.name}
          onClose={() => setIsDeploymentModalOpen(false)}
          onDeploymentToggle={() => {
            // 배포 토글 시 부모에게 알림 (앱 목록 새로고침)
            onRefresh?.();
          }}
        />
      )}
    </div>
  );
}
