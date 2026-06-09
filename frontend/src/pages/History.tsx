// R10 트랙 B — /history 페이지 인터랙티브 강화.
// 6 카드 구성:
//  1) GalaxyMasterTimeline       — Galaxy 17년 통합 timeline (5 시리즈 누적)  [B1]
//  2) GalaxyTimelineCard         — 시리즈별 line+bar (호버 detail 강화)         [B2]
//  3) CrisisLearningCard         — 5 사례 사건 학습 카드                       [B3]
//  4) SeriesHeatmap              — 세대×sentiment heatmap                     [B4]
//  5) SeriesComparisonCard       — 세대 line 비교 (기존 유지)
//  6) LegacyDistributionCard     — 클릭 시 LegacyDrawer 표시                   [B5]
import { useMemo, useState } from 'react';
import {
  Alert,
  Card,
  Col,
  Empty,
  Row,
  Segmented,
  Space,
  Spin,
  Statistic,
  Typography,
} from 'antd';
import { useQuery } from '@tanstack/react-query';
import ReactECharts from 'echarts-for-react';
import {
  fetchGalaxyTimeline,
  fetchSeriesComparison,
  legacyDistribution,
  timelineEchartsData,
  totalVoc7d,
  SERIES_OPTIONS,
} from '../services/historyApi';
import { makeBaseOption, palette } from '../utils/chartTheme';
import GalaxyMasterTimeline from '../components/history/GalaxyMasterTimeline';
import CrisisLearningCard from '../components/history/CrisisLearningCard';
import SeriesHeatmap from '../components/history/SeriesHeatmap';
import LegacyDrawer from '../components/history/LegacyDrawer';

const { Title, Paragraph } = Typography;

function GalaxyTimelineCard() {
  const [series, setSeries] = useState<string>('S');
  const { data, isLoading, isError } = useQuery({
    queryKey: ['history-timeline', series],
    queryFn: () => fetchGalaxyTimeline(series),
    staleTime: 5 * 60_000,
  });

  // B2: 호버 detail — 모델별 released_at / 7d voc / neg_rate / 매칭 사이트 수 (DB)
  const echartsOpt = useMemo(() => {
    if (!data) return null;
    const e = timelineEchartsData(data.models);
    const base = makeBaseOption({ withDataZoom: true, withToolbox: true, tooltipMode: 'off' });
    // tooltip override — 모델 상세 한 줄.
    return {
      ...base,
      tooltip: {
        trigger: 'axis',
        axisPointer: { type: 'cross' },
        formatter: (params: Array<{ axisValue?: string }>) => {
          const code = params?.[0]?.axisValue ?? '';
          const m = data.models.find((x) => x.code === code);
          if (!m) return String(code);
          return [
            `<b>${m.code}</b> ${m.name}`,
            m.released_at ? `출시: ${m.released_at}` : '출시일 미상',
            `7일 voc: ${m.voc_7d_count.toLocaleString()}건`,
            `neg_rate: ${(m.neg_rate * 100).toFixed(1)}%`,
            `180일 peak: ${m.peak_count.toLocaleString()}건`,
          ].join('<br/>');
        },
      },
      legend: { data: ['총 voc', '출시 7일 voc', 'peak (180d)', 'neg_rate %'], top: 4 },
      xAxis: {
        type: 'category',
        data: e.codes,
        axisLabel: { fontSize: 10, rotate: 35 },
      },
      yAxis: [
        { type: 'value', name: 'voc', position: 'left' },
        {
          type: 'value', name: 'neg %', position: 'right',
          min: 0, max: 100, axisLabel: { formatter: '{value}%' },
        },
      ],
      series: [
        { name: '총 voc', type: 'bar', data: e.totals, itemStyle: { color: '#bbbbbb' } },
        { name: '출시 7일 voc', type: 'bar', data: e.counts, itemStyle: { color: palette.primary } },
        { name: 'peak (180d)', type: 'line', smooth: true, data: e.peaks,
          itemStyle: { color: palette.accent } },
        { name: 'neg_rate %', type: 'line', smooth: true, yAxisIndex: 1, data: e.negRates,
          itemStyle: { color: palette.negative } },
      ],
    };
  }, [data]);

  return (
    <Card
      data-testid="galaxy-timeline-card"
      size="small"
      title="Galaxy 시리즈 라이프사이클"
      extra={
        <Segmented
          options={SERIES_OPTIONS.map((s) => ({ label: s.label, value: s.key }))}
          value={series}
          onChange={(v) => setSeries(String(v))}
          data-testid="series-segmented"
        />
      }
    >
      {isError && <Alert type="error" message="timeline 로드 실패" />}
      {isLoading && <Spin />}
      {data && data.models.length === 0 && <Empty description="모델 없음" />}
      {data && data.models.length > 0 && (
        <>
          <Space size={16} style={{ marginBottom: 8 }}>
            <Statistic title="모델 수" value={data.models.length} />
            <Statistic title="7일 합계 voc" value={totalVoc7d(data.models)} />
          </Space>
          {echartsOpt && (
            <ReactECharts
              option={echartsOpt}
              style={{ height: 360 }}
              notMerge
              lazyUpdate
            />
          )}
        </>
      )}
    </Card>
  );
}

