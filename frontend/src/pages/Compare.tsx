// P5 R7 (키-없이 라운드) — Compare 페이지 보강.
// 4 제품 side-by-side 분석: KPI 행 + 시계열 line + 카테고리 stacked bar + top 부정 표.
// 데이터는 fetchCompareData 단일 호출(내부 병렬)로 통일.
import { useMemo } from 'react';
import {
  Card,
  Col,
  Empty,
  Row,
  Select,
  Space,
  Spin,
  Statistic,
  Table,
  Typography,
} from 'antd';
import ReactECharts from 'echarts-for-react';
import { useQuery } from '@tanstack/react-query';
import { useFilterStore } from '../stores/useFilterStore';
import { effectivePeriodDays } from '../types/filters';
import { useProductOptions } from '../hooks/useFilterMeta';
import { fetchCompareData } from '../services/compareApi';
import {
  buildCategoryChart,
  buildIssueTable,
  buildKpiRows,
  buildTrendChart,
} from '../components/compare/compareUtils';
import {
  makeBaseOption,
  seriesColors,
  formatCount,
  formatPct,
  formatSent,
} from '../utils/chartTheme';
import CompareLLMCard from '../components/compare/CompareLLMCard';

const { Title, Paragraph, Text } = Typography;

export default function Compare() {
  const filters = useFilterStore();
  const period = effectivePeriodDays(filters);
  const products = filters.products.slice(0, 4);
  const { data: productMeta } = useProductOptions();

  const productOptions = useMemo(
    () => (productMeta ?? []).map((p) => ({ label: p.name, value: p.code })),
    [productMeta],
  );

  const { data, isLoading } = useQuery({
    queryKey: ['compare', 'data', products.join(','), period],
    queryFn: () => fetchCompareData(products, period),
    enabled: products.length >= 2,
    staleTime: 5 * 60_000,
  });

  // 파생 — useMemo 로 옵션 객체를 안정화 (echarts 재마운트 회피)
  const kpis = useMemo(() => (data ? buildKpiRows(data) : []), [data]);
  const trendOpt = useMemo(() => {
    if (!data) return null;
    const { xAxis, series } = buildTrendChart(data);
    const base = makeBaseOption({ withDataZoom: { dataPoints: xAxis.length, threshold: 30 } });
    return {
      ...base,
      xAxis: { type: 'category', data: xAxis, axisLabel: { fontSize: 11 } },
      yAxis: { type: 'value', axisLabel: { fontSize: 11 } },
      series: series.map((s, i) => ({
        name: s.name,
        type: 'line',
        data: s.data,
        smooth: true,
        connectNulls: true,
        showSymbol: false,
        lineStyle: { width: 2, color: seriesColors[i % seriesColors.length] },
        itemStyle: { color: seriesColors[i % seriesColors.length] },
      })),
    };
  }, [data]);

  const catOpt = useMemo(() => {
    if (!data) return null;
    const { categories, series } = buildCategoryChart(data);
    const base = makeBaseOption({});
    return {
      ...base,
      xAxis: { type: 'category', data: data.productCodes, axisLabel: { fontSize: 11 } },
      yAxis: { type: 'value', axisLabel: { fontSize: 11 } },
      series: series.map((s, i) => ({
        name: s.name,
        type: 'bar',
        stack: 'cat',
        data: s.data,
        itemStyle: { color: seriesColors[i % seriesColors.length] },
      })),
      legend: { bottom: 0, type: 'scroll', textStyle: { fontSize: 11 } },
      // categories 갯수가 적으면 토큰만 — 추후 확장
      grid: { top: 20, right: 20, bottom: 50, left: 50, containLabel: true },
      // categories 자체는 series.name 으로 노출
      // (categories 변수는 series 매핑에 사용됨)
      _categoryKeys: categories, // 디버그/검증 용 (echarts 무시)
    };
  }, [data]);

  const issueRows = useMemo(() => (data ? buildIssueTable(data) : []), [data]);

  const columns = useMemo(() => {
    const cols: Array<Record<string, unknown>> = [
      { title: '순위', dataIndex: 'rank', key: 'rank', width: 60 },
    ];
    for (const code of products) {
      cols.push({
        title: code,
        dataIndex: code,
        key: code,
        render: (cell: { label: string; count: number; negRate: number } | null | undefined) => {
          if (!cell) return <Text type="secondary">–</Text>;
          return (
            <div>
              <div>{cell.label}</div>
              <Text type="secondary" style={{ fontSize: 11 }}>
                {formatCount(cell.count)} · 부정 {formatPct(cell.negRate, 0)}
              </Text>
            </div>
          );
        },
      });
    }
    return cols;
  }, [products]);

  // table dataSource — cells 를 펼쳐 컬럼 dataIndex 와 매칭
  const dataSource = useMemo(
    () =>
      issueRows.map((r) => {
        const row: Record<string, unknown> = { key: r.rank, rank: r.rank };
        for (const code of products) row[code] = r.cells[code];
        return row;
      }),
    [issueRows, products],
  );

  return (
    <div>
      <Title level={3} style={{ marginTop: 0 }}>제품 비교</Title>
      <Paragraph type="secondary">
        제품 2~4개를 선택하면 KPI · 시계열 · 카테고리 · 부정 키워드를 같은 화면에서 비교합니다 (최근 {period}일).
      </Paragraph>

      <Space style={{ marginBottom: 16 }} wrap>
        <Select
          mode="multiple"
          allowClear
          style={{ minWidth: 360 }}
          placeholder="비교할 제품 2~4개 선택"
          value={products}
          onChange={(v) => filters.setProducts(v.slice(0, 4))}
          options={productOptions}
          maxTagCount="responsive"
          data-testid="compare-product-select"
        />
        <Select
          style={{ minWidth: 120 }}
          value={filters.periodDays || 30}
          onChange={(n) => filters.setPeriodDays(n)}
          options={[
            { label: '최근 7일', value: 7 },
            { label: '최근 14일', value: 14 },
            { label: '최근 30일', value: 30 },
            { label: '최근 90일', value: 90 },
          ]}
          data-testid="compare-period-select"
        />
      </Space>

      {products.length < 2 ? (
        <Card>
          <Empty description="최소 2개 제품을 선택하세요" data-testid="compare-empty" />
        </Card>
      ) : isLoading ? (
        <Card>
          <Spin />
        </Card>
      ) : !data ? (
        <Card>
          <Empty description="데이터 로드 실패" />
        </Card>
      ) : (
        <div data-testid="compare-grid">
          {/* KPI 4행 — 행마다 제품 N개 카드 */}
          <Row gutter={[12, 12]} style={{ marginBottom: 12 }}>
            {kpis.map((k) => (
              <Col xs={24} sm={12} md={Math.max(6, Math.floor(24 / kpis.length))} key={k.product}>
                <Card size="small" title={k.product}>
                  <Row gutter={[8, 8]}>
                    <Col span={12}>
                      <Statistic title="24h VOC" value={k.voc24h} suffix="건" />
                    </Col>
                    <Col span={12}>
                      <Statistic
                        title="7일 감성"
                        value={k.sent7d === null ? '-' : formatSent(k.sent7d)}
                      />
                    </Col>
                    <Col span={12}>
                      <Statistic
                        title="부정 비율"
                        value={k.negRate.toFixed(1)}
                        suffix="%"
                      />
                    </Col>
                    <Col span={12}>
                      <Statistic title="활발 사이트" value={k.activeSites} suffix="개" />
                    </Col>
                  </Row>
                </Card>
              </Col>
            ))}
          </Row>

          <Row gutter={[12, 12]}>
            <Col xs={24} lg={14}>
              <Card size="small" title="VOC 시계열 (제품별)" bodyStyle={{ height: 320 }}>
                {trendOpt && (trendOpt.xAxis as { data?: string[] }).data?.length ? (
                  <ReactECharts option={trendOpt} style={{ height: 280 }} />
                ) : (
                  <Empty description="시계열 데이터 없음" />
                )}
              </Card>
            </Col>
            <Col xs={24} lg={10}>
              <Card size="small" title="카테고리 분포 (Top 8 stacked)" bodyStyle={{ height: 320 }}>
                {catOpt && (catOpt.series as unknown[]).length ? (
                  <ReactECharts option={catOpt} style={{ height: 280 }} />
                ) : (
                  <Empty description="카테고리 데이터 없음" />
                )}
              </Card>
            </Col>
          </Row>

          <Card
            size="small"
            title="부정 키워드 Top 5 (제품별)"
            style={{ marginTop: 12 }}
            data-testid="compare-issue-table"
          >
            <Table
              size="small"
              pagination={false}
              dataSource={dataSource}
              columns={columns as never}
            />
          </Card>

          {/* 트랙 D — LLM(14b) 기반 비교 분석 narrative */}
          <div style={{ marginTop: 12 }}>
            <CompareLLMCard products={products} periodDays={period} />
          </div>
        </div>
      )}
    </div>
  );
}
