import { useMemo, useRef } from 'react';
import { Card, Empty, Spin, Typography } from 'antd';
import ReactECharts from 'echarts-for-react';
import type { EChartsOption } from 'echarts';
import { useQuery } from '@tanstack/react-query';
import { fetchNewTermSurvival } from '../../services/deepApi';
import type { NewTermSurvivalResponse } from '../../types/deep';
import { makeBaseOption, mergeOption, palette } from '../../utils/chartTheme';
import CardActions from '../common/CardActions';

const { Text } = Typography;

// sustained/mid/flash 분류색 — 색맹 친화 팔레트 사용 (positive/accent/neutral).
const COLOR_MAP: Record<string, string> = {
  sustained: palette.positive,
  mid: palette.accent,
  flash: palette.neutral,
};

function buildOption(resp: NewTermSurvivalResponse): EChartsOption {
  const data = resp.items.slice(0, 80).map((it) => ({
    name: it.keyword,
    value: [it.active_days, it.survival_days, it.total],
    itemStyle: { color: COLOR_MAP[it.cls] || palette.neutral },
  }));
  const custom: EChartsOption = {
    tooltip: {
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      formatter: (p: any) => {
        const v = (p?.data?.value ?? [0, 0, 0]) as [number, number, number];
        return `${p?.data?.name ?? ''}<br/>active ${v[0]}d · survival ${v[1]}d · n ${v[2]}`;
      },
    },
    grid: { left: 48, right: 16, top: 16, bottom: 36 },
    xAxis: { type: 'value', name: 'active_days' },
    yAxis: { type: 'value', name: 'survival_days' },
    series: [
      {
        type: 'scatter',
        data,
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        symbolSize: (v: any) => {
          const n = Array.isArray(v) ? Number(v[2] ?? 0) : Number(v?.value?.[2] ?? 0);
          return Math.max(6, Math.min(22, Math.log2(n + 1) * 4));
        },
      },
    ],
  };
  return mergeOption(makeBaseOption({ tooltipMode: 'off' }), custom);
}

export default function NewTermSurvivalCard() {
  const chartRef = useRef<unknown>(null);
  const { data, isLoading } = useQuery({
    queryKey: ['deep', 'survival'],
    queryFn: () => fetchNewTermSurvival({ period_days: 60, lookback_window: 14, min_mentions: 5 }),
    staleTime: 5 * 60_000,
  });
  const opt = useMemo(() => (data ? buildOption(data) : null), [data]);

  return (
    <Card
      title="신규 키워드 버즈 수명 (active × survival)"
      size="small"
      bodyStyle={{ height: 300, padding: 8 }}
      extra={
        <CardActions
          title="신규 키워드 버즈 수명"
          echartsRef={chartRef.current as never}
          json={data}
          renderExpanded={() =>
            opt ? <ReactECharts option={opt} style={{ height: 520 }} /> : <div>데이터 없음</div>
          }
        />
      }
    >
      {isLoading || !data ? (
        <Spin />
      ) : !data.items.length ? (
        <Empty description="신규어 없음" />
      ) : (
        <>
          <Text type="secondary" style={{ fontSize: 11 }}>
            sustained {data.summary.sustained} · mid {data.summary.mid} · flash {data.summary.flash}{' '}
            · avg survival {data.summary.avg_survival.toFixed(1)}d
          </Text>
          {opt && (
            <ReactECharts
              option={opt}
              style={{ height: 240 }}
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
