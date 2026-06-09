import { useMemo, useRef } from 'react';
import { Card, Empty, Spin, Typography } from 'antd';
import ReactECharts from 'echarts-for-react';
import type { EChartsOption } from 'echarts';
import { useQuery } from '@tanstack/react-query';
import { fetchEngagementSentiment } from '../../services/deepApi';
import type { EngagementSentimentResponse } from '../../types/deep';
import { makeBaseOption, mergeOption, palette } from '../../utils/chartTheme';
import CardActions from '../common/CardActions';

const { Text } = Typography;

function buildOption(resp: EngagementSentimentResponse): EChartsOption {
  const buckets = resp.buckets.map((b) => `Q${b.bucket}\n${b.eng_range}`);
  const custom: EChartsOption = {
    legend: { data: ['neg_ratio(%)', 'score'], top: 0, textStyle: { fontSize: 11 } },
    grid: { left: 48, right: 56, top: 28, bottom: 40 },
    xAxis: { type: 'category', data: buckets, axisLabel: { fontSize: 9 } },
    yAxis: [
      {
        type: 'value',
        name: 'neg%',
        position: 'left',
        max: 100,
        axisLabel: { formatter: (v: number) => `${v}` },
      },
      {
        type: 'value',
        name: 'score',
        min: -1,
        max: 1,
        position: 'right',
        splitLine: { show: false },
      },
    ],
    series: [
      {
        name: 'neg_ratio(%)',
        type: 'bar',
        data: resp.buckets.map((b) => Number((b.neg_ratio * 100).toFixed(1))),
        itemStyle: { color: palette.negative },
        yAxisIndex: 0,
      },
      {
        name: 'score',
        type: 'line',
        data: resp.buckets.map((b) => Number(b.score.toFixed(3))),
        itemStyle: { color: palette.primary },
        yAxisIndex: 1,
        smooth: true,
      },
    ],
  };
  return mergeOption(makeBaseOption(), custom);
}

export default function EngagementSentimentCard() {
  const chartRef = useRef<unknown>(null);
  const { data, isLoading } = useQuery({
    queryKey: ['deep', 'engagement'],
    queryFn: () => fetchEngagementSentiment({ period_days: 30 }),
    staleTime: 5 * 60_000,
  });
  const opt = useMemo(() => (data ? buildOption(data) : null), [data]);

  return (
    <Card
      title="engagement × sentiment (5분위 버킷)"
      size="small"
      bodyStyle={{ height: 300, padding: 8 }}
      extra={
        <CardActions
          title="engagement×sentiment"
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
      ) : !data.buckets.length ? (
        <Empty description="버킷 없음" />
      ) : (
        <>
          <Text type="secondary" style={{ fontSize: 11 }}>
            상위 quintile 일수록 부정 비율 변화 — 카테고리 corr top{' '}
            {data.by_category[0]
              ? `${data.by_category[0].category} ${data.by_category[0].corr_eng_neg.toFixed(2)}`
              : '-'}
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
