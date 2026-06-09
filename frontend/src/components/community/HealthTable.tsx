import { Alert, Card, Spin, Table, Tag, Typography } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { useQuery } from '@tanstack/react-query';
import dayjs from 'dayjs';
import { fetchPlatformHealth } from '../../services/communityApi';
import type { PlatformHealth, PlatformStatus } from '../../types/community';

const { Text } = Typography;

const STATUS_COLOR: Record<PlatformStatus, string> = {
  active: 'green',
  idle: 'orange',
  dead: 'red',
};

const COLUMNS: ColumnsType<PlatformHealth> = [
  {
    title: '사이트',
    dataIndex: 'code',
    key: 'code',
    sorter: (a, b) => a.code.localeCompare(b.code),
    render: (v: string, row) =>
      row.base_url ? (
        <a href={row.base_url} target="_blank" rel="noreferrer">
          {v}
        </a>
      ) : (
        v
      ),
  },
  {
    title: '지역',
    dataIndex: 'region',
    key: 'region',
    sorter: (a, b) => (a.region || '').localeCompare(b.region || ''),
    render: (v?: string | null) => v || <Text type="secondary">-</Text>,
  },
  {
    title: '24h 게시',
    dataIndex: 'posts_24h',
    key: 'posts_24h',
    sorter: (a, b) => a.posts_24h - b.posts_24h,
    align: 'right' as const,
    render: (v: number) => v.toLocaleString(),
  },
  {
    title: '7d 게시',
    dataIndex: 'posts_7d',
    key: 'posts_7d',
    sorter: (a, b) => a.posts_7d - b.posts_7d,
    defaultSortOrder: 'descend' as const,
    align: 'right' as const,
    render: (v: number) => v.toLocaleString(),
  },
  {
    title: '7d 평균 감성',
    dataIndex: 'sent_avg_7d',
    key: 'sent_avg_7d',
    sorter: (a, b) => (a.sent_avg_7d ?? 0) - (b.sent_avg_7d ?? 0),
    align: 'right' as const,
    render: (v: number | null) => {
      if (v == null) return <Text type="secondary">-</Text>;
      const color = v > 0.1 ? '#3f8600' : v < -0.1 ? '#cf1322' : undefined;
      return <span style={{ color }}>{v.toFixed(2)}</span>;
    },
  },
  {
    title: '본문 길이',
    dataIndex: 'avg_body_len_7d',
    key: 'avg_body_len_7d',
    sorter: (a, b) => (a.avg_body_len_7d ?? 0) - (b.avg_body_len_7d ?? 0),
    align: 'right' as const,
    render: (v: number | null) => (v == null ? '-' : v.toLocaleString()),
  },
  {
    title: '최근 수집',
    dataIndex: 'last_collected',
    key: 'last_collected',
    sorter: (a, b) =>
      new Date(a.last_collected || 0).getTime() - new Date(b.last_collected || 0).getTime(),
    render: (v?: string | null) =>
      v ? dayjs(v).format('YYYY-MM-DD HH:mm') : <Text type="secondary">-</Text>,
  },
  {
    title: '상태',
    dataIndex: 'status',
    key: 'status',
    sorter: (a, b) => a.status.localeCompare(b.status),
    filters: [
      { text: 'active', value: 'active' },
      { text: 'idle', value: 'idle' },
      { text: 'dead', value: 'dead' },
    ],
    onFilter: (val, row) => row.status === val,
    render: (v: PlatformStatus) => <Tag color={STATUS_COLOR[v]}>{v}</Tag>,
  },
];

export default function HealthTable() {
  const { data, isLoading, isError, error } = useQuery({
    queryKey: ['platform-health'],
    queryFn: fetchPlatformHealth,
    staleTime: 60_000,
  });

  return (
    <Card title="사이트 상태" bodyStyle={{ padding: 12 }}>
      {isLoading && (
        <div style={{ textAlign: 'center', padding: 48 }}>
          <Spin />
        </div>
      )}
      {isError && (
        <Alert
          type="error"
          showIcon
          message="플랫폼 상태 조회 실패"
          description={error instanceof Error ? error.message : '알 수 없는 오류'}
        />
      )}
      {data && !isLoading && (
        <Table<PlatformHealth>
          rowKey="platform_id"
          dataSource={data.platforms}
          columns={COLUMNS}
          size="small"
          pagination={{ pageSize: 20, showSizeChanger: true }}
        />
      )}
    </Card>
  );
}
