import { useMemo, useState } from 'react';
import { Alert, Card, Col, Row, Space, Spin, Typography } from 'antd';
import { useQuery } from '@tanstack/react-query';
import dayjs from 'dayjs';
import api from '../services/api';
import { useFilterStore } from '../stores/useFilterStore';
import CompareToggle from '../components/temporal/CompareToggle';
import LLMNarrativePanel from '../components/temporal/LLMNarrativePanel';
import TemporalChart from '../components/temporal/TemporalChart';
import type {
  CompareMode,
  LLMNarrativeRequest,
  TemporalSeriesResponse,
} from '../types/temporal';

const { Title, Paragraph } = Typography;

// 백엔드 미구현 시 화면을 비우지 않기 위한 폴백 더미 데이터.
// 실제 운영에서는 /analytics/temporal-series 응답이 사용된다.
function fallbackResponse(mode: CompareMode): TemporalSeriesResponse {
  const start = dayjs().subtract(60, 'day');
  const labels =
    mode === 'products'
      ? [
          { key: 'GS25', label: 'Galaxy S25' },
          { key: 'GS25U', label: 'Galaxy S25 Ultra' },
        ]
      : mode === 'categories'
        ? [
            { key: 'BAT', label: '배터리' },
            { key: 'CAM', label: '카메라' },
          ]
        : [
            { key: 'this', label: '최근 60일' },
            { key: 'prev', label: '직전 60일' },
          ];
  const series = labels.map((l, idx) => ({
    key: l.key,
    label: l.label,
    data: Array.from({ length: 60 }, (_, i) => {
      const d = start.add(i, 'day').format('YYYY-MM-DD');
      const base = 80 + idx * 20;
      const trend = Math.sin((i + idx * 6) / 7) * 25;
      const noise = Math.random() * 10;
      return {
        date: d,
        count: Math.round(base + trend + noise),
        sent_avg: +((Math.cos(i / 9) * 0.4 - idx * 0.05).toFixed(2)),
      };
    }),
  }));
  return {
    mode,
    series,
    events: [
      {
        date: start.add(20, 'day').format('YYYY-MM-DD'),
        title: 'Galaxy S25 출시',
        category: 'launch',
        product_code: 'GS25',
      },
      {
        date: start.add(42, 'day').format('YYYY-MM-DD'),
        title: 'One UI 7 베타',
        category: 'update',
      },
    ],
    changepoints: [
      { date: start.add(28, 'day').format('YYYY-MM-DD'), series_key: labels[0].key, delta: +18, reason: 'launch spike' },
      { date: start.add(50, 'day').format('YYYY-MM-DD'), series_key: labels[0].key, delta: -12 },
    ],
  };
}

async function fetchTemporal(
  mode: CompareMode,
  filters: { start?: string; end?: string; products: string[] },
): Promise<TemporalSeriesResponse> {
  try {
    const { data } = await api.get<TemporalSeriesResponse>('/analytics/temporal-series', {
      params: {
        mode,
        start: filters.start || undefined,
        end: filters.end || undefined,
        products: filters.products.length ? filters.products.join(',') : undefined,
      },
    });
    return data;
  } catch (err) {
    // 백엔드 미구현/오류 시 폴백 (UI 검증용)
    return fallbackResponse(mode);
  }
}

export default function TemporalInsight() {
  const [mode, setMode] = useState<CompareMode>('products');
  const { dateRange, products } = useFilterStore();

  const { data, isLoading, isError, error } = useQuery({
    queryKey: ['temporal-series', mode, dateRange.start, dateRange.end, products.join(',')],
    queryFn: () =>
      fetchTemporal(mode, {
        start: dateRange.start,
        end: dateRange.end,
        products,
      }),
    staleTime: 60_000,
  });

  // LLM 요청 페이로드
  const llmRequest: LLMNarrativeRequest | null = useMemo(() => {
    if (!data || !data.series.length) return null;
    const allDates = data.series.flatMap((s) => s.data.map((p) => p.date)).sort();
    return {
      mode,
      series_keys: data.series.map((s) => s.key),
      date_start: dateRange.start || allDates[0] || '',
      date_end: dateRange.end || allDates[allDates.length - 1] || '',
      context: {
        series: data.series,
        events: data.events,
        changepoints: data.changepoints,
      },
    };
  }, [data, mode, dateRange.start, dateRange.end]);

  return (
    <div>
      <Title level={3} style={{ marginTop: 0 }}>
        시계열 인사이트
      </Title>
      <Paragraph type="secondary">
        제품/기간/카테고리 추이와 출시 이벤트, 변곡점을 함께 보고 LLM 으로 해석합니다.
      </Paragraph>

      <Space style={{ marginBottom: 12 }}>
        <CompareToggle value={mode} onChange={setMode} />
      </Space>

      <Row gutter={[16, 16]}>
        <Col xs={24} xl={16}>
          <Card title="시계열 차트" bodyStyle={{ padding: 12 }}>
            {isLoading && (
              <div style={{ textAlign: 'center', padding: '120px 0' }}>
                <Spin size="large" />
              </div>
            )}
            {isError && (
              <Alert
                type="error"
                showIcon
                message="시계열 조회 실패"
                description={error instanceof Error ? error.message : '알 수 없는 오류'}
              />
            )}
            {data && !isLoading && (
              <TemporalChart
                series={data.series}
                events={data.events}
                changepoints={data.changepoints}
                height={480}
              />
            )}
          </Card>
        </Col>
        <Col xs={24} xl={8}>
          <LLMNarrativePanel request={llmRequest} disabled={isLoading || isError} />
        </Col>
      </Row>
    </div>
  );
}
