import { useMemo, useRef } from 'react';
import { Card, Empty, Spin, Tooltip, Typography } from 'antd';
import ReactECharts from 'echarts-for-react';
import type { EChartsOption } from 'echarts';
import { useQuery } from '@tanstack/react-query';
import { fetchLifecycleFunnel } from '../../services/deepApi';
import type { LifecycleFunnelResponse } from '../../types/deep';
import { makeBaseOption, mergeOption } from '../../utils/chartTheme';
import CardActions from '../common/CardActions';

const { Text } = Typography;

function buildOption(resp: LifecycleFunnelResponse): EChartsOption {
  const data = resp.stages.map((s) => ({ name: s.stage, value: s.n_keywords }));
  const custom: EChartsOption = {
    tooltip: { trigger: 'item', formatter: '{b}: {c}' },
    series: [
      {
        type: 'funnel',
        left: 16,
        top: 16,
        bottom: 16,
        width: '80%',
        sort: 'none',
        gap: 2,
        label: { show: true, fontSize: 11, formatter: '{b}\n{c}개' },
        itemStyle: { borderColor: '#fff', borderWidth: 1 },
        data,
      },
    ],
  };
  // funnel 차트는 legend / grid 가 필요 없어 base 의 color 회전만 위임.
  return mergeOption(makeBaseOption({ tooltipMode: 'off' }), custom);
}

export default function LifecycleFunnelCard() {
  const chartRef = useRef<unknown>(null);
  const { data, isLoading } = useQuery({
    queryKey: ['deep', 'lifecycle-funnel'],
    queryFn: () => fetchLifecycleFunnel({ period_days: 90 }),
    staleTime: 5 * 60_000,
  });
  const opt = useMemo(() => (data ? buildOption(data) : null), [data]);
  return (
    <Card
      title="신규 키워드 라이프사이클 깔때기"
      size="small"
      bodyStyle={{ height: 300, padding: 8 }}
      extra={
        <CardActions
          title="라이프사이클 깔때기"
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
      ) : !data.stages.length ? (
        <Empty description="해당 윈도우 데이터 없음" />
      ) : (
        <>
          <Text type="secondary" style={{ fontSize: 11 }}>
            총 신규 키워드 {data.stages.reduce((s, x) => s + x.n_keywords, 0)}개 ·{' '}
            {data.stages.map((s) => (
              <Tooltip
                key={s.stage}
                title={
                  s.examples.length
                    ? s.examples
                        .map((e) => `${e.keyword} (${e.days_alive}일/peak ${e.peak_count})`)
                        .join(', ')
                    : '예시 없음'
                }
              >
                <span style={{ marginRight: 6 }}>
                  {s.stage} {s.n_keywords}
                </span>
              </Tooltip>
            ))}
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
