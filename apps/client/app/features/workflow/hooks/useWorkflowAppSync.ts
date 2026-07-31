import { useEffect, useState } from 'react';
import { useParams } from 'next/navigation';
import { useWorkflowStore } from '@/app/features/workflow/store/useWorkflowStore';
import { workflowApi } from '../api/workflowApi';
import { appApi } from '@/app/features/app/api/appApi';
import { isMockWorkflowId } from '../utils/mockMode';
import { setActiveOrganizationId } from '@/lib/activeOrganization';

export const useWorkflowAppSync = () => {
  const params = useParams();
  const workflowId = params.id as string;
  const [currentAppId, setCurrentAppId] = useState<string>('');
  const loadWorkflowsByApp = useWorkflowStore(
    (state) => state.loadWorkflowsByApp,
  );
  const setProjectInfo = useWorkflowStore((state) => state.setProjectInfo);
  const setActiveWorkflowIdSafe = useWorkflowStore(
    (state) => state.setActiveWorkflowIdSafe,
  );
  const setProjectApp = useWorkflowStore((state) => state.setProjectApp);
  const setWorkflowAccess = useWorkflowStore(
    (state) => state.setWorkflowAccess,
  );

  useEffect(() => {
    let active = true;

    const loadWorkflowAppId = async () => {
      if (isMockWorkflowId(workflowId)) {
        if (!active) return;
        setCurrentAppId('mock-app');
        setProjectInfo(
          'Mock 워크플로우',
          { type: 'emoji', content: 'M', background_color: '#2563eb' },
          '백엔드 없이 UI/UX 작업을 하기 위한 샘플 워크플로우입니다.',
        );
        setProjectApp({
          id: 'mock-app',
          name: 'Mock 워크플로우',
          description: '백엔드 없이 UI/UX 작업을 하기 위한 샘플 워크플로우입니다.',
          icon: { type: 'emoji', content: 'M', background_color: '#2563eb' },
          is_market: false,
          workflow_id: workflowId,
          created_at: new Date().toISOString(),
          updated_at: new Date().toISOString(),
        });
        setActiveWorkflowIdSafe(workflowId);
        return;
      }

      try {
        const data = await workflowApi.getWorkflow(workflowId);
        if (!active) return;
        try {
          const access = await workflowApi.getWorkflowPermission(workflowId);
          if (!active) return;
          setWorkflowAccess(access);
          setActiveOrganizationId(access.organization_id);
        } catch (permissionError) {
          if (!active) return;
          console.error('Failed to load workflow permissions:', permissionError);
          setWorkflowAccess(null);
        }
        if (data.app_id) {
          setCurrentAppId(data.app_id);

          // 앱 정보 가져오기 (이름, 아이콘)
          try {
            const app = await appApi.getApp(data.app_id);
            if (!active) return;
            setProjectInfo(app.name, app.icon, app.description);
            setProjectApp(app);
          } catch (appError) {
            if (!active) return;
            console.error('Failed to load app details:', appError);
          }
        }
      } catch (error) {
        if (!active) return;
        console.error('Failed to load workflow app_id:', error);
      }
    };

    // URL의 workflowId가 변경되면 현재 활성 워크플로우 ID도 업데이트
    // 단, 여기서 직접 loadWorkflowsByApp을 호출하진 않음 (아래 effect에서 처리)
    if (workflowId) {
      setWorkflowAccess(null);
      void loadWorkflowAppId();
    }

    return () => {
      active = false;
    };
  }, [
    workflowId,
    setProjectInfo,
    setActiveWorkflowIdSafe,
    setProjectApp,
    setWorkflowAccess,
  ]);

  useEffect(() => {
    const initWorkflows = async () => {
      if (isMockWorkflowId(workflowId)) {
        setActiveWorkflowIdSafe(workflowId);
        return;
      }

      if (currentAppId) {
        await loadWorkflowsByApp(currentAppId);
        // 워크플로우 목록 로드 후 활성 워크플로우 식별자만 설정 (데이터 덮어쓰기 방지)
        setActiveWorkflowIdSafe(workflowId);
      }
    };
    initWorkflows();
  }, [currentAppId, loadWorkflowsByApp, workflowId, setActiveWorkflowIdSafe]);
};
