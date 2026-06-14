// 차트 갤러리 — backend /charts/* 의 echarts_option 을 그대로 렌더.
// MCP 도구 (LLM) 와 동일 데이터/규격을 화면으로 확인하는 페이지.
import { useState, useEffect, useCallback } from 'react';
import { Card, Tabs, Select, Space, Spin, Typography, Alert } from 'antd';
import ReactECharts from 'echarts-for-react';
import type { EChartsOption } from 'echarts';
import {
  fetchSentimentTimeseries, fetchCountryDistribution, fetchCategoryDistribution,
  fetchCrisisTimeline, fetchKeywordNetwork, type ChartResponse,
} from '../services/chartsApi';

const { Text } = Typography;

const CRISIS_CASES = [
  { value: 'GN7', label: 'Galaxy Note 7 발화' },
  { value: 'GZF1', label: 'Galaxy Fold 1 결함' },
  { value: 'GS22U', label: 'Galaxy S22 GoS' },
  { value: 'GZFL3', label: 'Galaxy Z Flip 3 힌지' },
  { value: 'GS20', label: 'Galaxy S20 가격' },
];

// 기간 옵션 — 0 = 전기간 (2007~, r6 backfill 옛 글 포함)
const PERIOD_OPTIONS = [
  { value: 0, label: '전체 기간 (2007~)' },
  { value: 3650, label: '최근 10년' },
  { value: 1825, label: '최근 5년' },
  { value: 365, label: '최근 1년' },
  { value: 90, label: '최근 90일' },
  { value: 30, label: '최근 30일' },
];

function ChartPanel({
  loader, deps, height = 460,
}: {
  loader: () => Promise<ChartResponse>;
  deps: unknown[];
  height?: number;
}) {
  const [opt, setOpt] = useState<EChartsOption | null>(null);
  const [summary, setSummary] = useState('');
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);

  const run = useCallback(async () => {
    setLoading(true); setErr(null);
    try {
      const r = await loader();
      setOpt(r.echarts_option); setSummary(r.summary);
    } catch (e) {
      setErr(e instanceof Error ? e.message : '로드 실패');
    } finally {
      setLoading(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);

  useEffect(() => { run(); }, [run]);

  if (err) return <Alert type="error" message={err} showIcon />;
  if (loading) return <div style={{ textAlign: 'center', padding: 80 }}><Spin /></div>;
  return (
    <>
      <Text type="secondary">{summary}</Text>
      {opt && <ReactECharts option={opt} style={{ height }} notMerge lazyUpdate />}
    </>
  );
}

export default function ChartGallery() {
  // 제품 코드 (대표 몇 개 — 실제론 products endpoint 에서 가져와도 됨)
  const [products, setProducts] = useState<string[]>(['GS25', 'GZF8']);
  const [crisisCase, setCrisisCase] = useState('GN7');
  // 차트별 기간 (기본: 시계열/분포 전체, 네트워크는 30일 — self-join 무거움)
  const [tsDays, setTsDays] = useState(0);
  const [tsGran, setTsGran] = useState('month');
  const [countryDays, setCountryDays] = useState(0);
  const [categoryDays, setCategoryDays] = useState(0);
  const [netDays, setNetDays] = useState(30);
  const productOptions = [
    'GS25', 'GS24', 'GZF8', 'GZF7', 'GZFL8', 'GN7', 'GS22U', 'GS20',
  ].map((c) => ({ value: c, label: c }));

  const periodSelect = (value: number, onChange: (v: number) => void) => (
    <>
      <Text>기간:</Text>
      <Select value={value} onChange={onChange} options={PERIOD_OPTIONS} style={{ minWidth: 160 }} />
    </>
  );

  const items = [
    {
      key: 'timeseries',
      label: '시계열',
      children: (
        <Card>
          <Space style={{ marginBottom: 12 }} wrap>
            <Text>제품:</Text>
            <Select mode="multiple" value={products} onChange={setProducts}
              options={productOptions} style={{ minWidth: 280 }} maxTagCount={4} />
            {periodSelect(tsDays, setTsDays)}
            <Text>단위:</Text>
            <Select value={tsGran} onChange={setTsGran} style={{ minWidth: 100 }}
              options={[{ value: 'month', label: '월' }, { value: 'week', label: '주' }, { value: 'day', label: '일' }]} />
          </Space>
          <ChartPanel loader={() => fetchSentimentTimeseries(products, tsDays, tsGran)}
            deps={[products, tsDays, tsGran]} />
        </Card>
      ),
    },
    {
      key: 'country',
      label: '국가 분포',
      children: (
        <Card>
          <Space style={{ marginBottom: 12 }} wrap>{periodSelect(countryDays, setCountryDays)}</Space>
          <ChartPanel loader={() => fetchCountryDistribution(undefined, 15, countryDays)}
            deps={[countryDays]} height={500} />
        </Card>
      ),
    },
    {
      key: 'category',
      label: '카테고리 분포',
      children: (
        <Card>
          <Space style={{ marginBottom: 12 }} wrap>{periodSelect(categoryDays, setCategoryDays)}</Space>
          <ChartPanel loader={() => fetchCategoryDistribution(undefined, 15, categoryDays)}
            deps={[categoryDays]} height={500} />
        </Card>
      ),
    },
    {
      key: 'crisis',
      label: '위기 타임라인',
      children: (
        <Card>
          <Space style={{ marginBottom: 12 }} wrap>
            <Text>사례:</Text>
            <Select value={crisisCase} onChange={setCrisisCase}
              options={CRISIS_CASES} style={{ minWidth: 220 }} />
            <Text type="secondary">(위기 사례는 사건 기간 고정)</Text>
          </Space>
          <ChartPanel loader={() => fetchCrisisTimeline(crisisCase)} deps={[crisisCase]} />
        </Card>
      ),
    },
    {
      key: 'network',
      label: '키워드 네트워크',
      children: (
        <Card>
          <Space style={{ marginBottom: 12 }} wrap>
            {periodSelect(netDays, setNetDays)}
            <Text type="secondary">(전기간은 제품 선택 시 권장)</Text>
          </Space>
          <ChartPanel loader={() => fetchKeywordNetwork(undefined, netDays, 3, 40)}
            deps={[netDays]} height={560} />
        </Card>
      ),
    },
  ];

  return (
    <div style={{ padding: 16 }}>
      <Typography.Title level={3}>차트 갤러리</Typography.Title>
      <Text type="secondary">
        backend <code>/api/v1/charts/*</code> 의 echarts_option 을 그대로 렌더 —
        MCP 도구 (LLM) 와 동일 데이터·규격 (voc_active 정합).
      </Text>
      <Tabs items={items} style={{ marginTop: 16 }} />
    </div>
  );
}