function SeriesComparisonCard() {
  const { data, isLoading, isError } = useQuery({
    queryKey: ['history-compare'],
    queryFn: () => fetchSeriesComparison(['S', 'Note', 'Z']),
    staleTime: 10 * 60_000,
  });
  const opt = useMemo(() => {
    if (!data) return null;
    const base = makeBaseOption({ withDataZoom: true, withToolbox: true });
    const maxGen = Math.max(
      1,
      ...data.series_list.map((s) => s.points.length),
    );
    const xs = Array.from({ length: maxGen }, (_, i) => `세대 ${i + 1}`);
    return {
      ...base,
      legend: { data: data.series_list.map((s) => s.label), top: 4 },
      xAxis: { type: 'category', data: xs },
      yAxis: { type: 'value', name: 'voc count' },
      series: data.series_list.map((s, i) => ({
        name: s.label,
        type: 'line',
        smooth: true,
        data: s.points.map((p) => p.count),
        itemStyle: {
          color: [palette.primary, palette.accent, palette.positive][i % 3],
        },
      })),
    };
  }, [data]);

  return (
    <Card data-testid="series-comparison-card" size="small" title="시리즈 세대 비교 (S vs Note vs Z)">
      {isError && <Alert type="error" message="비교 로드 실패" />}
      {isLoading && <Spin />}
      {data && opt && (
        <ReactECharts option={opt} style={{ height: 320 }} notMerge lazyUpdate />
      )}
    </Card>
  );
}

function LegacyDistributionCard() {
  // GS, GN 시리즈를 합쳐 2020 이전 모델만 추출.
  const { data: gs } = useQuery({
    queryKey: ['history-timeline', 'S'],
    queryFn: () => fetchGalaxyTimeline('S'),
    staleTime: 5 * 60_000,
  });
  const { data: gn } = useQuery({
    queryKey: ['history-timeline', 'Note'],
    queryFn: () => fetchGalaxyTimeline('Note'),
    staleTime: 5 * 60_000,
  });
  const merged = useMemo(() => {
    const all = [...(gs?.models ?? []), ...(gn?.models ?? [])];
    return legacyDistribution(all, 2020).slice(0, 20);
  }, [gs, gn]);

  // B5: 클릭 시 drawer
  const [picked, setPicked] = useState<{ code: string; name: string } | null>(null);

  const opt = useMemo(() => {
    if (merged.length === 0) return null;
    const base = makeBaseOption({ withDataZoom: false, withToolbox: false });
    return {
      ...base,
      grid: { top: 16, right: 24, bottom: 24, left: 110 },
      xAxis: { type: 'value' },
      yAxis: {
        type: 'category',
        data: merged.map((m) => m.name),
        inverse: true,
        axisLabel: { fontSize: 11 },
      },
      series: [
        {
          type: 'bar',
          data: merged.map((m) => m.total),
          itemStyle: { color: palette.primary },
          label: { show: true, position: 'right', fontSize: 10 },
        },
      ],
    };
  }, [merged]);

  const onChartClick = (params: { dataIndex?: number }) => {
    const idx = params?.dataIndex;
    if (typeof idx !== 'number') return;
    const m = merged[idx];
    if (m) setPicked({ code: m.code, name: m.name });
  };

  return (
    <Card data-testid="legacy-distribution-card" size="small" title="옛 모델 voc 분포 (~2019, 클릭 → 샘플)">
      {merged.length === 0 ? <Empty description="데이터 없음" /> : (
        <ReactECharts
          option={opt!}
          style={{ height: 380 }}
          notMerge
          lazyUpdate
          onEvents={{ click: onChartClick }}
        />
      )}
      <LegacyDrawer
        code={picked?.code ?? null}
        name={picked?.name ?? null}
        onClose={() => setPicked(null)}
      />
    </Card>
  );
}

export default function History() {
  return (
    <div data-testid="history-page">
      <Title level={3} style={{ marginTop: 0 }}>
        17년 라이프사이클
      </Title>
      <Paragraph type="secondary">
        Galaxy 17년 (2010~) 데이터 — 통합 timeline, 출시 7일 voc 라이프사이클,
        5종 위기 사례 학습 카드, 시리즈 세대 sentiment heatmap, 옛 모델 voc 드릴다운.
      </Paragraph>
      <Row gutter={[16, 16]}>
        <Col xs={24}>
          <GalaxyMasterTimeline />
        </Col>
        <Col xs={24}>
          <GalaxyTimelineCard />
        </Col>
        <Col xs={24}>
          <CrisisLearningCard />
        </Col>
        <Col xs={24} lg={12}>
          <SeriesHeatmap />
        </Col>
        <Col xs={24} lg={12}>
          <SeriesComparisonCard />
        </Col>
        <Col xs={24}>
          <LegacyDistributionCard />
        </Col>
      </Row>
    </div>
  );
}
