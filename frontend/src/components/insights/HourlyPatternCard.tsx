import { useMemo, useRef } from 'react';
import { Card, Empty, Spin, Typography } from 'antd';
import ReactECharts from 'echarts-for-react';
import type { EChartsOption } from 'echarts';
import { useQuery } from '@tanstack/react-query';
import { fetchHourlyPattern } from '../../services/insightsApi';
import type { HourlyPatternResponse } from '../../types/insights';
import { useViewport } from '../../utils/useViewport';
import {
  defaultAxisTooltipFormatter,
  palette,
  seriesColors,
} from '../../utils/chartTheme';
import { useFilterStore } from '../../stores/useFilterStore';
import { effectivePeriodDays } from '../../types/filters';
import CardActions from '../common/CardActions';
import FavoriteButton from '../global/FavoriteButton';

const { Text } = Typography;

// 트랙 D — viewport.isMobile 시 차트 옵션 축소 (legend off / dataZoom on / axisLabel 작게).
// 데스크탑 기본 옵션과 동일한 shape 을 유지하되, 모바일 분기에서 일부 속성만 override.
export function buildHourlyOption(
  resp: HourlyPatternResponse,
  opts: { mobile?: boolean } = {},
): EChartsOption {
  const mobile = !!opts.mobile;
  const hours = resp.points.map((p) => `${p.hour}h`);
  const counts = resp.points.map((p) => p.count);
  const sents = resp.points.map((p) => p.sent_avg);
  return {
    color: seriesColors,
    tooltip: {
      trigger: 'axis',
      formatter: defaultAxisTooltipFormatter as unknown as (p: unknown) => string,
    },
    grid: mobile
      ? { left: 36, right: 36, top: 12, bottom: 40 }
      : { left: 56, right: 56, top: 16, bottom: 36 },
    legend: mobile
      ? { show: false }
      : { data: ['count', 'sent_avg'], top: 0 },
    xAxis: {
      type: 'category',
      data: hours,
      axisLabel: { fontSize: mobile ? 10 : 11 },
    },
    yAxis: [
      { type: 'value', name: 'count', position: 'left', axisLabel: { fontSize: mobile ? 10 : 12 } },
      {
        type: 'value', name: 'sent', position: 'right',
        min: -1, max: 1, splitLine: { show: false },
        axisLabel: { fontSize: mobile ? 10 : 12 },
      },
    ],
    ...(mobile
      ? { dataZoom: [{ type: 'inside' }, { type: 'slider', height: 16, bottom: 4 }] }
      : {}),
    series: [
      {
        name: 'count', type: 'bar', data: counts, yAxisIndex: 0,
        itemStyle: { color: palette.primary },
      },
      {
        name: 'sent_avg', type: 'line', data: sents, yAxisIndex: 1, smooth: true,
        itemStyle: { color: palette.accent },
      },
    ],
  };
}

export default function HourlyPatternCard() {
  const vp = useViewport();
  // P4.3 트랙 B — 전역 필터 반영 (products[0] + period_days).
  const filters = useFilterStore();
  const period = effectivePeriodDays(filters);
  const product = filters.products[0]; // backend hourly-pattern 은 단일 product
  const chartRef = useRef<unknown>(null);

  const { data, isLoading } = useQuery({
    queryKey: ['insights', 'hourly', period, product ?? null],
    queryFn: () => fetchHourlyPattern({ period_days: period, product }),
    staleTime: 5 * 60_000,
  });

  const opt = useMemo(
    () => (data ? buildHourlyOption(data, { mobile: vp.isMobile }) : null),
    [data, vp.isMobile],
  );

  // 모바일: body 240 / chart 200 — 데스크탑: body 280 / chart 240 (기존 유지).
  const bodyH = vp.isMobile ? 240 : 280;
  const chartH = vp.isMobile ? 200 : 240;

  return (
    <Card
      title={`시간대 패턴 (0~23시, 최근 ${period}일${product ? ` · ${product}` : ''})`}
      size="small"
      bodyStyle={{ height: bodyH, padding: vp.isMobile ? 8 : 12 }}
      data-testid="hourly-card"
      extra={
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
          <FavoriteButton cardId="hourly-pattern" />
          <CardActions
            title="시간대 패턴"
            echartsRef={chartRef.current as never}
            json={data}
            renderExpanded={() =>
              opt ? <ReactECharts option={opt} style={{ height: 480 }} /> : <div>데이터 없음</div>
            }
          />
        </span>
      }
    >
      {isLoading ? (
        <Spin />
      ) : !opt || !data || data.points.every((p) => p.count === 0) ? (
        <Empty description="데이터 없음" />
      ) : (
        <>
          <Text type="secondary" style={{ fontSize: vp.isMobile ? 11 : 12 }}>
            총 {((data.meta?.total as number) || 0).toLocaleString()}건 ·
            피크 시간 {String(data.meta?.peak_hour ?? '-')}시
          </Text>
          <ReactECharts
            option={opt}
            style={{ height: chartH }}
            data-testid="hourly-chart"
            ref={(r) => {
              chartRef.current = r;
            }}
          />
        </>
      )}
    </Card>
  );
}
