// KG 페이지 좌측/상단 컨트롤 패널 — P2-3 T1
import { Card, Slider, Checkbox, Space, Typography } from 'antd';
import type { KGControls as KGControlsState, KGEdgeType } from '../../types/kg';

const { Text } = Typography;

const EDGE_TYPE_OPTIONS: { label: string; value: KGEdgeType }[] = [
  { label: '제품 ↔ 카테고리', value: 'product_category' },
  { label: '제품 ↔ 플랫폼', value: 'product_platform' },
  { label: '제품 ↔ 국가', value: 'product_country' },
];

export interface KGControlsProps {
  value: KGControlsState;
  onChange: (next: KGControlsState) => void;
}

export default function KGControls({ value, onChange }: KGControlsProps) {
  return (
    <Card size="small" title="그래프 제어">
      <Space direction="vertical" style={{ width: '100%' }} size={16}>
        <div>
          <Text strong>상위 노드 수 (top N): {value.topN}</Text>
          <Slider
            min={40}
            max={200}
            step={10}
            value={value.topN}
            onChange={(v) => onChange({ ...value, topN: v as number })}
            tooltip={{ formatter: (v) => `${v}` }}
          />
        </div>
        <div>
          <Text strong>엣지 최소 가중치: {value.minWeight}</Text>
          <Slider
            min={1}
            max={50}
            step={1}
            value={value.minWeight}
            onChange={(v) => onChange({ ...value, minWeight: v as number })}
            tooltip={{ formatter: (v) => `${v}` }}
          />
        </div>
        <div>
          <Text strong>엣지 타입</Text>
          <div style={{ marginTop: 6 }}>
            <Checkbox.Group
              options={EDGE_TYPE_OPTIONS}
              value={value.edgeTypes}
              onChange={(vals) =>
                onChange({ ...value, edgeTypes: vals as KGEdgeType[] })
              }
            />
          </div>
        </div>
      </Space>
    </Card>
  );
}
