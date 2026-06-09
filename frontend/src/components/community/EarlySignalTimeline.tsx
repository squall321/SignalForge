import { useMemo, useState } from 'react';
import { Alert, Card, Empty, Input, Space, Spin, Tag, Typography } from 'antd';
import { useQuery } from '@tanstack/react-query';
import dayjs from 'dayjs';
import { fetchEarlySignal } from '../../services/communityApi';
import type { EarlySignalRow } from '../../types/community';

const { Text } = Typography;

interface RowProps {
  row: EarlySignalRow;
  maxLag: number;
  isLeader: boolean;
}

function TimelineRow({ row, maxLag, isLeader }: RowProps) {
  const widthPct = maxLag > 0 ? (row.lag_hours / maxLag) * 100 : 0;
  return (
    <div
      style={{
        display: 'grid',
        gridTemplateColumns: '120px 1fr 80px',
        alignItems: 'center',
        gap: 8,
        padding: '6px 0',
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
        <Text strong style={{ fontSize: 12 }}>
          {row.platform_code}
        </Text>
        {isLeader && <Tag color="gold">선두</Tag>}
      </div>
      <div
        style={{
          position: 'relative',
          height: 18,
          background: '#f5f5f5',
          borderRadius: 2,
        }}
      >
        <div
          style={{
            position: 'absolute',
            left: 0,
            top: 0,
            height: '100%',
            width: `${widthPct}%`,
            background: isLeader ? '#52c41a' : '#1677ff',
            opacity: 0.85,
            borderRadius: 2,
          }}
        />
        <div
          style={{
            position: 'absolute',
            left: 4,
            top: 0,
            fontSize: 11,
            color: '#333',
            lineHeight: '18px',
          }}
        >
          +{row.lag_hours.toFixed(1)}h · {dayjs(row.first_seen).format('MM-DD HH:mm')}
        </div>
      </div>
      <Text type="secondary" style={{ fontSize: 12, textAlign: 'right' }}>
        {row.count_24h.toLocaleString()}
      </Text>
    </div>
  );
}

export default function EarlySignalTimeline() {
  const [signal, setSignal] = useState('GS25U');
  const { data, isLoading, isError, error } = useQuery({
    queryKey: ['platform-early-signal', signal],
    queryFn: () => fetchEarlySignal(signal),
    staleTime: 60_000,
    enabled: !!signal,
  });

  const sorted = useMemo(
    () => (data?.rows ? [...data.rows].sort((a, b) => a.lag_hours - b.lag_hours) : []),
    [data],
  );
  const maxLag = useMemo(
    () => (sorted.length ? sorted[sorted.length - 1].lag_hours : 1),
    [sorted],
  );

  return (
    <Card
      title="Early Signal — 플랫폼 간 등장 시점 lag"
      extra={
        <Space>
          <Text type="secondary">신호:</Text>
          <Input
            size="small"
            value={signal}
            onChange={(e) => setSignal(e.target.value.toUpperCase())}
            style={{ width: 140 }}
            placeholder="예: GS25U"
          />
        </Space>
      }
      bodyStyle={{ padding: 16 }}
    >
      {isLoading && (
        <div style={{ textAlign: 'center', padding: 48 }}>
          <Spin />
        </div>
      )}
      {isError && (
        <Alert
          type="error"
          showIcon
          message="Early Signal 조회 실패"
          description={error instanceof Error ? error.message : '알 수 없는 오류'}
        />
      )}
      {data && sorted.length === 0 && !isLoading && <Empty description="lag 데이터 없음" />}
      {sorted.length > 0 && !isLoading && (
        <div>
          <div
            style={{
              display: 'grid',
              gridTemplateColumns: '120px 1fr 80px',
              padding: '0 0 8px 0',
              borderBottom: '1px solid #f0f0f0',
              marginBottom: 4,
            }}
          >
            <Text type="secondary" style={{ fontSize: 11 }}>플랫폼</Text>
            <Text type="secondary" style={{ fontSize: 11 }}>등장 시점 (선두 대비 lag)</Text>
            <Text type="secondary" style={{ fontSize: 11, textAlign: 'right' }}>24h 건수</Text>
          </div>
          {sorted.map((row, idx) => (
            <TimelineRow key={row.platform_code} row={row} maxLag={maxLag} isLeader={idx === 0} />
          ))}
        </div>
      )}
    </Card>
  );
}
