import { lazy, Suspense, useMemo, useState } from 'react';
import { Button, Card, Col, Empty, Row, Spin, Tag, Typography } from 'antd';
import ReactECharts from 'echarts-for-react';
import type { EChartsOption } from 'echarts';
import { useQuery } from '@tanstack/react-query';
import { fetchAnomalyWithDrivers } from '../../services/deepApi';
import type { AnomalyWithDriversResponse } from '../../types/deep';
import { driverColor, selectEntry, sortByDate } from './anomalyDriverUtils';
import { palette } from '../../utils/chartTheme';

// 별도 청크 분리: drawer 가 처음 열릴 때만 로드.
const AnomalyDrilldownDrawer = lazy(() => import('./AnomalyDrilldownDrawer'));

const { Text } = Typography;

/**
 * P3.7 트랙 B — 부정 급등 원인 키워드 결합 카드.
 *  좌: anomaly timeline (value/baseline line + z 강조 scatter)
 *  우: 선택된 anomaly 의 top 5 driver 막대 (sentiment 부호로 색상)
 */
export default function AnomalyDriverCard() {
  const { data, isLoading } = useQuery<AnomalyWithDriversResponse>({
    queryKey: ['deep', 'anomaly-with-drivers'],
    queryFn: () => fetchAnomalyWithDrivers({ period_days: 14, z_threshold: 2.0 }),
    staleTime: 5 * 60_000,
  });

  const [selectedDate, setSelectedDate] = useState<string | null>(null);
  const [drilldownDate, setDrilldownDate] = useState<string | null>(null);
  const sorted = useMemo(() => (data ? sortByDate(data.anomalies) : []), [data]);
  const selected = useMemo(() => selectEntry(sorted, selectedDate), [sorted, selectedDate]);

  const timelineOption: EChartsOption | null = useMemo(() => {
    if (!sorted.length) return null;
    return {
      tooltip: { trigger: 'axis' },
      legend: { data: ['value', 'baseline'], top: 0, textStyle: { fontSize: 10 } },
      grid: { left: 36, right: 12, top: 26, bottom: 26 },
      xAxis: { type: 'category', data: sorted.map((a) => a.date), axisLabel: { fontSize: 10, rotate: 30 } },
      yAxis: { type: 'value', axisLabel: { fontSize: 10 } },
      series: [
        { name: 'baseline', type: 'line', data: sorted.map((a) => a.baseline), lineStyle: { type: 'dashed' }, itemStyle: { color: palette.neutral }, symbol: 'none' },
        { name: 'value', type: 'line', data: sorted.map((a) => a.value), itemStyle: { color: palette.primary }, smooth: true },
        { name: 'spike', type: 'scatter', data: sorted.map((a) => [a.date, a.value]), symbolSize: 14, itemStyle: { color: palette.negative } },
      ],
    };
  }, [sorted]);

  const driverOption: EChartsOption | null = useMemo(() => {
    if (!selected) return null;
    const ds = [...selected.top_drivers].reverse();
    return {
      tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
      grid: { left: 80, right: 24, top: 24, bottom: 20 },
      xAxis: { type: 'value', name: 'Δ%', axisLabel: { fontSize: 10 } },
      yAxis: { type: 'category', data: ds.map((d) => d.keyword), axisLabel: { fontSize: 10, width: 70, overflow: 'truncate' } },
      series: [{
        type: 'bar',
        data: ds.map((d) => ({ value: Number(d.delta_pct.toFixed(1)), itemStyle: { color: driverColor(d) } })),
      }],
    };
  }, [selected]);

  return (
    <>
    <Card title="부정 급등 원인 키워드 (anomaly + driver)" size="small" bodyStyle={{ height: 320, padding: 8 }}>
      {isLoading || !data ? (
        <Spin />
      ) : sorted.length === 0 ? (
        <Empty description="anomaly 0건 — 최근 14일 z<2.0" />
      ) : (
        <Row gutter={8} style={{ height: '100%' }}>
          <Col xs={24} md={14}>
            <Text type="secondary" style={{ fontSize: 11 }}>anomaly {sorted.length}건 · 클릭으로 선택</Text>
            {timelineOption && (
              <ReactECharts
                option={timelineOption}
                style={{ height: 240 }}
                onEvents={{
                  click: (p: { name?: string }) => {
                    if (!p?.name) return;
                    setSelectedDate(p.name);
                    setDrilldownDate(p.name);
                  },
                }}
              />
            )}
          </Col>
          <Col xs={24} md={10}>
            {selected ? (
              <>
                <div style={{ marginBottom: 4 }}>
                  <Tag color="red">{selected.date}</Tag>
                  <Tag color="geekblue">{selected.category}</Tag>
                  <Text strong style={{ fontSize: 11 }}>z={selected.z.toFixed(2)}</Text>
                  <Button
                    size="small"
                    type="link"
                    onClick={() => setDrilldownDate(selected.date)}
                    style={{ padding: '0 4px', fontSize: 11 }}
                  >
                    drill-down
                  </Button>
                </div>
                {selected.top_drivers.length === 0 ? (
                  <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="driver 없음" />
                ) : (
                  driverOption && <ReactECharts option={driverOption} style={{ height: 220 }} />
                )}
              </>
            ) : (
              <Empty description="선택 없음" />
            )}
          </Col>
        </Row>
      )}
    </Card>
    {drilldownDate && (
      <Suspense fallback={null}>
        <AnomalyDrilldownDrawer
          open={!!drilldownDate}
          date={drilldownDate}
          onClose={() => setDrilldownDate(null)}
        />
      </Suspense>
    )}
    </>
  );
}
