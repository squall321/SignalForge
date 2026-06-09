import { useMemo } from 'react';
import { Alert, Card, Empty, Spin } from 'antd';
import ReactECharts from 'echarts-for-react';
import type { EChartsOption } from 'echarts';
import { useQuery } from '@tanstack/react-query';
import { fetchClusters } from '../../services/communityApi';
import type { ClusterResponse } from '../../types/community';

// 클러스터별 색상 팔레트
const CLUSTER_COLORS = [
  '#1677ff', '#52c41a', '#fa8c16', '#722ed1',
  '#eb2f96', '#13c2c2', '#a0d911', '#fa541c',
];

/**
 * Scatter option builder — 테스트용 export.
 * X/Y = 임베딩 좌표, 크기 = 7d 게시량, 색 = 클러스터 ID.
 */
export function buildClusterOption(resp: ClusterResponse): EChartsOption {
  const maxPosts = Math.max(1, ...resp.points.map((p) => p.posts_7d));

  // 클러스터별로 series 분리 → 범례에서 토글 가능
  const seriesMap = new Map<number, Array<[number, number, number, string, number]>>();
  for (const p of resp.points) {
    const arr = seriesMap.get(p.cluster) || [];
    // [x, y, size, code, sent]
    arr.push([p.x, p.y, 8 + (p.posts_7d / maxPosts) * 32, p.platform_code, p.sent_avg_7d]);
    seriesMap.set(p.cluster, arr);
  }

  const series = Array.from(seriesMap.entries())
    .sort(([a], [b]) => a - b)
    .map(([cid, points]) => ({
      type: 'scatter' as const,
      name: `Cluster ${cid}`,
      data: points,
      symbolSize: (d: any) => d[2],
      itemStyle: { color: CLUSTER_COLORS[cid % CLUSTER_COLORS.length], opacity: 0.85 },
      label: {
        show: true,
        formatter: (p: any) => p.data[3] as string,
        fontSize: 10,
        position: 'right' as const,
      },
      emphasis: { focus: 'series' as const },
    }));

  return {
    tooltip: {
      trigger: 'item',
      formatter: (p: any) => {
        const [x, y, , code, sent] = p.data as [number, number, number, string, number];
        return [
          `<b>${code}</b>`,
          `cluster: ${p.seriesName}`,
          `xy: (${x.toFixed(2)}, ${y.toFixed(2)})`,
          `7d sent: ${sent.toFixed(2)}`,
        ].join('<br/>');
      },
    },
    legend: { bottom: 0 },
    grid: { left: 48, right: 48, top: 24, bottom: 48 },
    xAxis: { type: 'value', name: 'x', scale: true, splitLine: { show: true } },
    yAxis: { type: 'value', name: 'y', scale: true, splitLine: { show: true } },
    series,
  };
}

export default function ClusterScatter() {
  const { data, isLoading, isError, error } = useQuery({
    queryKey: ['platform-clusters'],
    queryFn: fetchClusters,
    staleTime: 60_000,
  });

  const option = useMemo(() => (data ? buildClusterOption(data) : null), [data]);

  return (
    <Card title="플랫폼 클러스터" bodyStyle={{ padding: 12 }}>
      {isLoading && (
        <div style={{ textAlign: 'center', padding: 48 }}>
          <Spin />
        </div>
      )}
      {isError && (
        <Alert
          type="error"
          showIcon
          message="클러스터 조회 실패"
          description={error instanceof Error ? error.message : '알 수 없는 오류'}
        />
      )}
      {data && data.points.length === 0 && !isLoading && (
        <Empty description="클러스터 데이터가 없습니다" />
      )}
      {option && data && data.points.length > 0 && !isLoading && (
        <ReactECharts option={option} style={{ height: 500 }} notMerge lazyUpdate />
      )}
    </Card>
  );
}
