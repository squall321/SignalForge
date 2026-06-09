import { useMemo, useRef } from 'react';
import { Card, Empty, Spin, Typography } from 'antd';
import ReactECharts from 'echarts-for-react';
import type { EChartsOption } from 'echarts';
import { useQuery } from '@tanstack/react-query';
import { fetchSiteDiffusion } from '../../services/deepApi';
import type { SiteDiffusionResponse } from '../../types/deep';
import { makeBaseOption, mergeOption } from '../../utils/chartTheme';
import CardActions from '../common/CardActions';

const { Text } = Typography;

function buildSankey(resp: SiteDiffusionResponse): EChartsOption {
  const nodes = Array.from(
    new Set(resp.edges.flatMap((e) => [e.from_site, e.to_site])),
  ).map((n) => ({ name: n }));
  const links = resp.edges.slice(0, 30).map((e) => ({
    source: e.from_site,
    target: e.to_site,
    value: e.count,
  }));
  const custom: EChartsOption = {
    tooltip: {
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      formatter: (p: any) => {
        const d = p?.data ?? {};
        if (p?.dataType === 'edge' && d.source) {
          const edge = resp.edges.find(
            (e) => e.from_site === d.source && e.to_site === d.target,
          );
          return `${d.source} → ${d.target}<br/>count ${d.value}<br/>avg lag ${edge?.avg_lag ?? '-'}d`;
        }
        return '';
      },
    },
    series: [
      {
        type: 'sankey',
        data: nodes,
        links,
        emphasis: { focus: 'adjacency' },
        lineStyle: { color: 'gradient', curveness: 0.5 },
        label: { fontSize: 10 },
      },
    ],
  };
  // sankey — base 의 color 회전만 위임. tooltip 은 카드 custom 이 우선.
  return mergeOption(makeBaseOption({ tooltipMode: 'off' }), custom);
}

export default function SiteDiffusionCard() {
  const chartRef = useRef<unknown>(null);
  const { data, isLoading } = useQuery({
    queryKey: ['deep', 'diffusion'],
    queryFn: () => fetchSiteDiffusion({ period_days: 45, min_sites: 2, top_keywords: 30 }),
    staleTime: 5 * 60_000,
  });
  const opt = useMemo(() => (data ? buildSankey(data) : null), [data]);

  return (
    <Card
      title="사이트 간 이슈 확산 (origin → terminal)"
      size="small"
      bodyStyle={{ height: 320, padding: 8 }}
      extra={
        <CardActions
          title="사이트 간 이슈 확산"
          echartsRef={chartRef.current as never}
          json={data}
          renderExpanded={() =>
            opt ? <ReactECharts option={opt} style={{ height: 560 }} /> : <div>데이터 없음</div>
          }
        />
      }
    >
      {isLoading || !data ? (
        <Spin />
      ) : !data.edges.length ? (
        <Empty description="확산 edge 없음" />
      ) : (
        <>
          <Text type="secondary" style={{ fontSize: 11 }}>
            키워드 {data.keywords.length}개 · edge {data.edges.length}개
          </Text>
          {opt && (
            <ReactECharts
              option={opt}
              style={{ height: 270 }}
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
