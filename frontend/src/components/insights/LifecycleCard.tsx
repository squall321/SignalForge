import { useMemo, useState } from 'react';
import { Card, Empty, Select, Space, Spin, Tag, Typography } from 'antd';
import ReactECharts from 'echarts-for-react';
import type { EChartsOption } from 'echarts';
import { useQuery } from '@tanstack/react-query';
import { fetchProductLifecycle } from '../../services/insightsApi';
import type { ProductLifecycleResponse } from '../../types/insights';
import {
  defaultAxisTooltipFormatter,
  palette,
  seriesColors,
} from '../../utils/chartTheme';

const { Text } = Typography;

const PRODUCTS = [
  'GS25', 'GS25P', 'GS25U',
  'GZF7', 'GZFL7',
  'GW8', 'GB3', 'GR2',
];

export function buildLifecycleOption(resp: ProductLifecycleResponse): EChartsOption {
  const labels = resp.points.map((p) => `D+${p.d_offset}`);
  const counts = resp.points.map((p) => p.count);
  const sents = resp.points.map((p) => p.sent_avg);
  return {
    color: seriesColors,
    tooltip: {
      trigger: 'axis',
      formatter: defaultAxisTooltipFormatter as unknown as (p: unknown) => string,
    },
    grid: { left: 56, right: 56, top: 24, bottom: 28 },
    legend: { data: ['count', 'sent_avg'], top: 0 },
    xAxis: { type: 'category', data: labels },
    yAxis: [
      { type: 'value', name: 'count' },
      { type: 'value', name: 'sent', position: 'right', min: -1, max: 1, splitLine: { show: false } },
    ],
    series: [
      { name: 'count', type: 'bar', data: counts, itemStyle: { color: palette.primary } },
      {
        name: 'sent_avg', type: 'line', yAxisIndex: 1, data: sents, smooth: true,
        itemStyle: { color: palette.accent },
      },
    ],
  };
}

export default function LifecycleCard() {
  const [product, setProduct] = useState('GS25');
  const { data, isLoading } = useQuery({
    queryKey: ['insights', 'lifecycle', product],
    queryFn: () => fetchProductLifecycle(product),
    staleTime: 5 * 60_000,
  });
  const opt = useMemo(() => (data ? buildLifecycleOption(data) : null), [data]);

  return (
    <Card
      title="제품 라이프사이클 (출시 D+0/7/30/90/180)"
      size="small"
      bodyStyle={{ height: 280, padding: 12 }}
      extra={
        <Select
          size="small"
          value={product}
          onChange={setProduct}
          style={{ width: 100 }}
          options={PRODUCTS.map((p) => ({ value: p, label: p }))}
        />
      }
    >
      {isLoading ? (
        <Spin />
      ) : !data || !data.release_date ? (
        <Empty description="출시일 미등록" />
      ) : (
        <>
          <Space size={4} wrap style={{ marginBottom: 6 }}>
            <Text type="secondary" style={{ fontSize: 12 }}>
              출시일 {data.release_date}
            </Text>
            {data.points[0]?.top_categories?.slice(0, 4).map((c) => (
              <Tag key={c} color="geekblue" style={{ fontSize: 10 }}>
                {c}
              </Tag>
            ))}
          </Space>
          {opt && <ReactECharts option={opt} style={{ height: 220 }} />}
        </>
      )}
    </Card>
  );
}
