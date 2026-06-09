// R10 트랙 B4 — 시리즈 세대×sentiment heatmap.
// 행=세대 (1..N), 열=시리즈 (S/Note/Z), 셀=sent_avg (-1..1).
// 색 스케일: red(-) → gray(0) → blue(+).
import { useMemo } from 'react';
import { Alert, Card, Empty, Spin } from 'antd';
import { useQuery } from '@tanstack/react-query';
import ReactECharts from 'echarts-for-react';
import { fetchSeriesComparison, seriesHeatmapCells } from '../../services/historyApi';
import { makeBaseOption } from '../../utils/chartTheme';

export default function SeriesHeatmap() {
  const { data, isLoading, isError } = useQuery({
    queryKey: ['history-compare-heatmap'],
    queryFn: () => fetchSeriesComparison(['S', 'Note', 'Z']),
    staleTime: 10 * 60_000,
  });

  const opt = useMemo(() => {
    if (!data) return null;
    const { rows, cols, cells } = seriesHeatmapCells(
      data.series_list.map((s) => ({
        label: s.label,
        points: s.points.map((p) => ({ gen: p.gen, sent_avg: p.sent_avg })),
      })),
    );
    if (rows.length === 0 || cols.length === 0) return null;
    const base = makeBaseOption({ withDataZoom: false, withToolbox: true, tooltipMode: 'off' });
    return {
      ...base,
      tooltip: {
        position: 'top',
        formatter: (p: { data?: [number, number, number] }) => {
          const v = p.data?.[2] ?? 0;
          const colName = cols[p.data?.[0] ?? 0] ?? '';
          const rowName = rows[p.data?.[1] ?? 0] ?? '';
          return `${colName} / ${rowName}<br/>sent_avg: <b>${v.toFixed(3)}</b>`;
        },
      },
      grid: { top: 40, right: 16, bottom: 32, left: 70 },
      xAxis: {
        type: 'category',
        data: cols,
        splitArea: { show: true },
        axisLabel: { fontSize: 11 },
      },
      yAxis: {
        type: 'category',
        data: rows,
        splitArea: { show: true },
        axisLabel: { fontSize: 11 },
      },
      visualMap: {
        min: -1,
        max: 1,
        calculable: true,
        orient: 'horizontal',
        left: 'center',
        top: 4,
        inRange: { color: ['#D55E00', '#EEEEEE', '#0072B2'] },
      },
      series: [
        {
          name: 'sent_avg',
          type: 'heatmap',
          data: cells,
          label: {
            show: true,
            fontSize: 10,
            formatter: (p: { value?: [number, number, number] }) => {
              const v = p.value?.[2] ?? 0;
              return v === 0 ? '' : v.toFixed(2);
            },
          },
        },
      ],
    };
  }, [data]);

  return (
    <Card
      data-testid="series-heatmap-card"
      size="small"
      title="시리즈 세대 sentiment heatmap"
    >
      {isError && <Alert type="error" message="heatmap 로드 실패" />}
      {isLoading && <Spin />}
      {!isLoading && !opt && <Empty description="데이터 없음" />}
      {opt && <ReactECharts option={opt} style={{ height: 320 }} notMerge lazyUpdate />}
    </Card>
  );
}
