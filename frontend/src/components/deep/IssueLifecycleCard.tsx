import { useMemo, useRef } from 'react';
import { Card, Empty, Spin, Typography } from 'antd';
import ReactECharts from 'echarts-for-react';
import type { EChartsOption } from 'echarts';
import { useQuery } from '@tanstack/react-query';
import { fetchIssueLifecycle } from '../../services/deepApi';
import type { IssueLifecycleResponse } from '../../types/deep';
import { makeBaseOption, mergeOption, palette } from '../../utils/chartTheme';
import CardActions from '../common/CardActions';

const { Text } = Typography;

function buildOption(resp: IssueLifecycleResponse): EChartsOption {
  const top = resp.items.slice(0, 12);
  const labels = top.map((i) => `${i.keyword}${i.category ? '·' + i.category : ''}`).reverse();
  const lifespans = top.map((i) => i.lifespan).reverse();
  const ttp = top.map((i) => i.days_to_peak).reverse();
  const custom: EChartsOption = {
    legend: { data: ['lifespan', 'days_to_peak'], top: 0, textStyle: { fontSize: 11 } },
    grid: { left: 110, right: 16, top: 28, bottom: 24 },
    xAxis: { type: 'value', name: 'days' },
    yAxis: {
      type: 'category',
      data: labels,
      axisLabel: { fontSize: 10, width: 100, overflow: 'truncate' },
    },
    series: [
      { name: 'lifespan', type: 'bar', data: lifespans, itemStyle: { color: palette.negative } },
      { name: 'days_to_peak', type: 'bar', data: ttp, itemStyle: { color: palette.accent } },
    ],
  };
  return mergeOption(makeBaseOption(), custom);
}

export default function IssueLifecycleCard() {
  const chartRef = useRef<unknown>(null);
  const { data, isLoading } = useQuery({
    queryKey: ['deep', 'lifecycle'],
    queryFn: () => fetchIssueLifecycle({ period_days: 60, top_n: 20 }),
    staleTime: 5 * 60_000,
  });
  const opt = useMemo(() => (data ? buildOption(data) : null), [data]);
  return (
    <Card
      title="이슈 라이프사이클 (부정 이슈 top12)"
      size="small"
      bodyStyle={{ height: 300, padding: 8 }}
      extra={
        <CardActions
          title="이슈 라이프사이클"
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
        <Empty description="해당 윈도우 이슈 없음" />
      ) : (
        <>
          <Text type="secondary" style={{ fontSize: 11 }}>
            평균 lifespan{' '}
            {data.category_avg.length
              ? (
                  data.category_avg.reduce((s, c) => s + c.avg_lifespan, 0) /
                  data.category_avg.length
                ).toFixed(1)
              : '-'}
            일 · 카테고리 {data.category_avg.length}개
          </Text>
          {opt && (
            <ReactECharts
              option={opt}
              style={{ height: 250 }}
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
