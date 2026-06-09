import { useMemo, useRef, useState } from 'react';
import { Card, Empty, Spin, Typography } from 'antd';
import ReactECharts from 'echarts-for-react';
import type { EChartsOption } from 'echarts';
import { useQuery } from '@tanstack/react-query';
import { fetchKeywordNetwork } from '../../services/deepApi';
import type { KeywordNetworkResponse } from '../../types/deep';
import { useFilterStore } from '../../stores/useFilterStore';
import { effectivePeriodDays } from '../../types/filters';
import CardActions from '../common/CardActions';
import { makeBaseOption, mergeOption, seriesColors } from '../../utils/chartTheme';
import KeywordDetailDrawer from './KeywordDetailDrawer';

const { Text } = Typography;

// 커뮤니티별 색상 — chartTheme.seriesColors (Okabe-Ito 색맹 친화 팔레트) 를 그대로 회전.
const COLORS = seriesColors;

function buildOption(resp: KeywordNetworkResponse): EChartsOption {
  // 너무 많으면 force layout 성능 저하 — node 60 / edge 200 cap
  const nodes = resp.nodes.slice(0, 60);
  const nodeIds = new Set(nodes.map((n) => n.id));
  const edges = resp.edges
    .filter((e) => nodeIds.has(e.source) && nodeIds.has(e.target))
    .slice(0, 200);
  const maxFreq = Math.max(...nodes.map((n) => n.freq), 1);
  const custom: EChartsOption = {
    tooltip: {
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      formatter: ((p: any) => {
        if (p?.dataType === 'edge') {
          const e = p.data as { source: string; target: string; weight?: number };
          return `${e.source} - ${e.target}<br/>w=${e.weight ?? '-'}`;
        }
        const n = (p?.data ?? {}) as { keyword?: string; freq?: number; community_id?: number };
        return `<b>${n.keyword ?? ''}</b><br/>freq=${n.freq ?? '-'} · comm=${n.community_id ?? '-'}`;
      }) as never,
    },
    legend: { show: false },
    series: [
      {
        type: 'graph',
        layout: 'force',
        roam: true,
        force: { repulsion: 90, edgeLength: 35, gravity: 0.1 },
        label: { show: true, fontSize: 9, position: 'right' },
        emphasis: { focus: 'adjacency' },
        data: nodes.map((n) => ({
          id: n.id,
          name: n.keyword,
          keyword: n.keyword,
          lang: n.lang,
          freq: n.freq,
          community_id: n.community_id,
          value: n.freq,
          symbolSize: 6 + (n.freq / maxFreq) * 18,
          itemStyle: { color: COLORS[n.community_id % COLORS.length] },
        })),
        links: edges.map((e) => ({
          source: e.source,
          target: e.target,
          weight: e.weight,
          lineStyle: { width: Math.min(0.5 + e.weight / 30, 3) },
        })),
      },
    ],
  };
  return mergeOption(makeBaseOption({ tooltipMode: 'off' }), custom);
}

export default function KeywordNetworkCard() {
  const filters = useFilterStore();
  const period = effectivePeriodDays(filters);
  const chartRef = useRef<unknown>(null);

  const [selectedKeyword, setSelectedKeyword] = useState<string | null>(null);
  const [selectedLang, setSelectedLang] = useState<string | null>(null);
  const [drawerOpen, setDrawerOpen] = useState<boolean>(false);

  const { data, isLoading } = useQuery({
    queryKey: ['deep', 'keyword-network', period],
    queryFn: () => fetchKeywordNetwork({ period_days: period, min_cooccur: 10, max_nodes: 80 }),
    staleTime: 5 * 60_000,
  });
  const opt = useMemo(() => (data ? buildOption(data) : null), [data]);
  const m = (data?.meta as { total_communities?: number }) ?? {};

  const onChartEvents = useMemo(
    () => ({
      click: (params: {
        dataType?: string;
        data?: { id?: string; keyword?: string; lang?: string | null };
      }) => {
        if (params?.dataType !== 'node') return;
        const kw = params.data?.keyword || params.data?.id;
        if (!kw) return;
        setSelectedKeyword(kw);
        setSelectedLang(params.data?.lang ?? null);
        setDrawerOpen(true);
      },
    }),
    [],
  );

  return (
    <Card
      title={`키워드 네트워크 (force-directed, ${period}일)`}
      size="small"
      bodyStyle={{ height: 300, padding: 8 }}
      extra={
        <CardActions
          title="키워드 네트워크"
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
        <Empty description="해당 윈도우 데이터 없음" />
      ) : (
        <>
          <Text type="secondary" style={{ fontSize: 11 }}>
            노드 {data.nodes.length} · 엣지 {data.edges.length} · 커뮤니티 {m.total_communities ?? '-'} · 노드 클릭 → 상세
          </Text>
          {opt && (
            <ReactECharts
              option={opt}
              style={{ height: 250 }}
              onEvents={onChartEvents}
              ref={(r) => {
                chartRef.current = r;
              }}
            />
          )}
        </>
      )}
      <KeywordDetailDrawer
        keyword={selectedKeyword}
        lang={selectedLang}
        open={drawerOpen}
        onClose={() => setDrawerOpen(false)}
        onSelectKeyword={(kw, lng) => {
          setSelectedKeyword(kw);
          setSelectedLang(lng ?? null);
        }}
      />
    </Card>
  );
}
