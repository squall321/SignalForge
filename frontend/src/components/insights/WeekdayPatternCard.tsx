import { useMemo } from 'react';
import { Card, Empty, Spin, Typography } from 'antd';
import ReactECharts from 'echarts-for-react';
import type { EChartsOption } from 'echarts';
import { useQuery } from '@tanstack/react-query';
import { fetchWeekdayPattern } from '../../services/insightsApi';
import type { WeekdayPatternResponse } from '../../types/insights';
import { useViewport } from '../../utils/useViewport';
import {
  defaultAxisTooltipFormatter,
  palette,
  seriesColors,
} from '../../utils/chartTheme';

const { Text } = Typography;

export function buildWeekdayOption(
  resp: WeekdayPatternResponse,
  opts: { mobile?: boolean } = {},
): EChartsOption {
  const mobile = !!opts.mobile;
  const labels = resp.points.map((p) => p.label);
  const counts = resp.points.map((p) => p.count);
  const negs = resp.points.map((p) => p.neg_rate);
  return {
    color: seriesColors,
    tooltip: {
      trigger: 'axis',
      formatter: defaultAxisTooltipFormatter as unknown as (p: unknown) => string,
    },
    legend: mobile ? { show: false } : { data: ['count', 'neg_rate(%)'], top: 0 },
    grid: mobile
      ? { left: 36, right: 36, top: 12, bottom: 24 }
      : { left: 56, right: 56, top: 24, bottom: 28 },
    xAxis: { type: 'category', data: labels, axisLabel: { fontSize: mobile ? 10 : 12 } },
    yAxis: [
      { type: 'value', name: 'count', axisLabel: { fontSize: mobile ? 10 : 12 } },
      {
        type: 'value', name: 'neg(%)', position: 'right', max: 100, splitLine: { show: false },
        axisLabel: { fontSize: mobile ? 10 : 12 },
      },
    ],
    series: [
      { name: 'count', type: 'bar', data: counts, itemStyle: { color: palette.primary } },
      {
        name: 'neg_rate(%)', type: 'line', yAxisIndex: 1, data: negs, smooth: true,
        itemStyle: { color: palette.negative },
      },
    ],
  };
}

export default function WeekdayPatternCard() {
  const vp = useViewport();
  const { data, isLoading } = useQuery({
    queryKey: ['insights', 'weekday'],
    queryFn: () => fetchWeekdayPattern({ period_days: 30 }),
    staleTime: 5 * 60_000,
  });

  const opt = useMemo(
    () => (data ? buildWeekdayOption(data, { mobile: vp.isMobile }) : null),
    [data, vp.isMobile],
  );

  const bodyH = vp.isMobile ? 240 : 280;
  const chartH = vp.isMobile ? 200 : 240;

  return (
    <Card
      title="요일 패턴 (Mon~Sun, 최근 30일)"
      size="small"
      bodyStyle={{ height: bodyH, padding: vp.isMobile ? 8 : 12 }}
    >
      {isLoading ? (
        <Spin />
      ) : !opt || !data || data.points.every((p) => p.count === 0) ? (
        <Empty description="데이터 없음" />
      ) : (
        <>
          <Text type="secondary" style={{ fontSize: vp.isMobile ? 11 : 12 }}>
            총 {((data.meta?.total as number) || 0).toLocaleString()}건
          </Text>
          <ReactECharts option={opt} style={{ height: chartH }} />
        </>
      )}
    </Card>
  );
}
