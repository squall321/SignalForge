import { useMemo, useState } from 'react';
import { Card, Empty, Select, Space, Spin, Tag, Typography } from 'antd';
import ReactECharts from 'echarts-for-react';
import type { EChartsOption } from 'echarts';
import { useQuery } from '@tanstack/react-query';
import { fetchProductLifecycle } from '../../services/insightsApi';
import { useProductOptions } from '../../hooks/useFilterMeta';
import type { ProductLifecycleResponse } from '../../types/insights';
import {
  defaultAxisTooltipFormatter,
  palette,
  seriesColors,
} from '../../utils/chartTheme';

const { Text } = Typography;

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
  // 전체 제품(활성) 동적 로드 — 하드코딩 목록 대신 어떤 기종이든 선택 가능.
  const { data: productMeta } = useProductOptions();
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
          showSearch
          optionFilterProp="label"
          value={product}
          onChange={setProduct}
          style={{ width: 180 }}
          options={(productMeta ?? []).map((p) => ({ value: p.code, label: `${p.name} (${p.code})` }))}
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
