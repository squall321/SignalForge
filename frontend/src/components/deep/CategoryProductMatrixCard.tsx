import { useMemo, useRef } from 'react';
import { Card, Empty, Spin, Typography } from 'antd';
import ReactECharts from 'echarts-for-react';
import type { EChartsOption } from 'echarts';
import { useQuery } from '@tanstack/react-query';
import { fetchCategoryProductMatrix } from '../../services/deepApi';
import type { CategoryProductMatrixResponse } from '../../types/deep';
import { makeBaseOption, mergeOption, palette } from '../../utils/chartTheme';
import CardActions from '../common/CardActions';

const { Text } = Typography;

function buildHeatmap(resp: CategoryProductMatrixResponse): EChartsOption {
  const data = resp.cells.map((c) => [
    resp.products.indexOf(c.product),
    resp.categories.indexOf(c.category),
    Number(c.score.toFixed(3)),
    c.flag,
    c.n,
  ]);
  const custom: EChartsOption = {
    tooltip: {
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      formatter: (p: any) => {
        const d = (p?.data ?? [0, 0, 0, 'normal', 0]) as [number, number, number, string, number];
        const prod = resp.products[d[0]];
        const cat = resp.categories[d[1]];
        return `${prod} × ${cat}<br/>score ${Number(d[2]).toFixed(3)} (n=${d[4]})<br/>flag <b>${d[3]}</b>`;
      },
    },
    grid: { left: 80, right: 24, top: 16, bottom: 50 },
    xAxis: {
      type: 'category',
      data: resp.products,
      axisLabel: { fontSize: 10, rotate: 35 },
    },
    yAxis: { type: 'category', data: resp.categories, axisLabel: { fontSize: 10 } },
    visualMap: {
      // 색맹 친화: 빨↔녹 대비 회피 — negative/neutral/positive 팔레트로 교체.
      min: -1,
      max: 1,
      calculable: true,
      orient: 'vertical',
      left: 'right',
      bottom: 'center',
      inRange: { color: [palette.negative, '#fff', palette.positive] },
      textStyle: { fontSize: 10 },
    },
    series: [
      {
        type: 'heatmap',
        data,
        label: {
          show: true,
          fontSize: 9,
          // eslint-disable-next-line @typescript-eslint/no-explicit-any
          formatter: (p: any) => {
            const flag = Array.isArray(p?.data) ? p.data[3] : 'normal';
            return flag !== 'normal' ? '★' : '';
          },
        },
      },
    ],
  };
  return mergeOption(makeBaseOption({ tooltipMode: 'off' }), custom);
}

export default function CategoryProductMatrixCard() {
  const chartRef = useRef<unknown>(null);
  const { data, isLoading } = useQuery({
    queryKey: ['deep', 'matrix'],
    queryFn: () => fetchCategoryProductMatrix({ period_days: 30, top_products: 10 }),
    staleTime: 5 * 60_000,
  });
  const opt = useMemo(() => (data ? buildHeatmap(data) : null), [data]);

  return (
    <Card
      title="카테고리 × 제품 sentiment 매트릭스 (★ = |z| ≥ 2 outlier)"
      size="small"
      bodyStyle={{ height: 320, padding: 8 }}
      extra={
        <CardActions
          title="카테고리×제품 매트릭스"
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
      ) : !data.cells.length ? (
        <Empty description="셀 없음" />
      ) : (
        <>
          <Text type="secondary" style={{ fontSize: 11 }}>
            cells {data.cells.length} · outlier{' '}
            {data.cells.filter((c) => c.flag !== 'normal').length}개
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
