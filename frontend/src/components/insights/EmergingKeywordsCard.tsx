import { useMemo, useRef } from 'react';
import { Card, Empty, Spin, Tabs, Typography } from 'antd';
import ReactECharts from 'echarts-for-react';
import type { EChartsOption } from 'echarts';
import { useQuery } from '@tanstack/react-query';
import { fetchEmergingKeywords } from '../../services/insightsApi';
import type { KeywordTrend } from '../../types/insights';
import { palette } from '../../utils/chartTheme';
import { useFilterStore } from '../../stores/useFilterStore';
import { effectivePeriodDays } from '../../types/filters';
import CardActions from '../common/CardActions';
import FavoriteButton from '../global/FavoriteButton';

const { Text } = Typography;

function buildBarOption(rows: KeywordTrend[], kind: 'up' | 'down'): EChartsOption {
  const sorted = [...rows].sort((a, b) =>
    kind === 'up' ? b.growth_pct - a.growth_pct : a.growth_pct - b.growth_pct,
  ).slice(0, 10);
  const labels = sorted.map((r) => r.keyword).reverse();
  const values = sorted.map((r) => r.growth_pct).reverse();
  const color = kind === 'up' ? palette.negative : palette.primary;
  return {
    tooltip: {
      trigger: 'axis',
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      formatter: (p: any) => {
        const arr = Array.isArray(p) ? p : [p];
        const r = sorted.find((x) => x.keyword === arr[0].name);
        const v = Number(arr[0].value ?? 0);
        return `${arr[0].name}<br/>${r?.prev_week_count ?? 0} → ${
          r?.this_week_count ?? 0
        } (${v.toFixed(0)}%)`;
      },
    },
    grid: { left: 100, right: 24, top: 8, bottom: 24 },
    xAxis: { type: 'value', axisLabel: { formatter: '{value}%', fontSize: 10 } },
    yAxis: {
      type: 'category',
      data: labels,
      axisLabel: { fontSize: 11, width: 90, overflow: 'truncate' },
    },
    series: [
      {
        type: 'bar',
        data: values,
        itemStyle: { color },
        label: {
          show: true,
          position: 'right',
          fontSize: 10,
          // eslint-disable-next-line @typescript-eslint/no-explicit-any
          formatter: (p: any) => {
            const v = Number(p.value ?? 0);
            return `${v >= 0 ? '+' : ''}${v.toFixed(0)}%`;
          },
        },
      },
    ],
  };
}

export default function EmergingKeywordsCard() {
  const filters = useFilterStore();
  // P4.3 트랙 B — 기간이 7d 이하면 그대로, 30d 이상이면 emerging 윈도우는 7 유지(주간 비교 의미 보존)하되
  // 필터 변경 시 queryKey 가 바뀌어 카드가 명시적으로 reload 되도록.
  const period = Math.min(effectivePeriodDays(filters), 14);
  const upRef = useRef<unknown>(null);

  const { data, isLoading } = useQuery({
    queryKey: ['insights', 'emerging', period],
    queryFn: () => fetchEmergingKeywords({ period_days: period, top_n: 20 }),
    staleTime: 5 * 60_000,
  });

  const optUp = useMemo(() => (data ? buildBarOption(data.emerging, 'up') : null), [data]);
  const optDown = useMemo(() => (data ? buildBarOption(data.declining, 'down') : null), [data]);

  return (
    <Card
      title={`키워드 emerging / declining (최근 ${period}일)`}
      size="small"
      bodyStyle={{ height: 280, padding: 8 }}
      extra={
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
          <FavoriteButton cardId="emerging-keywords" />
          <CardActions
            title="Emerging Keywords"
            echartsRef={upRef.current as never}
            json={data}
            renderExpanded={() =>
              optUp ? <ReactECharts option={optUp} style={{ height: 480 }} /> : <div>데이터 없음</div>
            }
          />
        </span>
      }
    >
      {isLoading || !data ? (
        <Spin />
      ) : !data.emerging.length && !data.declining.length ? (
        <Empty description="데이터 없음" />
      ) : (
        <Tabs
          size="small"
          items={[
            {
              key: 'up',
              label: `Emerging (${data.emerging.length})`,
              children: optUp ? (
                <ReactECharts
                  option={optUp}
                  style={{ height: 220 }}
                  ref={(r) => {
                    upRef.current = r;
                  }}
                />
              ) : (
                <Empty description="데이터 없음" />
              ),
            },
            {
              key: 'down',
              label: `Declining (${data.declining.length})`,
              children: optDown ? (
                <ReactECharts option={optDown} style={{ height: 220 }} />
              ) : (
                <Empty description="데이터 없음" />
              ),
            },
          ]}
        />
      )}
      {data && (
        <Text type="secondary" style={{ fontSize: 11 }}>
          상승=주황, 하락=파랑 (색맹 친화 팔레트)
        </Text>
      )}
    </Card>
  );
}
