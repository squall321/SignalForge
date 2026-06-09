import { useMemo, useRef } from 'react';
import { Card, Empty, Spin, Typography } from 'antd';
import ReactECharts from 'echarts-for-react';
import type { EChartsOption } from 'echarts';
import { useQuery } from '@tanstack/react-query';
import { fetchInfluenceRank } from '../../services/deepApi';
import type { InfluenceRankResponse } from '../../types/deep';
import { makeBaseOption, mergeOption, palette } from '../../utils/chartTheme';
import CardActions from '../common/CardActions';

const { Text } = Typography;

function buildOption(resp: InfluenceRankResponse): EChartsOption {
  const top = resp.items.slice(0, 12);
  const labels = top.map((i) => i.platform).reverse();
  const scores = top.map((i) => i.score).reverse();
  const custom: EChartsOption = {
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'shadow' },
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      formatter: ((params: any) => {
        const arr = Array.isArray(params) ? params : [params];
        if (!arr.length) return '';
        const idx = top.length - 1 - (arr[0].dataIndex ?? 0);
        const it = top[idx];
        if (!it) return '';
        const d = it.drivers;
        return (
          `<b>${it.platform}</b> · ${it.region ?? '-'}<br/>`
          + `score=${it.score.toFixed(3)}<br/>`
          + `eng=${d.engagement.toFixed(2)} · neg=${(d.neg_rate * 100).toFixed(1)}%<br/>`
          + `lead=${d.lead_days.toFixed(1)}d · reach=${d.reach.toFixed(2)}`
        );
      }) as never,
    },
    grid: { left: 110, right: 24, top: 12, bottom: 24 },
    xAxis: { type: 'value', name: 'score', max: 1 },
    yAxis: {
      type: 'category',
      data: labels,
      axisLabel: { fontSize: 10, width: 100, overflow: 'truncate' },
    },
    series: [
      {
        name: 'score',
        type: 'bar',
        data: scores,
        itemStyle: { color: palette.primary },
        label: {
          show: true,
          position: 'right',
          fontSize: 10,
          // eslint-disable-next-line @typescript-eslint/no-explicit-any
          formatter: ((v: any) => Number(v.value ?? 0).toFixed(2)) as never,
        },
      },
    ],
  };
  return mergeOption(makeBaseOption({ tooltipMode: 'off' }), custom);
}

export default function InfluenceRankCard() {
  const chartRef = useRef<unknown>(null);
  const { data, isLoading } = useQuery({
    queryKey: ['deep', 'influence-rank'],
    queryFn: () => fetchInfluenceRank({ period_days: 30, top_n: 30 }),
    staleTime: 5 * 60_000,
  });
  const opt = useMemo(() => (data ? buildOption(data) : null), [data]);
  return (
    <Card
      title="사이트 영향력 순위 (engagement × neg × lead × reach)"
      size="small"
      bodyStyle={{ height: 320, padding: 8 }}
      extra={
        <CardActions
          title="사이트 영향력 순위"
          echartsRef={chartRef.current as never}
          json={data}
          renderExpanded={() =>
            opt ? <ReactECharts option={opt} style={{ height: 560 }} /> : <div>데이터 없음</div>
          }
        />
      }
    >
      {isLoading || !data ? (
        <Spin />
      ) : !data.items.length ? (
        <Empty description="해당 윈도우 데이터 없음" />
      ) : (
        <>
          <Text type="secondary" style={{ fontSize: 11 }}>
            top {Math.min(data.items.length, 12)} of {data.items.length} · 가중 0.3/0.25/0.25/0.2
          </Text>
          {opt && (
            <ReactECharts
              option={opt}
              style={{ height: 270 }}
              ref={(r) => {
                chartRef.current = r;
              }}
            />
          )}
        </>
      )}
    </Card>
  );
}
