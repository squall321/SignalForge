// R9 트랙 A — galaxy-history 3 endpoint 클라이언트 + 순수 유틸.
//
// 페이지 (/history) 는 4 카드를 한 화면에 표시 — Timeline / Crisis / Comparison /
// Legacy distribution. API 호출과 가공 유틸을 한 파일로 묶어 페이지 컴포넌트를 슬림하게.
import api from './api';
import type {
  CrisisCase,
  CrisisCasesResponse,
  GalaxyTimelineModel,
  GalaxyTimelineResponse,
  SeriesComparisonResponse,
} from '../types/deep';

export interface SeriesOption {
  key: string;        // backend 알리어스 ('S', 'Note', 'Z', 'Fold', 'Flip', 'Watch', 'Buds')
  label: string;      // 화면 표시명
}

export const SERIES_OPTIONS: SeriesOption[] = [
  { key: 'S', label: 'Galaxy S' },
  { key: 'Note', label: 'Galaxy Note' },
  { key: 'Z', label: 'Galaxy Z' },
  { key: 'Fold', label: 'Galaxy Z Fold' },
  { key: 'Flip', label: 'Galaxy Z Flip' },
  { key: 'Watch', label: 'Galaxy Watch' },
  { key: 'Buds', label: 'Galaxy Buds' },
];

export async function fetchGalaxyTimeline(
  series: string,
  product?: string,
): Promise<GalaxyTimelineResponse> {
  const { data } = await api.get('/deep/galaxy-timeline', {
    params: { series, ...(product ? { product } : {}) },
  });
  return data;
}

export async function fetchCrisisCases(): Promise<CrisisCasesResponse> {
  const { data } = await api.get('/deep/crisis-cases');
  return data;
}

export async function fetchSeriesComparison(
  series: string[],
): Promise<SeriesComparisonResponse> {
  const { data } = await api.get('/deep/series-comparison', {
    params: { series: series.join(',') },
  });
  return data;
}

// ── 순수 유틸 (단위 테스트 대상) ─────────────────────────────────
// timeline 모델의 voc_7d_count 합 — KPI 표시용.
export function totalVoc7d(models: GalaxyTimelineModel[]): number {
  return models.reduce((s, m) => s + (m.voc_7d_count || 0), 0);
}

// 위기 사례 timeline 데이터를 echarts xAxis/yAxis 한 줄 시리즈로 정규화.
// 빈 timeline → { x: [], y: [] }, 단조 정렬 보장.
export function crisisLineSeries(c: CrisisCase): { x: string[]; y: number[] } {
  const sorted = [...c.timeline].sort((a, b) => a.day.localeCompare(b.day));
  return {
    x: sorted.map((p) => p.day),
    y: sorted.map((p) => p.count),
  };
}

// galaxy-timeline 모델 → echarts series 점 (모델 code 라벨 + count + neg_rate).
// 차트는 단일 시리즈 line + bar.
export function timelineEchartsData(models: GalaxyTimelineModel[]) {
  const sorted = [...models].sort((a, b) =>
    (a.released_at ?? '').localeCompare(b.released_at ?? ''),
  );
  return {
    codes: sorted.map((m) => m.code),
    counts: sorted.map((m) => m.voc_7d_count),
    totals: sorted.map((m) => m.total_count),
    peaks: sorted.map((m) => m.peak_count),
    negRates: sorted.map((m) => Number((m.neg_rate * 100).toFixed(1))),
  };
}

// LegacyDistribution — 옛 모델 (released_at < cutoff) 만 추출, voc total 내림차순.
export function legacyDistribution(
  models: GalaxyTimelineModel[],
  cutoffYear = 2020,
): { code: string; name: string; total: number }[] {
  return models
    .filter((m) => {
      if (!m.released_at) return false;
      const y = parseInt(m.released_at.slice(0, 4), 10);
      return Number.isFinite(y) && y < cutoffYear;
    })
    .map((m) => ({ code: m.code, name: m.name, total: m.total_count }))
    .sort((a, b) => b.total - a.total);
}

// ── R10 트랙 B ─────────────────────────────────────────────────
// B1) 시리즈별 색상 (Master Timeline 누적 line) — Master Timeline 전용.
// 색맹 친화 (Okabe-Ito 변형)를 따른다. label 은 화면 표시명.
export interface MasterSeriesSpec {
  key: string;       // GalaxyTimeline series_code (GS/GN/GZF/GZFL/GW/GB)
  label: string;
  color: string;
}
export const MASTER_SERIES_SPECS: MasterSeriesSpec[] = [
  { key: 'S',     label: 'Galaxy S',     color: '#0072B2' },
  { key: 'Note',  label: 'Galaxy Note',  color: '#E69F00' },
  { key: 'Z',     label: 'Galaxy Z',     color: '#CC79A7' },
  { key: 'Watch', label: 'Galaxy Watch', color: '#009E73' },
  { key: 'Buds',  label: 'Galaxy Buds',  color: '#D55E00' },
];

// Master Timeline 데이터 — 시리즈별 timeline 응답에서 (출시연도, total_count) 점을 누적.
// 동일 연도 다중 모델은 합산. 빈 입력은 빈 배열을 반환.
export function masterTimelineSeries(
  inputs: { key: string; label: string; color: string; models: GalaxyTimelineModel[] }[],
  minYear = 2010,
  maxYear = 2026,
): {
  years: number[];
  seriesData: { name: string; color: string; values: number[] }[];
} {
  const years: number[] = [];
  for (let y = minYear; y <= maxYear; y++) years.push(y);
  const seriesData = inputs.map((inp) => {
    let cum = 0;
    const values = years.map((y) => {
      const yearTotal = inp.models
        .filter((m) => {
          if (!m.released_at) return false;
          const my = parseInt(m.released_at.slice(0, 4), 10);
          return Number.isFinite(my) && my === y;
        })
        .reduce((s, m) => s + (m.total_count || 0), 0);
      cum += yearTotal;
      return cum;
    });
    return { name: inp.label, color: inp.color, values };
  });
  return { years, seriesData };
}

// B4) Series Heatmap — 세대×시점(첫 N 세대 / sentiment) 매트릭스.
// 행: 세대 1..maxGen, 열: 시리즈, 셀: sent_avg (-1..1).
// echarts heatmap 데이터 형식 [colIdx, rowIdx, value].
export function seriesHeatmapCells(
  seriesList: {
    label: string;
    points: { gen: number; sent_avg: number }[];
  }[],
): {
  rows: string[];          // 세대 라벨
  cols: string[];          // 시리즈 라벨
  cells: [number, number, number][];  // [col, row, value]
} {
  const maxGen = Math.max(0, ...seriesList.map((s) => s.points.length));
  const rows = Array.from({ length: maxGen }, (_, i) => `세대 ${i + 1}`);
  const cols = seriesList.map((s) => s.label);
  const cells: [number, number, number][] = [];
  seriesList.forEach((s, colIdx) => {
    s.points.forEach((p) => {
      cells.push([colIdx, p.gen - 1, Number((p.sent_avg ?? 0).toFixed(3))]);
    });
  });
  return { rows, cols, cells };
}
