// 차트 갤러리 — backend /charts/* 의 echarts_option 을 렌더 + 거시·미시 풀 컨트롤.
// 축: 시간(기간/단위/dataZoom) · 제품(전체↔특정) · 지리(막대/지도) · 항목(클릭 드릴다운).
import { useState, useEffect, useCallback } from 'react';
import { Card, Tabs, Select, Space, Spin, Typography, Alert, Segmented, Drawer, Button } from 'antd';
import ReactECharts from 'echarts-for-react';
import type { EChartsOption } from 'echarts';
import WorldChoropleth from '../components/geo/WorldChoropleth';
import type { CountryMetric } from '../types/geo';
import { alpha2ToAlpha3, alpha3ToAlpha2 } from '../utils/countryCodes';
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

const PERIOD_OPTIONS = [
  { value: 0, label: '전체 기간 (2007~)' },
  { value: 3650, label: '최근 10년' },
  { value: 1825, label: '최근 5년' },
  { value: 365, label: '최근 1년' },
  { value: 90, label: '최근 90일' },
  { value: 30, label: '최근 30일' },
];

const PRODUCT_CODES = ['GS25', 'GS24', 'GZF8', 'GZF7', 'GZFL8', 'GN7', 'GS22U', 'GS20'];

// line/timeline 차트에 시간 미시 줌 (dataZoom inside+slider) + PNG 저장 toolbox 를 얹는다.
// backend echarts_option 의 series/xAxis 는 그대로 두고 컨트롤만 추가 (얕은 병합 안전).
function withZoom(opt: EChartsOption, enable: boolean): EChartsOption {
  if (!enable) return opt;
  return {
    ...opt,
    dataZoom: [
      { type: 'inside', filterMode: 'none' },
      { type: 'slider', filterMode: 'none', bottom: 8, height: 18 },
    ],
    toolbox: { feature: { saveAsImage: { pixelRatio: 2, title: 'PNG' } }, right: 16 },
    grid: { left: '3%', right: '4%', bottom: 56, top: 60, containLabel: true },
  };
}

function ChartPanel({
  loader, deps, height = 460, zoom = false, onClick,
}: {
  loader: () => Promise<ChartResponse>;
  deps: unknown[];
  height?: number;
  zoom?: boolean;
  onClick?: (name: string, raw: unknown) => void;
}) {
  const [resp, setResp] = useState<ChartResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);

  const run = useCallback(async () => {
    setLoading(true); setErr(null);
    try { setResp(await loader()); }
    catch (e) { setErr(e instanceof Error ? e.message : '로드 실패'); }
    finally { setLoading(false); }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);

  useEffect(() => { run(); }, [run]);

  if (err) return <Alert type="error" message={err} showIcon />;
  if (loading) return <div style={{ textAlign: 'center', padding: 80 }}><Spin /></div>;
  if (!resp) return null;
  const opt = withZoom(resp.echarts_option, zoom && resp.chart_type === 'line');
  return (
    <>
      <Text type="secondary">{resp.summary}</Text>
      <ReactECharts
        option={opt} style={{ height }} notMerge lazyUpdate
        onEvents={onClick ? { click: (p: { name?: string }) => p.name && onClick(p.name, resp.raw) } : undefined}
      />
    </>
  );
}

