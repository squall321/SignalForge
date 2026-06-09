// Track B — 수집 채널 모니터링 대시보드.
// 60+ 사이트의 수집량/지연/실패율을 한 화면에서 보고, 지역별 분포와
// 시간대별 추이까지 단일 페이지로 본다.
import { useMemo } from 'react';
import {
  Alert,
  Card,
  Col,
  Empty,
  Row,
  Space,
  Spin,
  Statistic,
  Table,
  Tag,
  Tooltip,
  Typography,
} from 'antd';
import {
  CheckCircleOutlined,
  WarningOutlined,
  PauseCircleOutlined,
  CloseCircleOutlined,
} from '@ant-design/icons';
import ReactECharts from 'echarts-for-react';
import { useQuery } from '@tanstack/react-query';
import dayjs from 'dayjs';
import {
  fetchCollectionStatus,
  HEALTH_BADGE,
  countByHealth,
  regionSorted,
  type CollectionPlatform,
  type PlatformHealth,
} from '../services/collectionStatusApi';
import {
  makeBaseOption,
  formatCount,
  palette,
} from '../utils/chartTheme';

const { Title, Paragraph, Text } = Typography;

const HEALTH_ICON: Record<PlatformHealth, React.ReactNode> = {
  active: <CheckCircleOutlined style={{ color: palette.positive }} />,
  slow: <WarningOutlined style={{ color: palette.accent }} />,
  stale: <PauseCircleOutlined style={{ color: palette.warning }} />,
  dead: <CloseCircleOutlined style={{ color: palette.negative }} />,
};

function healthTag(h: PlatformHealth) {
  const meta = HEALTH_BADGE[h];
  return (
    <Tag color={meta.color} style={{ marginRight: 0 }}>
      {meta.label}
    </Tag>
  );
}

// 시간대별 추이 차트 — 응답에 시간 시계열이 없으므로 platform.records_1h vs
// records_24h 비율로 "최근 1h 가 일평균 대비 몇 %" 의 분포만 표시한다.
// 실시간 모니터링 핵심 신호: 어느 플랫폼이 최근 1h 에 강세/약세인지.
function buildHourlyShareOption(platforms: CollectionPlatform[]) {
  // 24h 신규가 있는 플랫폼 TOP 12 만 노출 (가시성).
  const top = platforms
    .filter((p) => p.records_24h > 0)
    .sort((a, b) => b.records_24h - a.records_24h)
    .slice(0, 12);
  const base = makeBaseOption({ withDataZoom: false, withToolbox: true });
  return {
    ...base,
    xAxis: {
      type: 'category',
      data: top.map((p) => p.code),
      axisLabel: { fontSize: 10, rotate: 30 },
    },
    yAxis: [
      { type: 'value', name: '건수', position: 'left' },
    ],
    series: [
      {
        name: 'records_1h',
        type: 'bar',
        data: top.map((p) => p.records_1h),
        itemStyle: { color: palette.primary },
      },
      {
        name: 'records_24h',
        type: 'line',
        yAxisIndex: 0,
        data: top.map((p) => p.records_24h),
        itemStyle: { color: palette.accent },
        smooth: true,
      },
    ],
  };
}

