import { useMemo, useState, useEffect } from 'react';
import {
  Drawer,
  Statistic,
  Row,
  Col,
  Table,
  Tag,
  Empty,
  Skeleton,
  Typography,
  List,
  Pagination,
  Button,
  Space,
} from 'antd';
import ReactECharts from 'echarts-for-react';
import type { EChartsOption } from 'echarts';
import { useQuery } from '@tanstack/react-query';
import { fetchAnomalyDrilldown, fetchAnomalyDrilldownHour } from '../../services/deepApi';
import type {
  AnomalyDrilldownHourResponse,
  AnomalyDrilldownResponse,
  DrilldownHourVocItem,
  DrilldownKeyword,
  DrilldownPlatform,
  DrilldownProduct,
} from '../../types/deep';
import { buildHourlyBars } from './anomalyDrilldownUtils';

const { Text, Link: AntLink } = Typography;

interface Props {
  open: boolean;
  date: string | null;
  onClose: () => void;
}

const PAGE_SIZE = 20;

/**
 * P4.1 트랙 B + P4.2 E3 — anomaly day Drawer.
 *  - hourly bar 클릭 시 해당 1시간 VoC 본문 리스트(neg 우선) 페치 + 페이지네이션.
 */
export default function AnomalyDrilldownDrawer({ open, date, onClose }: Props) {
  const { data, isLoading } = useQuery<AnomalyDrilldownResponse>({
    queryKey: ['deep', 'anomaly-drilldown', date],
    queryFn: () => fetchAnomalyDrilldown({ date: date as string, z_threshold: 2.0, top_k: 10 }),
    enabled: open && !!date,
    staleTime: 5 * 60_000,
  });

  const [selectedHour, setSelectedHour] = useState<number | null>(null);
  const [hourPage, setHourPage] = useState<number>(1);

  // Drawer 닫히거나 date 바뀌면 selection reset.
  useEffect(() => {
    setSelectedHour(null);
    setHourPage(1);
  }, [date, open]);

  const { data: hourData, isLoading: hourLoading } = useQuery<AnomalyDrilldownHourResponse>({
    queryKey: ['deep', 'anomaly-drilldown-hour', date, selectedHour, hourPage],
    queryFn: () =>
      fetchAnomalyDrilldownHour({
        date: date as string,
        hour: selectedHour as number,
        limit: PAGE_SIZE,
        offset: (hourPage - 1) * PAGE_SIZE,
      }),
    enabled: open && !!date && selectedHour !== null,
    staleTime: 60_000,
  });

  const hourlyOption: EChartsOption | null = useMemo(() => {
    if (!data?.hourly?.length) return null;
    const { categories, values } = buildHourlyBars(data.hourly);
    return {
      tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
      grid: { left: 36, right: 12, top: 16, bottom: 28 },
      xAxis: { type: 'category', data: categories, axisLabel: { fontSize: 10 } },
      yAxis: { type: 'value', axisLabel: { fontSize: 10 } },
      series: [{ type: 'bar', data: values, barCategoryGap: '20%' }],
    };
  }, [data]);

  const onHourlyChartEvents = useMemo(
    () => ({
      click: (params: { dataIndex?: number }) => {
        if (typeof params.dataIndex !== 'number' || !data?.hourly) return;
        const hour = data.hourly[params.dataIndex]?.hour;
        if (typeof hour === 'number') {
          setSelectedHour(hour);
          setHourPage(1);
        }
      },
    }),
    [data],
  );

  const productCols = [
    { title: 'code', dataIndex: 'code', key: 'code', width: 90 },
    { title: '제품', dataIndex: 'name_ko', key: 'name_ko', ellipsis: true },
    { title: 'n', dataIndex: 'count', key: 'count', width: 60 },
    {
      title: 'neg',
      dataIndex: 'neg_rate',
      key: 'neg_rate',
      width: 70,
      render: (v: number) => `${(v * 100).toFixed(1)}%`,
    },
  ];

  const keywordCols = [
    { title: 'keyword', dataIndex: 'keyword', key: 'keyword', width: 120 },
    {
      title: 'Δ%',
      dataIndex: 'delta_pct',
      key: 'delta_pct',
      width: 70,
      render: (v: number) => (
        <Text style={{ color: v >= 0 ? '#cf1322' : '#1677ff', fontSize: 11 }}>{v.toFixed(1)}</Text>
      ),
    },
    {
      title: '관련 제품',
      dataIndex: 'related_products',
      key: 'related_products',
      render: (codes: string[]) =>
        (codes || []).map((c) => (
          <Tag key={c} color="geekblue" style={{ fontSize: 10 }}>
            {c}
          </Tag>
        )),
    },
  ];

  const platformCols = [
    { title: 'code', dataIndex: 'code', key: 'code', width: 90 },
    { title: '플랫폼', dataIndex: 'name', key: 'name', ellipsis: true },
    { title: 'n', dataIndex: 'count', key: 'count', width: 60 },
  ];

  const empty = !isLoading && data && !data.hourly.length && !data.products.length && !data.keywords.length;

  return (
    <Drawer
      title={date ? `Anomaly drill-down · ${date}` : 'Anomaly drill-down'}
      placement="right"
      open={open}
      onClose={onClose}
      width={typeof window !== 'undefined' && window.innerWidth < 768 ? '100%' : 560}
      destroyOnClose
    >
      {isLoading || !data ? (
        <Skeleton active paragraph={{ rows: 8 }} />
      ) : empty ? (
        <Empty description="해당 날짜에 데이터 없음" />
      ) : (
        <>
          <Row gutter={8} style={{ marginBottom: 12 }}>
            <Col span={8}>
              <Statistic title="z" value={data.anomaly_summary.z} precision={2} />
            </Col>
            <Col span={8}>
              <Statistic title="value" value={data.anomaly_summary.value} precision={0} />
            </Col>
            <Col span={8}>
              <Statistic title="baseline" value={data.anomaly_summary.baseline} precision={1} />
            </Col>
          </Row>

          <Text strong style={{ fontSize: 12 }}>시간대 분포 (peak 강조 · bar 클릭 → 1h VoC)</Text>
          {hourlyOption ? (
            <ReactECharts
              option={hourlyOption}
              style={{ height: 160, marginBottom: 12 }}
              onEvents={onHourlyChartEvents}
            />
          ) : (
            <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="hourly 없음" />
          )}

          {selectedHour !== null && (
            <div data-testid="hour-voc-section" style={{ marginBottom: 16 }}>
              <Space style={{ marginBottom: 6 }}>
                <Text strong style={{ fontSize: 12 }}>
                  {`${selectedHour}h VoC (${hourData?.total ?? 0})`}
                </Text>
                <Button
                  size="small"
                  type="link"
                  onClick={() => {
                    setSelectedHour(null);
                    setHourPage(1);
                  }}
                >
                  닫기
                </Button>
              </Space>
              {hourLoading || !hourData ? (
                <Skeleton active paragraph={{ rows: 3 }} />
              ) : hourData.items.length === 0 ? (
                <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="해당 시간 VoC 없음" />
              ) : (
                <>
                  <List<DrilldownHourVocItem>
                    size="small"
                    dataSource={hourData.items}
                    renderItem={(it) => {
                      const sev = it.sentiment_label;
                      const color =
                        sev === 'negative' ? 'red' : sev === 'positive' ? 'blue' : 'default';
                      return (
                        <List.Item style={{ display: 'block', padding: '6px 0' }}>
                          <Space size={4} wrap>
                            <Tag color={color} style={{ fontSize: 10 }}>
                              {sev ?? 'neutral'}
                            </Tag>
                            {it.product?.code && (
                              <Tag color="geekblue" style={{ fontSize: 10 }}>
                                {it.product.name_ko || it.product.code}
                              </Tag>
                            )}
                            {it.platform?.code && (
                              <Tag color="purple" style={{ fontSize: 10 }}>
                                {it.platform.name || it.platform.code}
                              </Tag>
                            )}
                            <Text type="secondary" style={{ fontSize: 10 }}>
                              eng {it.engagement_score?.toFixed(2) ?? '-'}
                            </Text>
                          </Space>
                          <div style={{ marginTop: 4, fontSize: 12, lineHeight: 1.4 }}>
                            {it.url ? (
                              <AntLink href={it.url} target="_blank" rel="noopener noreferrer">
                                {it.content_preview}
                              </AntLink>
                            ) : (
                              <Text>{it.content_preview}</Text>
                            )}
                          </div>
                        </List.Item>
                      );
                    }}
                  />
                  <Pagination
                    size="small"
                    style={{ marginTop: 8, textAlign: 'right' }}
                    current={hourPage}
                    pageSize={PAGE_SIZE}
                    total={hourData.total}
                    showSizeChanger={false}
                    onChange={(p) => setHourPage(p)}
                  />
                </>
              )}
            </div>
          )}

          <Text strong style={{ fontSize: 12 }}>Top 제품 (top 5)</Text>
          <Table<DrilldownProduct>
            size="small"
            rowKey="code"
            dataSource={data.products.slice(0, 5)}
            columns={productCols}
            pagination={false}
            style={{ marginBottom: 12 }}
          />

          <Text strong style={{ fontSize: 12 }}>Top 키워드 (top 10)</Text>
          <Table<DrilldownKeyword>
            size="small"
            rowKey="keyword"
            dataSource={data.keywords.slice(0, 10)}
            columns={keywordCols}
            pagination={false}
            style={{ marginBottom: 12 }}
          />

          <Text strong style={{ fontSize: 12 }}>Top 플랫폼 (top 5)</Text>
          <Table<DrilldownPlatform>
            size="small"
            rowKey="code"
            dataSource={data.platforms.slice(0, 5)}
            columns={platformCols}
            pagination={false}
          />
        </>
      )}
    </Drawer>
  );
}
