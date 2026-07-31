import { App } from '../api/appApi';
import { DeploymentType } from '../../workflow/types/Deployment';

export interface ModuleTag {
  label: string;
  type: DeploymentType | 'undeployed';
}

export function getModuleTags(app: App): ModuleTag[] {
  const tags: ModuleTag[] = [];

  // 배포 방식 태그 (가장 마지막에 설정한 하나만)
  if (app.active_deployment_type) {
    const deploymentTagMap: Record<
      string,
      { label: string; type: ModuleTag['type'] }
    > = {
      api: { label: 'REST API', type: 'api' },
      webapp: { label: '웹 앱', type: 'webapp' },
      widget: { label: '위젯', type: 'widget' },
      chatbot: { label: '공개 챗봇', type: 'chatbot' },
      internal_chatbot: {
        label: '내부 챗봇',
        type: 'internal_chatbot',
      },
      mcp: { label: 'MCP', type: 'mcp' },
      workflow_node: { label: '서브 모듈', type: 'workflow_node' },
      webhook: { label: '웹훅', type: 'webhook' },
      schedule: { label: '알람', type: 'schedule' },
    };

    const tag = deploymentTagMap[app.active_deployment_type];
    if (tag) tags.push(tag);
  } else {
    tags.push({ label: '미배포', type: 'undeployed' });
  }

  return tags;
}
