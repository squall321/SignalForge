import { useMemo } from 'react';
import { Card, Empty, Spin, Typography } from 'antd';
import ReactECharts from 'echarts-for-react';
import type { EChartsOption } from 'echarts';
import { useQuery } from '@tanstack/react-query';
import { fetchSentimentSwing } from '../../services/insightsApi';
import type { SentimentSwingResponse } from '../../types/insights';
import { palette } from '../../utils/chartTheme';

const { Text } = Typography;

function buildSwingOption(resp: SentimentSwingResponse): EChartsOption {
  const items = [...resp.items].sort((a, b) => a.delta_pp - b.delta_pp).slice(0, 12);
  const products = items.map((i) => i.product);
  const before = items.map((i) => i.before_sent);
  const after = items.map((i) => i.after_sent);
  return {
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'shadow' },
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      formatter: (p: any) => {
        const arr = Array.isArray(p) ? p : [p];
        const it = items.find((x) => x.product === arr[0]?.axisValue);
        if (!it) return '';
        const sign = it.delta_pp >= 0 ? '+' : '';
        return `${it.product}<br/>before ${it.before_sent.toFixed(3)} → after ${it.after_sent.toFixed(3)}<br/>Δ ${sign}${it.delta_pp.toFixed(3)}<br/>n ${it.n_before}/${it.n_after}`;
      },
    },
    legend: { data: ['before', 'after'], top: 0, textStyle: { fontSize: 11 } },
    grid: { left: 60, right: 16, top: 28, bottom: 56 },
    xAxis: {
      type: 'category',
      data: products,
      axisLabel: { fontSize: 10, rotate: 35 },
    },
    yAxis: { type: 'value', min: -1, max: 1, name: 'sent' },
    series: [
      {
        name: 'before',
        type: 'scatter',
        data: before,
        itemStyle: { color: palette.neutral },
        symbolSize: 10,
      },
      {
        name: 'after',
        type: 'scatter',
        data: after,
        itemStyle: { color: palette.negative },
        symbolSize: 10,
      },
      {
        // arrow lines (markLine 으로 before→after 잇기)
        type: 'line',
        data: [],
        markLine: {
          symbol: ['none', 'arrow'],
          lineStyle: { type: 'solid', width: 1, opacity: 0.6 },
          label: { show: false },
          data: items.map((it, idx) => [
            { coord: [idx, it.before_sent], itemStyle: { color: palette.neutral } },
            {
              coord: [idx, it.after_sent],
              itemStyle: { color: it.delta_pp >= 0 ? palette.positive : palette.negative },
            },
          ]),
        },
      },
    ],
  };
}

export default function SentimentSwingCard() {
  const { data, isLoading } = useQuery({
    queryKey: ['insights', 'swing'],
    queryFn: () => fetchSentimentSwing({ period_days: 14, min_volume: 50 }),
    staleTime: 5 * 60_000,
  });
  const opt = useMemo(() => (data ? buildSwingOption(data) : null), [data]);

  return (
    <Card
      title="감성 변동 큰 제품 (14d vs 28d, before→after)"
      size="small"
      bodyStyle={{ height: 280, padding: 8 }}
    >
      {isLoading || !data ? (
        <Spin />
      ) : !data.items.length ? (
        <Empty description="변동 없음" />
      ) : (
        <>
          <Text type="secondary" style={{ fontSize: 11 }}>
            회색=before, 주황=after / 화살표 상승(청록) · 하락(주황)
          </Text>
          {opt && <ReactECharts option={opt} style={{ height: 230 }} />}
        </>
      )}
    </Card>
  );
}
