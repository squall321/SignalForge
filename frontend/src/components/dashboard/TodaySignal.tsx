// P5 R6 트랙 A — "오늘의 신호" 카드.
// LLM 키 미입력 환경에서도 동작하도록 fallback narrative 우선.
import { Card, List, Typography, Tag } from 'antd';
import { ThunderboltOutlined } from '@ant-design/icons';
import type { DashboardOverviewResponse } from '../../types/dashboard';
import { buildFallbackSignal } from './kpiUtils';

interface Props {
  overview: DashboardOverviewResponse | null;
  loading?: boolean;
}

export default function TodaySignal({ overview, loading }: Props) {
  const narrative = buildFallbackSignal(overview);

  return (
    <Card
      size="small"
      title={
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 8 }}>
          <ThunderboltOutlined style={{ color: '#fa8c16' }} />
          오늘의 신호
          <Tag color="default" style={{ marginLeft: 4 }}>
            룰 기반
          </Tag>
        </span>
      }
      loading={loading}
    >
      <Typography.Title level={5} style={{ marginTop: 0, marginBottom: 8 }}>
        {narrative.headline}
      </Typography.Title>
      <List
        size="small"
        dataSource={narrative.bullets}
        renderItem={(item) => (
          <List.Item style={{ padding: '4px 0', border: 'none' }}>
            <Typography.Text>• {item}</Typography.Text>
          </List.Item>
        )}
      />
    </Card>
  );
}
