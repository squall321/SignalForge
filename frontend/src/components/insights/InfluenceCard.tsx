import { useMemo } from 'react';
import { Card, Empty, Spin, Typography } from 'antd';
import ReactECharts from 'echarts-for-react';
import type { EChartsOption } from 'echarts';
import { useQuery } from '@tanstack/react-query';
import { fetchPlatformInfluence } from '../../services/insightsApi';
import type { PlatformInfluenceResponse } from '../../types/insights';
import { palette } from '../../utils/chartTheme';

const { Text } = Typography;

function buildScatterOption(resp: PlatformInfluenceResponse): EChartsOption {
  const data = resp.items.map((it) => ({
    name: it.platform,
    value: [it.drivers.engagement, it.drivers.neg_rate, it.score, it.n, it.region ?? ''],
  }));
  return {
    tooltip: {
      trigger: 'item',
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      formatter: (p: any) => {
        const v = (p?.data?.value ?? [0, 0, 0, 0, '']) as [number, number, number, number, string];
        const [eng, neg, score, n, region] = v;
        return `${p?.data?.name ?? ''}${region ? ' (' + region + ')' : ''}<br/>engagement ${eng.toFixed(1)} · neg ${neg.toFixed(1)}%<br/>score <b>${score.toFixed(1)}</b> · n ${n}`;
      },
    },
    grid: { left: 56, right: 24, top: 16, bottom: 40 },
    xAxis: { type: 'value', name: 'engagement', nameLocation: 'middle', nameGap: 24 },
    yAxis: { type: 'value', name: 'neg(%)', max: 100 },
    series: [
      {
        type: 'scatter',
        data,
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        symbolSize: (v: any) => {
          const s = Number((Array.isArray(v) ? v[2] : v?.value?.[2]) ?? 0);
          return Math.max(8, Math.min(36, s * 0.45));
        },
        itemStyle: {
          // eslint-disable-next-line @typescript-eslint/no-explicit-any
          color: (p: any) => {
            const score = Number(p?.data?.value?.[2] ?? 0);
            if (score >= 70) return palette.negative;
            if (score >= 40) return palette.accent;
            return palette.primary;
          },
          opacity: 0.75,
        },
        label: {
          show: true,
          position: 'right',
          fontSize: 10,
          // eslint-disable-next-line @typescript-eslint/no-explicit-any
          formatter: (p: any) => String(p?.data?.name ?? ''),
        },
      },
    ],
  };
}

export default function InfluenceCard() {
  const { data, isLoading } = useQuery({
    queryKey: ['insights', 'influence'],
    queryFn: () => fetchPlatformInfluence({ period_days: 30 }),
    staleTime: 5 * 60_000,
  });
  const opt = useMemo(() => (data ? buildScatterOption(data) : null), [data]);

  return (
    <Card
      title="사이트 영향력 scatter (x=engagement, y=neg%, 크기=score)"
      size="small"
      bodyStyle={{ height: 320, padding: 8 }}
    >
      {isLoading ? (
        <Spin />
      ) : !data || !data.items.length ? (
        <Empty description="데이터 없음" />
      ) : (
        <>
          <Text type="secondary" style={{ fontSize: 11 }}>
            상위 사이트 {data.items.length}개 · 색=score 구간(파/주/빨)
          </Text>
          {opt && <ReactECharts option={opt} style={{ height: 270 }} />}
        </>
      )}
    </Card>
  );
}
