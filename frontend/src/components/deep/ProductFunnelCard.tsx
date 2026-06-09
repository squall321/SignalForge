import { useMemo, useRef, useState } from 'react';
import { Card, Empty, Select, Space, Spin, Typography } from 'antd';
import ReactECharts from 'echarts-for-react';
import type { EChartsOption } from 'echarts';
import { useQuery } from '@tanstack/react-query';
import { fetchProductFunnel } from '../../services/deepApi';
import type { ProductFunnelResponse } from '../../types/deep';
import { makeBaseOption, mergeOption } from '../../utils/chartTheme';
import CardActions from '../common/CardActions';

const { Text } = Typography;

const PRODUCT_OPTIONS = [
  { label: 'Galaxy S25', value: 'GS25' },
  { label: 'Galaxy S25+', value: 'GS25P' },
  { label: 'Galaxy S25 Ultra', value: 'GS25U' },
  { label: 'Galaxy S24', value: 'GS24' },
  { label: 'Galaxy S24 Ultra', value: 'GS24U' },
];

function buildOption(resp: ProductFunnelResponse): EChartsOption {
  const stages = resp.stages;
  const custom: EChartsOption = {
    tooltip: {
      trigger: 'item',
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      formatter: ((p: any) => {
        const name = String(p?.name ?? '');
        const s = stages.find((x) => x.stage === name);
        if (!s) return name;
        return (
          `<b>${s.stage}</b><br/>${s.period}<br/>n=${s.count}<br/>`
          + `sent=${s.sent_avg.toFixed(2)}<br/>kw: ${s.top_keywords.join(', ') || '-'}`
        );
      }) as never,
    },
    series: [
      {
        type: 'funnel',
        left: 16,
        top: 16,
        bottom: 16,
        width: '80%',
        sort: 'none',
        gap: 2,
        label: {
          show: true,
          fontSize: 11,
          // eslint-disable-next-line @typescript-eslint/no-explicit-any
          formatter: ((p: any) => {
            const name = String(p?.name ?? '');
            const value = Number(p?.value ?? 0);
            const s = stages.find((x) => x.stage === name);
            const sa = s ? ` · sent ${s.sent_avg.toFixed(2)}` : '';
            return `${name}: ${value}${sa}`;
          }) as never,
        },
        data: stages.map((s) => ({ name: s.stage, value: s.count })),
      },
    ],
  };
  // funnel 차트 — color 회전만 base 에서 위임 받고 나머지는 custom 우선.
  return mergeOption(makeBaseOption({ tooltipMode: 'off' }), custom);
}

export default function ProductFunnelCard() {
  const [product, setProduct] = useState<string>('GS25');
  const chartRef = useRef<unknown>(null);
  const { data, isLoading } = useQuery({
    queryKey: ['deep', 'product-funnel', product],
    queryFn: () => fetchProductFunnel({ product, period_days: 180 }),
    staleTime: 5 * 60_000,
  });
  const opt = useMemo(() => (data ? buildOption(data) : null), [data]);
  const m = (data?.meta as { release_date?: string; reason?: string }) ?? {};
  return (
    <Card
      title={
        <Space size={8}>
          <span>제품 라이프사이클 깔때기</span>
          <Select
            size="small"
            value={product}
            onChange={setProduct}
            options={PRODUCT_OPTIONS}
            style={{ width: 150 }}
          />
        </Space>
      }
      size="small"
      bodyStyle={{ height: 320, padding: 8 }}
      extra={
        <CardActions
          title="제품 라이프사이클"
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
      ) : !data.stages.length ? (
        <Empty description={m.reason ?? '단계 데이터 없음'} />
      ) : (
        <>
          <Text type="secondary" style={{ fontSize: 11 }}>
            release {m.release_date ?? '-'} · 단계 {data.stages.length}개
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
