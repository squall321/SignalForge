import { useMemo } from 'react';
import ReactECharts from 'echarts-for-react';
import type { EChartsOption } from 'echarts';
import type {
  Changepoint,
  TemporalSeries,
  TimelineEvent,
} from '../../types/temporal';

interface Props {
  series: TemporalSeries[];
  events: TimelineEvent[];
  changepoints: Changepoint[];
  height?: number;
}

const SERIES_COLORS = ['#1677ff', '#52c41a', '#fa541c', '#722ed1', '#13c2c2', '#eb2f96'];

/**
 * ECharts 옵션 빌더 — 분리해서 단위 테스트가 가능하도록 export.
 *   - count(좌측 y) 라인 + 영역
 *   - sent_avg(우측 y) 라인 (-1 ~ 1)
 *   - timeline_events markLine + 툴팁
 *   - changepoints markPoint annotation
 */
export function buildChartOption(
  series: TemporalSeries[],
  events: TimelineEvent[],
  changepoints: Changepoint[],
): EChartsOption {
  // 모든 series 의 날짜 union → x축 카테고리
  const dateSet = new Set<string>();
  series.forEach((s) => s.data.forEach((p) => dateSet.add(p.date)));
  const dates = Array.from(dateSet).sort();

  const eventMarklines = events.map((e) => ({
    xAxis: e.date,
    label: { formatter: e.title, position: 'insideEndTop' as const, fontSize: 11 },
    lineStyle: { color: '#fa8c16', type: 'dashed' as const, width: 1 },
  }));

  // 변곡점 markPoint (첫 series 기준)
  const cpBySeries = new Map<string, Changepoint[]>();
  changepoints.forEach((cp) => {
    if (!cpBySeries.has(cp.series_key)) cpBySeries.set(cp.series_key, []);
    cpBySeries.get(cp.series_key)!.push(cp);
  });

  const echartsSeries = series.flatMap((s, idx) => {
    const color = SERIES_COLORS[idx % SERIES_COLORS.length];
    const byDate = new Map(s.data.map((p) => [p.date, p]));
    const countData = dates.map((d) => byDate.get(d)?.count ?? null);
    const sentData = dates.map((d) => byDate.get(d)?.sent_avg ?? null);

    const cps = cpBySeries.get(s.key) || [];

    return [
      {
        name: `${s.label} (건수)`,
        type: 'line' as const,
        yAxisIndex: 0,
        data: countData,
        smooth: true,
        showSymbol: false,
        areaStyle: { opacity: 0.15 },
        lineStyle: { color, width: 2 },
        itemStyle: { color },
        markLine: idx === 0
          ? {
              symbol: ['none', 'none'] as ['none', 'none'],
              data: eventMarklines,
              tooltip: { show: true, formatter: (p: any) => p.name || p.data.label?.formatter },
            }
          : undefined,
        markPoint: cps.length
          ? {
              symbol: 'pin',
              symbolSize: 36,
              data: cps.map((cp) => ({
                xAxis: cp.date,
                yAxis: byDate.get(cp.date)?.count ?? 0,
                value: cp.delta > 0 ? `+${cp.delta}` : String(cp.delta),
                itemStyle: { color: cp.delta > 0 ? '#52c41a' : '#fa541c' },
              })),
            }
          : undefined,
      },
      {
        name: `${s.label} (감성)`,
        type: 'line' as const,
        yAxisIndex: 1,
        data: sentData,
        smooth: true,
        showSymbol: false,
        lineStyle: { color, width: 1, type: 'dashed' as const, opacity: 0.7 },
        itemStyle: { color },
      },
    ];
  });

  return {
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'cross' },
    },
    legend: {
      top: 0,
      type: 'scroll',
    },
    grid: { left: 56, right: 56, top: 48, bottom: 64 },
    xAxis: {
      type: 'category',
      data: dates,
      boundaryGap: false,
      axisLabel: { rotate: 0 },
    },
    yAxis: [
      {
        type: 'value',
        name: '건수',
        position: 'left',
        axisLine: { show: true },
      },
      {
        type: 'value',
        name: '감성',
        position: 'right',
        min: -1,
        max: 1,
        axisLine: { show: true },
      },
    ],
    dataZoom: [
      { type: 'inside', start: 0, end: 100 },
      { type: 'slider', height: 24, bottom: 16 },
    ],
    series: echartsSeries as EChartsOption['series'],
  };
}

export default function TemporalChart({
  series,
  events,
  changepoints,
  height = 480,
}: Props) {
  const option = useMemo(
    () => buildChartOption(series, events, changepoints),
    [series, events, changepoints],
  );

  return (
    <ReactECharts
      option={option}
      notMerge
      lazyUpdate
      style={{ height, width: '100%' }}
      opts={{ renderer: 'canvas' }}
    />
  );
}
