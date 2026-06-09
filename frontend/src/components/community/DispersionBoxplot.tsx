import { useMemo } from 'react';
import { Alert, Card, Empty, Spin } from 'antd';
import ReactECharts from 'echarts-for-react';
import type { EChartsOption } from 'echarts';
import { useQuery } from '@tanstack/react-query';
import { fetchDispersion } from '../../services/communityApi';
import type { DispersionResponse } from '../../types/community';

/**
 * Boxplot option builder — 테스트용 export.
 * 각 플랫폼의 감성 분포 5수치 + 개별 outlier 점.
 */
export function buildBoxplotOption(resp: DispersionResponse): EChartsOption {
  // sent 분포가 큰 순으로 정렬 (max - min 폭 기준)
  const sorted = [...resp.entries].sort(
    (a, b) => b.max - b.min - (a.max - a.min),
  );
  const categories = sorted.map((e) => e.platform_code);
  const boxData = sorted.map((e) => [e.min, e.q1, e.median, e.q3, e.max]);
  const outlierData: Array<[number, number]> = [];
  sorted.forEach((e, idx) => {
    (e.outliers || []).forEach((o) => outlierData.push([idx, o]));
  });

  return {
    tooltip: {
      trigger: 'item',
      formatter: (p: any) => {
        if (p.seriesType === 'boxplot') {
          const [min, q1, med, q3, max] = p.data.slice(1) as number[];
          const e = sorted[p.dataIndex];
          return [
            `<b>${e.platform_code}</b> (n=${e.n})`,
            `max: ${max.toFixed(2)}`,
            `Q3: ${q3.toFixed(2)}`,
            `median: ${med.toFixed(2)}`,
            `Q1: ${q1.toFixed(2)}`,
            `min: ${min.toFixed(2)}`,
          ].join('<br/>');
        }
        return `outlier: ${(p.data as [number, number])[1].toFixed(2)}`;
      },
    },
    grid: { left: 80, right: 24, top: 24, bottom: 60 },
    xAxis: {
      type: 'category',
      data: categories,
      axisLabel: { rotate: 45, fontSize: 11 },
    },
    yAxis: {
      type: 'value',
      name: '감성',
      min: -1,
      max: 1,
      splitLine: { show: true },
    },
    series: [
      {
        name: '감성 분포',
        type: 'boxplot',
        data: boxData,
        itemStyle: { color: '#bae0ff', borderColor: '#1677ff' },
      },
      {
        name: '이상치',
        type: 'scatter',
        data: outlierData,
        symbolSize: 6,
        itemStyle: { color: '#fa541c' },
      },
    ],
  };
}

export default function DispersionBoxplot() {
  const { data, isLoading, isError, error } = useQuery({
    queryKey: ['platform-dispersion'],
    queryFn: fetchDispersion,
    staleTime: 60_000,
  });

  const option = useMemo(() => (data ? buildBoxplotOption(data) : null), [data]);

  return (
    <Card title="플랫폼별 감성 분산 (Boxplot)" bodyStyle={{ padding: 12 }}>
      {isLoading && (
        <div style={{ textAlign: 'center', padding: 48 }}>
          <Spin />
        </div>
      )}
      {isError && (
        <Alert
          type="error"
          showIcon
          message="분산 조회 실패"
          description={error instanceof Error ? error.message : '알 수 없는 오류'}
        />
      )}
      {data && data.entries.length === 0 && !isLoading && (
        <Empty description="분산 데이터가 없습니다" />
      )}
      {option && data && data.entries.length > 0 && !isLoading && (
        <ReactECharts option={option} style={{ height: 460 }} notMerge lazyUpdate />
      )}
    </Card>
  );
}
