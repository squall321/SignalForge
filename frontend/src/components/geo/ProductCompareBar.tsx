import { useMemo } from 'react';
import { Alert, Card, Empty, Input, Space, Spin, Typography } from 'antd';
import ReactECharts from 'echarts-for-react';
import type { EChartsOption } from 'echarts';
import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { fetchProductCompare } from '../../services/geoApi';
import { useFilterStore } from '../../stores/useFilterStore';
import type { ProductCompareItem } from '../../types/geo';

const { Text } = Typography;

interface Props {
  defaultProduct?: string;
  height?: number;
}

/**
 * 옵션 빌더 — 테스트 용도로 분리.
 * 국가별 sent_avg 바 차트 + 95% CI 에러바.
 */
export function buildOption(items: ProductCompareItem[]): EChartsOption {
  const sorted = [...items].sort((a, b) => b.sent_avg - a.sent_avg);
  const labels = sorted.map((i) => i.country_name || i.country_code);
  const bars = sorted.map((i) => ({
    value: i.sent_avg,
    itemStyle: {
      color: i.sent_avg > 0.1 ? '#52c41a' : i.sent_avg < -0.1 ? '#fa541c' : '#1677ff',
    },
  }));
  const errorData = sorted.map((i, idx) => [
    idx,
    i.sent_ci_low ?? i.sent_avg,
    i.sent_ci_high ?? i.sent_avg,
  ]);

  return {
    grid: { left: 80, right: 24, top: 24, bottom: 32 },
    tooltip: {
      trigger: 'axis',
      formatter: (params: any) => {
        const p = Array.isArray(params) ? params[0] : params;
        const item = sorted[p.dataIndex];
        if (!item) return '';
        return [
          `<b>${item.country_name || item.country_code}</b>`,
          `평균 감성: ${item.sent_avg.toFixed(2)}`,
          item.sent_ci_low != null && item.sent_ci_high != null
            ? `95% CI: [${item.sent_ci_low.toFixed(2)}, ${item.sent_ci_high.toFixed(2)}]`
            : '',
          `건수: ${item.count.toLocaleString()}`,
        ]
          .filter(Boolean)
          .join('<br/>');
      },
    },
    xAxis: { type: 'value', min: -1, max: 1, name: '감성' },
    yAxis: { type: 'category', data: labels, axisLabel: { fontSize: 11 } },
    series: [
      {
        name: '평균 감성',
        type: 'bar',
        data: bars,
        barWidth: '60%',
      },
      {
        name: '95% CI',
        type: 'custom',
        renderItem: (_p: any, api: any) => {
          const y = api.coord([0, api.value(0)])[1];
          const xLow = api.coord([api.value(1), api.value(0)])[0];
          const xHigh = api.coord([api.value(2), api.value(0)])[0];
          const cap = 4;
          return {
            type: 'group',
            children: [
              {
                type: 'line',
                shape: { x1: xLow, y1: y, x2: xHigh, y2: y },
                style: { stroke: '#333', lineWidth: 1.2 },
              },
              {
                type: 'line',
                shape: { x1: xLow, y1: y - cap, x2: xLow, y2: y + cap },
                style: { stroke: '#333', lineWidth: 1.2 },
              },
              {
                type: 'line',
                shape: { x1: xHigh, y1: y - cap, x2: xHigh, y2: y + cap },
                style: { stroke: '#333', lineWidth: 1.2 },
              },
            ],
          };
        },
        encode: { x: [1, 2], y: 0 },
        data: errorData,
        z: 100,
      },
    ],
  };
}

export default function ProductCompareBar({ defaultProduct = 'GS25', height = 400 }: Props) {
  const [productCode, setProductCode] = useState(defaultProduct);
  const { dateRange } = useFilterStore();

  const { data, isLoading, isError, error } = useQuery({
    queryKey: ['product-compare', productCode, dateRange.start, dateRange.end],
    queryFn: () =>
      fetchProductCompare(productCode, {
        start: dateRange.start,
        end: dateRange.end,
      }),
    staleTime: 60_000,
  });

  const option = useMemo(() => buildOption(data?.items ?? []), [data]);

  return (
    <Card
      title="국가별 제품 비교"
      extra={
        <Space>
          <Text type="secondary">제품:</Text>
          <Input
            size="small"
            value={productCode}
            onChange={(e) => setProductCode(e.target.value.toUpperCase())}
            style={{ width: 120 }}
          />
        </Space>
      }
      bodyStyle={{ padding: 12 }}
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
          message="제품 비교 조회 실패"
          description={error instanceof Error ? error.message : '알 수 없는 오류'}
        />
      )}
      {data && data.items.length === 0 && !isLoading && (
        <Empty description="데이터가 없습니다" />
      )}
      {data && data.items.length > 0 && !isLoading && (
        <ReactECharts option={option} style={{ height }} notMerge lazyUpdate />
      )}
    </Card>
  );
}
