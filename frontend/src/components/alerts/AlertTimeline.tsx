// P4 트랙 D — 지난 7일 발화 timeline (severity stack).
import { useMemo } from 'react';
import ReactECharts from 'echarts-for-react';
import { Empty } from 'antd';
import type { AlertEvent } from '../../services/alertsApi';
import { bucketEventsByHour } from './alertsUtils';

interface Props {
  events: AlertEvent[];
  /** 7일 전 epoch ms 기본. */
  sinceMs?: number;
}

const COLORS = {
  critical: '#cf1322',
  warning: '#fa8c16',
  info: '#1677ff',
};

export default function AlertTimeline({ events, sinceMs }: Props) {
  const since = sinceMs ?? Date.now() - 7 * 24 * 3600 * 1000;
  const buckets = useMemo(
    () => bucketEventsByHour(events, since),
    [events, since],
  );

  if (buckets.length === 0) {
    return <Empty description="지난 7일 발화 없음" image={Empty.PRESENTED_IMAGE_SIMPLE} />;
  }

  const option = {
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
    legend: { data: ['critical', 'warning', 'info'] },
    grid: { left: 40, right: 16, top: 32, bottom: 36 },
    xAxis: {
      type: 'category',
      data: buckets.map((b) => b.hour.slice(5)),  // MM-DD HH:00
      axisLabel: { fontSize: 10, rotate: 30 },
    },
    yAxis: { type: 'value', minInterval: 1 },
    series: [
      {
        name: 'critical', type: 'bar', stack: 'sev',
        data: buckets.map((b) => b.critical),
        itemStyle: { color: COLORS.critical },
      },
      {
        name: 'warning', type: 'bar', stack: 'sev',
        data: buckets.map((b) => b.warning),
        itemStyle: { color: COLORS.warning },
      },
      {
        name: 'info', type: 'bar', stack: 'sev',
        data: buckets.map((b) => b.info),
        itemStyle: { color: COLORS.info },
      },
    ],
  };
  return <ReactECharts option={option} style={{ height: 220 }} notMerge />;
}