export default function CollectionStatus() {
  const { data, isLoading, isError, error } = useQuery({
    queryKey: ['collection-status', 24],
    queryFn: () => fetchCollectionStatus(24),
    staleTime: 60_000,
    refetchInterval: 60_000,
  });

  const healthCounts = useMemo(
    () => (data ? countByHealth(data.platforms) : null),
    [data],
  );
  const regions = useMemo(
    () => (data ? regionSorted(data.by_region) : []),
    [data],
  );
  const chartOpt = useMemo(
    () => (data ? buildHourlyShareOption(data.platforms) : null),
    [data],
  );

  const columns = [
    {
      title: '상태',
      dataIndex: 'health',
      key: 'health',
      width: 90,
      render: (h: PlatformHealth) => (
        <Space size={4}>
          {HEALTH_ICON[h]}
          {healthTag(h)}
        </Space>
      ),
      filters: (Object.keys(HEALTH_BADGE) as PlatformHealth[]).map((h) => ({
        text: HEALTH_BADGE[h].label,
        value: h,
      })),
      onFilter: (v: boolean | React.Key, r: CollectionPlatform) => r.health === v,
    },
    {
      title: '코드',
      dataIndex: 'code',
      key: 'code',
      width: 130,
      render: (c: string) => <Text code>{c}</Text>,
    },
    { title: '이름', dataIndex: 'name', key: 'name', ellipsis: true },
    {
      title: '지역',
      dataIndex: 'region',
      key: 'region',
      width: 80,
      render: (r: string | null) => r ?? '-',
    },
    {
      title: '활성',
      dataIndex: 'is_active',
      key: 'is_active',
      width: 70,
      render: (a: boolean) => (a ? <Tag color="blue">활성</Tag> : <Tag>비활성</Tag>),
    },
    {
      title: '24h',
      dataIndex: 'records_24h',
      key: 'records_24h',
      width: 100,
      align: 'right' as const,
      sorter: (a: CollectionPlatform, b: CollectionPlatform) =>
        a.records_24h - b.records_24h,
      defaultSortOrder: 'descend' as const,
      render: (v: number) => v.toLocaleString(),
    },
    {
      title: '1h',
      dataIndex: 'records_1h',
      key: 'records_1h',
      width: 80,
      align: 'right' as const,
      render: (v: number) => v.toLocaleString(),
    },
    {
      title: '7d 평균/일',
      dataIndex: 'avg_per_day_7d',
      key: 'avg_per_day_7d',
      width: 110,
      align: 'right' as const,
      render: (v: number) => v.toLocaleString(),
    },
    {
      title: '마지막 수집',
      dataIndex: 'last_collected',
      key: 'last_collected',
      width: 160,
      render: (s: string | null, row: CollectionPlatform) => {
        if (!s) return <Text type="secondary">-</Text>;
        const ago = row.hours_since_last;
        return (
          <Tooltip title={s}>
            <Text type={ago !== null && ago >= 24 ? 'danger' : undefined}>
              {dayjs(s).format('MM-DD HH:mm')}
              {ago !== null ? ` (${ago.toFixed(1)}h前)` : ''}
            </Text>
          </Tooltip>
        );
      },
    },
  ];

  return (
    <div data-testid="collection-status-page">
      <Title level={3} style={{ marginTop: 0 }}>
        수집 상태
      </Title>
      <Paragraph type="secondary">
        60+ 플랫폼의 24h 수집량·지연·정체 여부를 한 화면에서 봅니다. 60초마다 자동
        새로고침.
      </Paragraph>

      {isError && (
        <Alert
          type="error"
          showIcon
          message="수집 상태 로드 실패"
          description={error instanceof Error ? error.message : '알 수 없는 오류'}
          style={{ marginBottom: 16 }}
        />
      )}

      {isLoading && (
        <div style={{ textAlign: 'center', padding: 80 }}>
          <Spin size="large" />
        </div>
      )}

      {data && (
        <>
          {/* 상단 KPI */}
          <Row gutter={[16, 16]} style={{ marginBottom: 16 }}>
            <Col xs={12} md={6}>
              <Card size="small">
                <Statistic
                  title="활성 플랫폼"
                  value={data.summary.total_active}
                  suffix={`/ ${data.summary.total_active + data.summary.total_inactive}`}
                />
              </Card>
            </Col>
            <Col xs={12} md={6}>
              <Card size="small">
                <Statistic
                  title="24h 총 수집"
                  value={data.summary.total_records_24h}
                  formatter={(v) => formatCount(Number(v))}
                />
              </Card>
            </Col>
            <Col xs={12} md={6}>
              <Card size="small">
                <Statistic
                  title="최근 1h 신규"
                  value={data.summary.total_records_1h}
                  formatter={(v) => formatCount(Number(v))}
                />
              </Card>
            </Col>
            <Col xs={12} md={6}>
              <Card size="small">
                <Space direction="vertical" size={2} style={{ width: '100%' }}>
                  <Text type="secondary" style={{ fontSize: 12 }}>
                    상태 분포
                  </Text>
                  <Space wrap size={4}>
                    {healthCounts &&
                      (Object.keys(healthCounts) as PlatformHealth[]).map((h) => (
                        <Tag key={h} color={HEALTH_BADGE[h].color}>
                          {HEALTH_BADGE[h].label} {healthCounts[h]}
                        </Tag>
                      ))}
                  </Space>
                </Space>
              </Card>
            </Col>
          </Row>

          {/* 지역별 */}
          <Card size="small" title="지역별 수집 (24h)" style={{ marginBottom: 16 }}>
            {regions.length === 0 ? (
              <Empty description="지역 데이터 없음" />
            ) : (
              <Space wrap size={[8, 8]}>
                {regions.map(([code, stat]) => (
                  <Tag
                    key={code}
                    color="geekblue"
                    style={{ fontSize: 13, padding: '4px 10px' }}
                  >
                    {code}: {stat.records_24h.toLocaleString()}건 (활성 {stat.active}/
                    {stat.total})
                  </Tag>
                ))}
              </Space>
            )}
          </Card>

          {/* 플랫폼 테이블 */}
          <Card
            size="small"
            title={`플랫폼 ${data.platforms.length}개`}
            style={{ marginBottom: 16 }}
          >
            <Table<CollectionPlatform>
              rowKey="code"
              size="small"
              columns={columns}
              dataSource={data.platforms}
              pagination={{ pageSize: 25, showSizeChanger: true }}
              scroll={{ x: 1000 }}
            />
          </Card>

          {/* 시간대별 추이 (1h vs 24h 분포) */}
          <Card size="small" title="최근 활동 TOP 12 (1h bar / 24h line)">
            {chartOpt ? (
              <ReactECharts
                option={chartOpt}
                style={{ height: 320 }}
                notMerge
                lazyUpdate
              />
            ) : (
              <Empty description="차트 데이터 없음" />
            )}
          </Card>
        </>
      )}
    </div>
  );
}
