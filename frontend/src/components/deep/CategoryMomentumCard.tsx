import { useMemo } from 'react';
import { Card, Empty, Spin, Typography } from 'antd';
import ReactECharts from 'echarts-for-react';
import type { EChartsOption } from 'echarts';
import { useQuery } from '@tanstack/react-query';
import { fetchCategoryMomentum } from '../../services/deepApi';
import type { CategoryMomentumResponse } from '../../types/deep';
import {
  defaultAxisTooltipFormatter,
  seriesColors,
} from '../../utils/chartTheme';

const { Text } = Typography;

function buildOption(resp: CategoryMomentumResponse): EChartsOption {
  // 모멘텀 절댓값 상위 6 카테고리만 (가독성)
  const cats = [...resp.categories]
    .sort((a, b) => Math.abs(b.momentum_slope) - Math.abs(a.momentum_slope))
    .slice(0, 6);
  const weeks = Array.from(
    new Set(cats.flatMap((c) => c.series.map((p) => p.week))),
  ).sort();
  const series = cats.map((c) => {
    const m = new Map(c.series.map((p) => [p.week, p.share_pct]));
    return {
      name: `${c.name_ko ?? c.code}`,
      type: 'line' as const,
      smooth: true,
      data: weeks.map((w) => m.get(w) ?? null),
    };
  });
  return {
    color: seriesColors,
    tooltip: {
      trigger: 'axis',
      formatter: defaultAxisTooltipFormatter as unknown as (p: unknown) => string,
    },
    legend: { top: 0, textStyle: { fontSize: 10 }, type: 'scroll' },
    grid: { left: 36, right: 16, top: 28, bottom: 24 },
    xAxis: { type: 'category', data: weeks, axisLabel: { fontSize: 9 } },
    yAxis: { type: 'value', name: 'share %', nameTextStyle: { fontSize: 10 } },
    series,
  };
}

export default function CategoryMomentumCard() {
  const { data, isLoading } = useQuery({
    queryKey: ['deep', 'category-momentum'],
    queryFn: () => fetchCategoryMomentum({ period_days: 60, bucket: 'week' }),
    staleTime: 5 * 60_000,
  });
  const opt = useMemo(() => (data ? buildOption(data) : null), [data]);
  const top = data?.categories[0];
  return (
    <Card
      title="카테고리 모멘텀 (주별 비중 추이)"
      size="small"
      bodyStyle={{ height: 300, padding: 8 }}
    >
      {isLoading || !data ? (
        <Spin />
      ) : !data.categories.length ? (
        <Empty description="해당 윈도우 데이터 없음" />
      ) : (
        <>
          <Text type="secondary" style={{ fontSize: 11 }}>
            최상위 slope: <b>{top?.name_ko ?? top?.code}</b>{' '}
            ({top && top.momentum_slope >= 0 ? '+' : ''}{top?.momentum_slope.toFixed(2)}) · 카테고리{' '}
            {data.categories.length}개
          </Text>
          {opt && <ReactECharts option={opt} style={{ height: 250 }} />}
        </>
      )}
    </Card>
  );
}
