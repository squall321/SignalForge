import { useState } from 'react';
import { Dropdown, Button, Modal, Typography } from 'antd';
import { MoreOutlined, DownloadOutlined, CodeOutlined, ExpandOutlined } from '@ant-design/icons';
import type { MenuProps } from 'antd';
// echarts-for-react 의 ref 타입은 default export 의 instance 메소드를 통해 dataURL 추출.
// 카드별로 ref 를 보유한 곳에서만 PNG 다운로드 활성화.

const { Paragraph } = Typography;

interface EChartsRefLike {
  getEchartsInstance(): { getDataURL(opts?: { type?: string; pixelRatio?: number; backgroundColor?: string }): string };
}

// 순수 함수 — 단위 테스트 대상.
// echartsRef 가 있으면 dataURL → PNG, 없으면 download 액션을 메뉴에서 제거한다.
export function buildCardMenuItems(opts: {
  hasChart: boolean;
  hasJson: boolean;
}): NonNullable<MenuProps['items']> {
  const items: NonNullable<MenuProps['items']> = [];
  if (opts.hasChart) {
    items.push({ key: 'png', icon: <DownloadOutlined />, label: 'PNG 저장' });
  }
  if (opts.hasJson) {
    items.push({ key: 'json', icon: <CodeOutlined />, label: 'JSON 응답 보기' });
  }
  items.push({ key: 'expand', icon: <ExpandOutlined />, label: '확대' });
  return items;
}

export function downloadPng(echartsRef: EChartsRefLike | null | undefined, filename: string): boolean {
  if (!echartsRef) return false;
  try {
    const url = echartsRef.getEchartsInstance().getDataURL({
      type: 'png',
      pixelRatio: 2,
      backgroundColor: '#fff',
    });
    const a = document.createElement('a');
    a.href = url;
    a.download = `${filename}.png`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    return true;
  } catch {
    return false;
  }
}

interface CardActionsProps {
  title: string;
  echartsRef?: EChartsRefLike | null;
  json?: unknown;
  // 확대 모드 시 body 영역(차트 등)을 더 큰 사이즈로 다시 렌더할 때 호출
  renderExpanded?: () => JSX.Element;
}

export default function CardActions({ title, echartsRef, json, renderExpanded }: CardActionsProps) {
  const [jsonOpen, setJsonOpen] = useState(false);
  const [expandOpen, setExpandOpen] = useState(false);

  const items = buildCardMenuItems({ hasChart: !!echartsRef, hasJson: json !== undefined });

  const onClick: MenuProps['onClick'] = ({ key }) => {
    if (key === 'png') downloadPng(echartsRef, title.replace(/\s+/g, '_'));
    else if (key === 'json') setJsonOpen(true);
    else if (key === 'expand') setExpandOpen(true);
  };

  return (
    <>
      <Dropdown menu={{ items, onClick }} trigger={['click']}>
        <Button
          type="text"
          size="small"
          icon={<MoreOutlined />}
          aria-label="card-actions"
          data-testid="card-actions-trigger"
        />
      </Dropdown>
      <Modal
        title={`${title} — JSON 응답`}
        open={jsonOpen}
        onCancel={() => setJsonOpen(false)}
        footer={null}
        width={720}
      >
        <Paragraph>
          <pre
            style={{
              background: '#fafafa',
              padding: 12,
              borderRadius: 6,
              maxHeight: 480,
              overflow: 'auto',
              fontSize: 12,
            }}
          >
            {JSON.stringify(json, null, 2)}
          </pre>
        </Paragraph>
      </Modal>
      <Modal
        title={title}
        open={expandOpen}
        onCancel={() => setExpandOpen(false)}
        footer={null}
        width="80%"
        styles={{ body: { minHeight: 480 } }}
        data-testid="card-expanded-modal"
      >
        {renderExpanded ? renderExpanded() : <div>확대 미지원</div>}
      </Modal>
    </>
  );
}
