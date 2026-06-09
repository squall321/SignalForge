// R10 트랙 B1 — Galaxy 17년 통합 timeline 카드.
// 시리즈별 누적 voc 를 연도 축에 stack 라인으로 표시한다.
// /history 페이지 최상단에서 시리즈 전반의 라이프사이클 비교를 한 화면에서 보여 준다.
import { useMemo } from 'react';
import { Alert, Card, Empty, Spin } from 'antd';
import { useQueries } from '@tanstack/react-query';
import ReactECharts from 'echarts-for-react';
import {
  MASTER_SERIES_SPECS,
  fetchGalaxyTimeline,
  masterTimelineSeries,
} from '../../services/historyApi';
import { makeBaseOption } from '../../utils/chartTheme';

export default function GalaxyMasterTimeline() {
  // 5 시리즈를 병렬 fetch. 각 응답을 masterTimelineSeries 로 누적.
  const results = useQueries({
    queries: MASTER_SERIES_SPECS.map((spec) => ({
      queryKey: ['history-timeline', spec.key],
      queryFn: () => fetchGalaxyTimeline(spec.key),
      staleTime: 5 * 60_000,
    })),
  });

  const isLoading = results.some((r) => r.isLoading);
  const isError = results.some((r) => r.isError);

  const option = useMemo(() => {
    if (results.some((r) => !r.data)) return null;
    const inputs = MASTER_SERIES_SPECS.map((spec, i) => ({
      key: spec.key,
      label: spec.label,
      color: spec.color,
      models: results[i].data?.models ?? [],
    }));
    const { years, seriesData } = masterTimelineSeries(inputs, 2010, 2026);
    const base = makeBaseOption({ withDataZoom: true, withToolbox: true });
    return {
      ...base,
      legend: { data: seriesData.map((s) => s.name), top: 4 },
      xAxis: {
        type: 'category',
        data: years.map((y) => String(y)),
        axisLabel: { fontSize: 10 },
      },
      yAxis: { type: 'value', name: '누적 voc' },
      series: seriesData.map((s) => ({
        name: s.name,
        type: 'line',
        smooth: true,
        showSymbol: false,
        data: s.values,
        itemStyle: { color: s.color },
        lineStyle: { color: s.color, width: 2 },
      })),
    };
  }, [results]);

  return (
    <Card
      data-testid="galaxy-master-timeline-card"
      size="small"
      title="Galaxy 17년 통합 timeline (2010~2026)"
    >
      {isError && <Alert type="error" message="master timeline 로드 실패" />}
      {isLoading && <Spin />}
      {!isLoading && !option && <Empty description="데이터 없음" />}
      {option && (
        <ReactECharts option={option} style={{ height: 320 }} notMerge lazyUpdate />
      )}
    </Card>
  );
}
