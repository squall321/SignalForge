import { Alert, Card, Empty, List, Spin, Tag, Typography } from 'antd';
import { useQuery } from '@tanstack/react-query';
import dayjs from 'dayjs';
import { fetchAnomalies } from '../../services/communityApi';
import type { AnomalyItem, AnomalyKind } from '../../types/community';

const { Text } = Typography;

const KIND_LABEL: Record<AnomalyKind, { color: string; label: string }> = {
  volume_spike: { color: 'red', label: '게시 급증' },
  volume_drop:  { color: 'orange', label: '게시 급감' },
  sent_swing:   { color: 'purple', label: '감성 급변' },
  silence:      { color: 'default', label: '무수집' },
};

function scoreColor(score: number) {
  if (score >= 0.85) return '#cf1322';
  if (score >= 0.7) return '#fa8c16';
  return '#1677ff';
}

export default function AnomalyList() {
  const { data, isLoading, isError, error } = useQuery({
    queryKey: ['platform-anomalies'],
    queryFn: fetchAnomalies,
    staleTime: 60_000,
  });

  const sorted = data?.items
    ? [...data.items].sort((a, b) => b.score - a.score)
    : [];

  return (
    <Card title="이상치 (Anomalies)" bodyStyle={{ padding: 12 }}>
      {isLoading && (
        <div style={{ textAlign: 'center', padding: 48 }}>
          <Spin />
        </div>
      )}
      {isError && (
        <Alert
          type="error"
          showIcon
          message="이상치 조회 실패"
          description={error instanceof Error ? error.message : '알 수 없는 오류'}
        />
      )}
      {data && sorted.length === 0 && !isLoading && (
        <Empty description="감지된 이상치가 없습니다" />
      )}
      {sorted.length > 0 && !isLoading && (
        <List<AnomalyItem>
          size="small"
          dataSource={sorted}
          renderItem={(it) => {
            const meta = KIND_LABEL[it.kind];
            return (
              <List.Item style={{ padding: '8px 4px' }}>
                <div style={{ display: 'grid', gridTemplateColumns: '110px 100px 1fr 100px 140px', gap: 12, width: '100%', alignItems: 'center' }}>
                  <Text strong>{it.platform_code}</Text>
                  <Tag color={meta.color}>{meta.label}</Tag>
                  <Text>{it.description}</Text>
                  <Text style={{ color: scoreColor(it.score), fontWeight: 600, textAlign: 'right' }}>
                    score {it.score.toFixed(2)}
                  </Text>
                  <Text type="secondary" style={{ fontSize: 12, textAlign: 'right' }}>
                    {dayjs(it.detected_at).format('MM-DD HH:mm')}
                  </Text>
                </div>
              </List.Item>
            );
          }}
        />
      )}
    </Card>
  );
}
