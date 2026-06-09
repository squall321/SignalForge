import { useMemo } from 'react';
import { Alert, Card, Empty, Segmented, Space, Spin, Typography } from 'antd';
import { useState } from 'react';
import ReactECharts from 'echarts-for-react';
import type { EChartsOption } from 'echarts';
import { useQuery } from '@tanstack/react-query';
import { fetchProductMatrix } from '../../services/communityApi';
import type { ProductMatrixResponse } from '../../types/community';

const { Text } = Typography;

type Metric = 'count' | 'sent_avg';

/**
 * heatmap option builder — 테스트 가능하도록 export.
 * X = 사이트(platforms), Y = 제품(products).
 */
export function buildMatrixOption(
  resp: ProductMatrixResponse,
  metric: Metric,
): EChartsOption {
  const { platforms, products, cells } = resp;
  const cellMap = new Map<string, { count: number; sent_avg: number }>();
  for (const c of cells) {
    cellMap.set(`${c.platform_code}::${c.product_code}`, {
      count: c.count,
      sent_avg: c.sent_avg,
    });
  }

  const data: Array<[number, number, number]> = [];
  let vmin = Infinity;
  let vmax = -Infinity;
  for (let xi = 0; xi < platforms.length; xi += 1) {
    for (let yi = 0; yi < products.length; yi += 1) {
      const cell = cellMap.get(`${platforms[xi]}::${products[yi]}`);
      const v = cell ? (metric === 'count' ? cell.count : cell.sent_avg) : 0;
      data.push([xi, yi, v]);
      if (v < vmin) vmin = v;
      if (v > vmax) vmax = v;
    }
  }
  if (!Number.isFinite(vmin)) vmin = 0;
  if (!Number.isFinite(vmax)) vmax = 1;

  const isDiverging = metric === 'sent_avg';
  const absMax = Math.max(Math.abs(vmin), Math.abs(vmax), 0.01);

  return {
    tooltip: {
      position: 'top',
      formatter: (p: any) => {
        const [xi, yi, v] = p.data as [number, number, number];
        return `<b>${platforms[xi]} × ${products[yi]}</b><br/>${
          metric === 'count' ? '건수: ' + v.toLocaleString() : '평균 감성: ' + v.toFixed(2)
        }`;
      },
    },
    grid: { left: 96, right: 24, top: 24, bottom: 80 },
    xAxis: {
      type: 'category',
      data: platforms,
      splitArea: { show: true },
      axisLabel: { rotate: 45, fontSize: 11 },
    },
    yAxis: {
      type: 'category',
      data: products,
      splitArea: { show: true },
    },
    visualMap: isDiverging
      ? {
          min: -absMax,
          max: absMax,
          calculable: true,
          orient: 'horizontal',
          left: 'center',
          bottom: 0,
          inRange: { color: ['#cf1322', '#f0f0f0', '#3f8600'] },
        }
      : {
          min: 0,
          max: vmax || 1,
          calculable: true,
          orient: 'horizontal',
          left: 'center',
          bottom: 0,
          inRange: { color: ['#f0f5ff', '#1677ff', '#0958d9'] },
        },
    series: [
      {
        type: 'heatmap',
        data,
        emphasis: { itemStyle: { borderColor: '#000', borderWidth: 1 } },
      },
    ],
  };
}

export default function PlatformMatrix() {
  const [metric, setMetric] = useState<Metric>('count');
  const { data, isLoading, isError, error } = useQuery({
    queryKey: ['platform-product-matrix'],
    queryFn: fetchProductMatrix,
    staleTime: 60_000,
  });

  const option = useMemo(
    () => (data ? buildMatrixOption(data, metric) : null),
    [data, metric],
  );

  return (
    <Card
      title="제품 × 사이트 매트릭스"
      extra={
        <Space>
          <Text type="secondary">색상:</Text>
          <Segmented
            value={metric}
            onChange={(v) => setMetric(v as Metric)}
            options={[
              { label: '건수', value: 'count' },
              { label: '평균 감성', value: 'sent_avg' },
            ]}
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
          message="매트릭스 조회 실패"
          description={error instanceof Error ? error.message : '알 수 없는 오류'}
        />
      )}
      {data && data.cells.length === 0 && !isLoading && (
        <Empty description="매트릭스 데이터가 없습니다" />
      )}
      {option && data && data.cells.length > 0 && !isLoading && (
        <ReactECharts option={option} style={{ height: 480 }} notMerge lazyUpdate />
      )}
    </Card>
  );
}
