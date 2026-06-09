import { useMemo } from 'react';
import { Card, Empty, Spin, Typography } from 'antd';
import ReactECharts from 'echarts-for-react';
import type { EChartsOption } from 'echarts';
import { useQuery } from '@tanstack/react-query';
import { fetchCountrySentimentGap } from '../../services/deepApi';
import type { CountrySentimentGapResponse } from '../../types/deep';
import { seriesColors } from '../../utils/chartTheme';

const { Text } = Typography;

function buildOption(resp: CountrySentimentGapResponse): EChartsOption {
  // 상위 6개 제품만 grouped bar
  const products = Array.from(new Set(resp.items.map((i) => i.product))).slice(0, 6);
  const countries = Array.from(new Set(resp.items.map((i) => i.country))).slice(0, 8);
  const series = countries.map((c) => ({
    name: c,
    type: 'bar' as const,
    data: products.map((p) => {
      const found = resp.items.find((i) => i.product === p && i.country === c);
      return found ? found.score : null;
    }),
  }));
  return {
    color: seriesColors,
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
    legend: { top: 0, textStyle: { fontSize: 10 } },
    grid: { left: 56, right: 16, top: 32, bottom: 28 },
    xAxis: { type: 'category', data: products, axisLabel: { fontSize: 10, rotate: 25 } },
    yAxis: { type: 'value', min: -1, max: 1, name: 'score' },
    series,
  };
}

export default function CountrySentimentGapCard() {
  const { data, isLoading } = useQuery({
    queryKey: ['deep', 'country-gap'],
    queryFn: () => fetchCountrySentimentGap({ period_days: 30, top_products: 10, min_n: 20 }),
    staleTime: 5 * 60_000,
  });
  const opt = useMemo(() => (data ? buildOption(data) : null), [data]);

  return (
    <Card
      title="국가별 sentiment 갭 (제품×국가 score)"
      size="small"
      bodyStyle={{ height: 320, padding: 8 }}
    >
      {isLoading || !data ? (
        <Spin />
      ) : !data.items.length ? (
        <Empty description="셀 없음" />
      ) : (
        <>
          <Text type="secondary" style={{ fontSize: 11 }}>
            top gap{' '}
            {data.top_gaps[0]
              ? `${data.top_gaps[0].product} ${data.top_gaps[0].country_high}↔${data.top_gaps[0].country_low} Δ${data.top_gaps[0].gap.toFixed(2)}`
              : '-'}
          </Text>
          {opt && <ReactECharts option={opt} style={{ height: 260 }} />}
        </>
      )}
    </Card>
  );
}
