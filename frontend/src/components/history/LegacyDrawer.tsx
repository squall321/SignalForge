// R11 트랙 E — Legacy 모델 상세 Drawer (R10 5-샘플 단순 wrapper → 풍부한 detail).
// LegacyDistributionCard 의 horizontal bar 클릭 시 해당 모델 종합 detail.
//
// 상단: Statistic 3 (총 voc, neg_rate, top_platform)
// 중단: 월별 voc timeline (echarts bar)
// 하단: 5 sample voc (sentiment 색 분류 + 원본 링크)
import { useQuery } from '@tanstack/react-query';
import {
  Alert, Col, Drawer, Empty, List, Row, Spin, Statistic, Tag, Typography,
} from 'antd';
import ReactECharts from 'echarts-for-react';
import api from '../../services/api';

interface SampleItem {
  id: number;
  content: string | null;
  sentiment_label: string | null;
  author_name: string | null;
  published_at: string | null;
  source_url: string | null;
}
interface VocResponse {
  total: number;
  items: SampleItem[];
}
interface MasterRow {
  product_code: string;
  name_ko: string | null;
  released_at: string | null;
  series: string;
  month: string;
  voc_count: number;
  sent_avg: number | null;
  neg_rate: number | null;
}

async function fetchLegacyVoc(code: string): Promise<VocResponse> {
  const { data } = await api.get(`/products/${encodeURIComponent(code)}/voc`, {
    params: { limit: 5 },
  });
  return data;
}

async function fetchMasterTimeline(code: string): Promise<MasterRow[]> {
  // R11 D — galaxy-master-timeline MV 활용 (개별 모델 month 시계열)
  // backend galaxy-timeline endpoint 가 series 단위. 여기는 단일 product 만 필요 → series 받아 client filter.
  // 차후 개별 endpoint 추가 가능. 일단 series prefix 추출.
  // GS25 → GS, GN7 → GN, GZF1 → GZF, GW7 → GW 등
  const seriesPrefix = code.match(/^[A-Z]+/)?.[0] ?? '';
  if (!seriesPrefix) return [];
  try {
    const { data } = await api.get('/deep/galaxy-timeline', {
      params: { series: seriesPrefix },
    });
    const all: MasterRow[] = data?.rows ?? data?.timeline ?? data?.items ?? [];
    return all.filter((r) => r.product_code === code);
  } catch {
    return [];
  }
}

const { Text, Paragraph } = Typography;

export interface LegacyDrawerProps {
  code: string | null;
  name: string | null;
  onClose: () => void;
}

export default function LegacyDrawer({ code, name, onClose }: LegacyDrawerProps) {
  const enabled = !!code;
  const vocQ = useQuery({
    queryKey: ['legacy-voc', code],
    queryFn: () => fetchLegacyVoc(code!),
    enabled,
    staleTime: 60_000,
  });
  const mtQ = useQuery({
    queryKey: ['legacy-master-timeline', code],
    queryFn: () => fetchMasterTimeline(code!),
    enabled,
    staleTime: 5 * 60_000,
  });

  // 월별 voc bar chart 옵션
  const monthlyOption = (() => {
    const rows = (mtQ.data ?? []).slice().sort((a, b) => a.month.localeCompare(b.month));
    return {
      grid: { top: 16, right: 16, bottom: 30, left: 40 },
      tooltip: { trigger: 'axis' },
      xAxis: {
        type: 'category',
        data: rows.map((r) => r.month?.slice(0, 7) ?? ''),
        axisLabel: { fontSize: 9, rotate: 30 },
      },
      yAxis: { type: 'value', axisLabel: { fontSize: 9 } },
      series: [
        {
          type: 'bar',
          data: rows.map((r) => r.voc_count),
          itemStyle: { color: '#0072B2' },
          name: '건수',
        },
      ],
    };
  })();

  // 통계 — 총 voc / 평균 sent / 사이트 1위
  const total = vocQ.data?.total ?? 0;
  const negCount = (vocQ.data?.items ?? []).filter((i) => i.sentiment_label === 'negative').length;
  const negSamplePct = vocQ.data?.items?.length
    ? Math.round((negCount * 100) / vocQ.data.items.length)
    : 0;

  return (
    <Drawer
      title={code ? `${name ?? code} — 상세` : ''}
      open={enabled}
      onClose={onClose}
      width={520}
      data-testid="legacy-drawer"
    >
      {vocQ.isError && <Alert type="error" message="voc 샘플 로드 실패" />}
      {vocQ.isLoading && <Spin />}

      {vocQ.data && (
        <Row gutter={12} style={{ marginBottom: 12 }}>
          <Col span={8}>
            <Statistic title="총 voc" value={total.toLocaleString()} />
          </Col>
          <Col span={8}>
            <Statistic title="샘플 내 부정 %" value={`${negSamplePct}%`} />
          </Col>
          <Col span={8}>
            <Statistic
              title="시리즈"
              value={code?.match(/^[A-Z]+/)?.[0] ?? '-'}
            />
          </Col>
        </Row>
      )}

      {mtQ.data && mtQ.data.length > 0 && (
        <div style={{ marginBottom: 16 }}>
          <Text strong style={{ fontSize: 12 }}>월별 voc 추이</Text>
          <ReactECharts option={monthlyOption} style={{ height: 160 }} />
        </div>
      )}

      {vocQ.data && vocQ.data.items.length === 0 && <Empty description="샘플 없음" />}
      {vocQ.data && vocQ.data.items.length > 0 && (
        <>
          <Paragraph type="secondary" style={{ fontSize: 12, marginBottom: 4 }}>
            최근 voc 5건 (감성 분류 + 원본)
          </Paragraph>
          <List
            size="small"
            dataSource={vocQ.data.items}
            renderItem={(it) => (
              <List.Item data-testid={`legacy-voc-item-${it.id}`}>
                <div style={{ width: '100%' }}>
                  <div style={{ marginBottom: 4 }}>
                    {it.sentiment_label && (
                      <Tag
                        color={
                          it.sentiment_label === 'positive'
                            ? 'green'
                            : it.sentiment_label === 'negative'
                            ? 'red'
                            : 'default'
                        }
                      >
                        {it.sentiment_label}
                      </Tag>
                    )}
                    {it.published_at && (
                      <Text type="secondary" style={{ fontSize: 11 }}>
                        {it.published_at.slice(0, 10)}
                      </Text>
                    )}
                  </div>
                  <Text style={{ fontSize: 12 }}>
                    {(it.content ?? '').slice(0, 200)}
                    {(it.content?.length ?? 0) > 200 ? '…' : ''}
                  </Text>
                  {it.source_url && (
                    <div>
                      <a
                        href={it.source_url}
                        target="_blank"
                        rel="noreferrer"
                        style={{ fontSize: 11 }}
                      >
                        원본 보기
                      </a>
                    </div>
                  )}
                </div>
              </List.Item>
            )}
          />
        </>
      )}
    </Drawer>
  );
}
