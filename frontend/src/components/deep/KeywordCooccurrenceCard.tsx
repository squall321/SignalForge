import { useMemo, useRef } from 'react';
import { Card, Empty, Spin, Typography } from 'antd';
import ReactECharts from 'echarts-for-react';
import type { EChartsOption } from 'echarts';
import { useQuery } from '@tanstack/react-query';
import { fetchKeywordCooccurrence } from '../../services/deepApi';
import type { KeywordCooccurrenceResponse } from '../../types/deep';
import { makeBaseOption, mergeOption, palette } from '../../utils/chartTheme';
import CardActions from '../common/CardActions';

const { Text } = Typography;

function buildGraph(resp: KeywordCooccurrenceResponse): EChartsOption {
  const maxDeg = Math.max(1, ...resp.nodes.map((n) => n.degree));
  const nodes = resp.nodes.slice(0, 50).map((n) => ({
    id: n.id,
    name: n.id,
    symbolSize: 8 + (n.degree / maxDeg) * 18,
    // 색맹 친화: positive / negative / primary 팔레트로 매핑 (빨↔녹 직접 대비 회피)
    itemStyle: {
      color:
        n.sentiment_bias >= 0.1
          ? palette.positive
          : n.sentiment_bias <= -0.1
            ? palette.negative
            : palette.primary,
    },
    value: n.degree,
  }));
  const nodeIds = new Set(nodes.map((n) => n.id));
  const links = resp.edges
    .filter((e) => nodeIds.has(e.from) && nodeIds.has(e.to))
    .slice(0, 100)
    .map((e) => ({ source: e.from, target: e.to, value: e.weight }));
  const custom: EChartsOption = {
    tooltip: {
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      formatter: (p: any) => {
        const d = p?.data ?? {};
        if (p?.dataType === 'node') {
          return `${d.name ?? ''}<br/>degree ${d.value ?? 0}`;
        }
        return `${d.source ?? ''} ↔ ${d.target ?? ''}<br/>weight ${d.value ?? 0}`;
      },
    },
    series: [
      {
        type: 'graph',
        layout: 'force',
        roam: true,
        force: { repulsion: 80, edgeLength: [40, 80] },
        data: nodes,
        links,
        label: { show: true, fontSize: 9, position: 'right' },
        lineStyle: { color: 'source', opacity: 0.4, curveness: 0.2 },
      },
    ],
  };
  return mergeOption(makeBaseOption({ tooltipMode: 'off' }), custom);
}

export default function KeywordCooccurrenceCard() {
  const chartRef = useRef<unknown>(null);
  const { data, isLoading } = useQuery({
    queryKey: ['deep', 'cooccur'],
    queryFn: () => fetchKeywordCooccurrence({ period_days: 30, min_edge_weight: 5, top_nodes: 60 }),
    staleTime: 5 * 60_000,
  });
  const opt = useMemo(() => (data ? buildGraph(data) : null), [data]);

  return (
    <Card
      title="키워드 공출현 네트워크"
      size="small"
      bodyStyle={{ height: 320, padding: 8 }}
      extra={
        <CardActions
          title="키워드 공출현"
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
      ) : !data.nodes.length ? (
        <Empty description="네트워크 없음" />
      ) : (
        <>
          <Text type="secondary" style={{ fontSize: 11 }}>
            nodes {data.nodes.length} · edges {data.edges.length} · top pair{' '}
            {data.top_pairs[0]
              ? `${data.top_pairs[0].k1}↔${data.top_pairs[0].k2} w=${data.top_pairs[0].weight}`
              : '-'}
          </Text>
          {opt && (
            <ReactECharts
              option={opt}
              style={{ height: 260 }}
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
