import { useMemo, useRef } from 'react';
import { Card, Empty, Spin, Typography } from 'antd';
import ReactECharts from 'echarts-for-react';
import type { EChartsOption } from 'echarts';
import { useQuery } from '@tanstack/react-query';
import { fetchNewTerms } from '../../services/insightsApi';
import type { NewTermsResponse } from '../../types/insights';
import { makeBaseOption, mergeOption, palette } from '../../utils/chartTheme';
import CardActions from '../common/CardActions';

const { Text } = Typography;

function buildTimelineOption(resp: NewTermsResponse): EChartsOption {
  // 가로 = first_seen 일자, 세로 = 빈도 (size = count_recent)
  const top = [...resp.items].sort((a, b) => b.count_recent - a.count_recent).slice(0, 30);
  const series = top.map((it) => ({
    name: it.keyword,
    value: [it.first_seen, it.count_recent],
    symbolSize: Math.min(8 + it.count_recent * 1.5, 26),
  }));
  const custom: EChartsOption = {
    tooltip: {
      trigger: 'item',
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      formatter: (p: any) => {
        const d = p.data ?? {};
        const v = (d.value ?? ['', 0]) as [string, number];
        return `${d.name ?? ''}<br/>첫 등장 ${v[0]}<br/>최근 ${v[1]}건`;
      },
    },
    grid: { left: 48, right: 16, top: 16, bottom: 36 },
    xAxis: { type: 'time', axisLabel: { fontSize: 10 } },
    yAxis: { type: 'value', name: 'count', axisLabel: { fontSize: 10 } },
    series: [
      {
        type: 'scatter',
        data: series,
        itemStyle: { color: palette.secondary, opacity: 0.7 },
        label: {
          show: true,
          position: 'top',
          fontSize: 9,
          // eslint-disable-next-line @typescript-eslint/no-explicit-any
          formatter: (p: any) => String(p?.data?.name ?? ''),
        },
      },
    ],
  };
  return mergeOption(makeBaseOption({ tooltipMode: 'off' }), custom);
}

export default function NewTermsCard() {
  const chartRef = useRef<unknown>(null);
  const { data, isLoading } = useQuery({
    queryKey: ['insights', 'new-terms'],
    queryFn: () => fetchNewTerms({ period_days: 30 }),
    staleTime: 5 * 60_000,
  });
  const opt = useMemo(() => (data ? buildTimelineOption(data) : null), [data]);

  return (
    <Card
      title="신조어 timeline (최근 30일 첫 등장)"
      size="small"
      bodyStyle={{ height: 280, padding: 8 }}
      extra={
        <CardActions
          title="신조어 timeline"
          echartsRef={chartRef.current as never}
          json={data}
          renderExpanded={() =>
            opt ? <ReactECharts option={opt} style={{ height: 480 }} /> : <div>데이터 없음</div>
          }
        />
      }
    >
      {isLoading || !data ? (
        <Spin />
      ) : !data.items.length ? (
        <Empty description="신규 키워드 없음" />
      ) : (
        <>
          <Text type="secondary" style={{ fontSize: 11 }}>
            총 {data.items.length}개 / size = 최근 mention 수
          </Text>
          {opt && (
            <ReactECharts
              option={opt}
              style={{ height: 230 }}
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