export default function ChartGallery() {
  const [products, setProducts] = useState<string[]>(['GS25', 'GZF8']);
  const [crisisCase, setCrisisCase] = useState('GN7');
  // 공통 제품 필터 (분포/네트워크의 거시↔미시) — undefined = 전체
  const [filterProduct, setFilterProduct] = useState<string | undefined>(undefined);
  // 기간
  const [tsDays, setTsDays] = useState(0);
  const [tsGran, setTsGran] = useState('month');
  const [countryDays, setCountryDays] = useState(0);
  const [categoryDays, setCategoryDays] = useState(0);
  const [netDays, setNetDays] = useState(30);
  // 분포 표현: 막대 / 지도
  const [countryView, setCountryView] = useState<'bar' | 'map'>('bar');
  // 드릴다운: 국가 클릭 → 카테고리 country 필터
  const [drillCountry, setDrillCountry] = useState<string | undefined>(undefined);
  // 지도용 국가 데이터
  const [mapData, setMapData] = useState<CountryMetric[]>([]);
  // 노드/항목 클릭 상세 Drawer
  const [drawer, setDrawer] = useState<{ open: boolean; title: string; body: string }>(
    { open: false, title: '', body: '' });

  const productOptions = PRODUCT_CODES.map((c) => ({ value: c, label: c }));
  const filterOptions = [{ value: '', label: '전체 제품' }, ...productOptions];

  const periodSel = (value: number, onChange: (v: number) => void) => (
    <><Text>기간:</Text>
      <Select value={value} onChange={onChange} options={PERIOD_OPTIONS} style={{ minWidth: 160 }} /></>
  );

  // 지도 모드 진입 시 국가 데이터 로드 (charts country-distribution → alpha3 변환)
  useEffect(() => {
    if (countryView !== 'map') return;
    fetchCountryDistribution(filterProduct, 50, countryDays).then((r) => {
      const rows = r.raw as Array<{ country_code: string; voc_count: number; avg_score: number }>;
      setMapData(rows.map((d) => ({
        country_code: alpha2ToAlpha3(d.country_code), count: d.voc_count, sent_avg: d.avg_score,
      })));
    }).catch(() => setMapData([]));
  }, [countryView, filterProduct, countryDays]);

  const items = [
    {
      key: 'timeseries', label: '시계열',
      children: (
        <Card>
          <Space style={{ marginBottom: 12 }} wrap>
            <Text>제품:</Text>
            <Select mode="multiple" value={products} onChange={setProducts}
              options={productOptions} style={{ minWidth: 280 }} maxTagCount={4} />
            {periodSel(tsDays, setTsDays)}
            <Text>단위:</Text>
            <Select value={tsGran} onChange={setTsGran} style={{ minWidth: 100 }}
              options={[{ value: 'month', label: '월' }, { value: 'week', label: '주' }, { value: 'day', label: '일' }]} />
            <Text type="secondary">하단 슬라이더로 구간 확대</Text>
          </Space>
          <ChartPanel zoom loader={() => fetchSentimentTimeseries(products, tsDays, tsGran)}
            deps={[products, tsDays, tsGran]} />
        </Card>
      ),
    },
    {
      key: 'country', label: '국가 분포',
      children: (
        <Card>
          <Space style={{ marginBottom: 12 }} wrap>
            <Text>제품:</Text>
            <Select value={filterProduct ?? ''} onChange={(v) => setFilterProduct(v || undefined)}
              options={filterOptions} style={{ minWidth: 140 }} />
            {periodSel(countryDays, setCountryDays)}
            <Segmented value={countryView} onChange={(v) => setCountryView(v as 'bar' | 'map')}
              options={[{ value: 'bar', label: '막대' }, { value: 'map', label: '지도' }]} />
            <Text type="secondary">막대 클릭 → 해당국 카테고리</Text>
          </Space>
          {countryView === 'bar' ? (
            <ChartPanel height={500}
              loader={() => fetchCountryDistribution(filterProduct, 15, countryDays)}
              deps={[filterProduct, countryDays]}
              onClick={(name) => setDrillCountry(name)} />
          ) : (
            <WorldChoropleth countries={mapData} mode="count" height={500}
              onSelect={(a3) => setDrillCountry(alpha3ToAlpha2(a3))} />
          )}
        </Card>
      ),
    },
    {
      key: 'category', label: '카테고리 분포',
      children: (
        <Card>
          <Space style={{ marginBottom: 12 }} wrap>
            <Text>제품:</Text>
            <Select value={filterProduct ?? ''} onChange={(v) => setFilterProduct(v || undefined)}
              options={filterOptions} style={{ minWidth: 140 }} />
            {periodSel(categoryDays, setCategoryDays)}
            {drillCountry && (
              <Button size="small" onClick={() => setDrillCountry(undefined)}>
                국가 필터 해제: {drillCountry} ✕
              </Button>
            )}
          </Space>
          <ChartPanel height={500}
            loader={() => fetchCategoryDistribution(filterProduct, 15, categoryDays, drillCountry)}
            deps={[filterProduct, categoryDays, drillCountry]} />
        </Card>
      ),
    },
    {
      key: 'crisis', label: '위기 타임라인',
      children: (
        <Card>
          <Space style={{ marginBottom: 12 }} wrap>
            <Text>사례:</Text>
            <Select value={crisisCase} onChange={setCrisisCase}
              options={CRISIS_CASES} style={{ minWidth: 220 }} />
            <Text type="secondary">하단 슬라이더로 구간 확대 · 사건 기간 고정</Text>
          </Space>
          <ChartPanel zoom loader={() => fetchCrisisTimeline(crisisCase)} deps={[crisisCase]} />
        </Card>
      ),
    },
    {
      key: 'network', label: '키워드 네트워크',
      children: (
        <Card>
          <Space style={{ marginBottom: 12 }} wrap>
            <Text>제품:</Text>
            <Select value={filterProduct ?? ''} onChange={(v) => setFilterProduct(v || undefined)}
              options={filterOptions} style={{ minWidth: 140 }} />
            {periodSel(netDays, setNetDays)}
            <Text type="secondary">노드 클릭 → 키워드 상세</Text>
          </Space>
          <ChartPanel height={560}
            loader={() => fetchKeywordNetwork(filterProduct, netDays, 3, 40)}
            deps={[filterProduct, netDays]}
            onClick={(name, raw) => {
              const r = raw as { nodes?: Array<{ id: string; value: number; lang?: string }> };
              const node = r.nodes?.find((n) => n.id === name);
              setDrawer({
                open: true, title: `키워드: ${name}`,
                body: node ? `빈도 ${node.value}건 · 언어 ${node.lang ?? '-'}` : '상세 없음',
              });
            }} />
        </Card>
      ),
    },
  ];

  return (
    <div style={{ padding: 16 }}>
      <Typography.Title level={3}>차트 갤러리</Typography.Title>
      <Text type="secondary">
        시간(기간·단위·줌) · 제품 · 지리(막대/지도) · 항목(클릭 드릴다운) 으로
        거시↔미시 자유 탐색. backend <code>/api/v1/charts/*</code> · voc_active 정합.
      </Text>
      <Tabs items={items} style={{ marginTop: 16 }} />
      <Drawer open={drawer.open} title={drawer.title} onClose={() => setDrawer((s) => ({ ...s, open: false }))}>
        <p>{drawer.body}</p>
      </Drawer>
    </div>
  );
}
