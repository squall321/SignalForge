// P5 R7 — Compare 페이지 단위 테스트.
// jsdom 미설정 환경이라 (1) 선택 슬라이스 4개 제한 (2) compareUtils 변환 로직 두 가지를 검증.
//
// (1) Compare.tsx 의 setProducts(v.slice(0, 4)) 와 동일한 슬라이스를 단독 함수로 표현.
// (2) buildKpiRows / buildCategoryChart / buildIssueTable / buildTrendChart 가
//     실제 API 응답 모킹값으로 올바른 구조를 만드는지 검증.

import { describe, it, expect } from 'vitest';
import {
  buildCategoryChart,
  buildIssueTable,
  buildKpiRows,
  buildTrendChart,
} from '../components/compare/compareUtils';
import type { CompareData } from '../services/compareApi';

// 슬라이스 규칙 (페이지에서 setProducts(v.slice(0,4)))
function capSelection(v: string[]): string[] {
  return v.slice(0, 4);
}

describe('Compare 페이지 — 제품 선택 4개 제한', () => {
  it('5 개 입력 → 앞 4 개만 남는다', () => {
    expect(capSelection(['A', 'B', 'C', 'D', 'E'])).toEqual(['A', 'B', 'C', 'D']);
  });
  it('2 개 입력 → 그대로 통과', () => {
    expect(capSelection(['A', 'B'])).toEqual(['A', 'B']);
  });
  it('빈 입력 → 빈 배열', () => {
    expect(capSelection([])).toEqual([]);
  });
});

// 모킹 데이터 — 두 제품 비교 케이스.
const MOCK: CompareData = {
  productCodes: ['GS25', 'GS26'],
  periodDays: 30,
  // backend 는 % 단위 (0..100). mock 도 동일.
  sentiment7d: [
    {
      product_code: 'GS25',
      product_name: 'Galaxy S25',
      total: 100,
      positive: 30,
      negative: 50,
      neutral: 20,
      positive_rate: 30,
      negative_rate: 50,
      avg_score: -0.12,
    },
    {
      product_code: 'GS26',
      product_name: 'Galaxy S26',
      total: 80,
      positive: 40,
      negative: 20,
      neutral: 20,
      positive_rate: 50,
      negative_rate: 25,
      avg_score: 0.18,
    },
  ],
  categories: [
    {
      product_code: 'GS25',
      product_name: 'Galaxy S25',
      total: 100,
      categories: [
        { category: 'battery', count: 40 },
        { category: 'camera', count: 30 },
        { category: 'display', count: 10 },
      ],
    },
    {
      product_code: 'GS26',
      product_name: 'Galaxy S26',
      total: 80,
      categories: [
        { category: 'battery', count: 10 },
        { category: 'camera', count: 50 },
        { category: 'price', count: 20 },
      ],
    },
  ],
  overviews: {
    GS25: {
      period: '7d',
      filters: {},
      kpis: { total_voc: 700, neg_rate: 50, top_product: 'GS25', alert_count: 1 },
      trend14d: [
        { date: '2026-05-30', count: 50, sent_avg: 0.0 },
        { date: '2026-05-31', count: 60, sent_avg: -0.1 },
        { date: '2026-06-01', count: 90, sent_avg: -0.2 },
      ],
      top_sites: [
        { code: 'reddit', count: 200, sent_avg: -0.1 },
        { code: 'xda', count: 150, sent_avg: 0.0 },
        { code: 'gsmarena', count: 100, sent_avg: 0.1 },
      ],
    },
    GS26: {
      period: '7d',
      filters: {},
      kpis: { total_voc: 500, neg_rate: 25, top_product: 'GS26', alert_count: 0 },
      trend14d: [
        { date: '2026-05-30', count: 30, sent_avg: 0.1 },
        { date: '2026-06-01', count: 70, sent_avg: 0.2 },
      ],
      top_sites: [
        { code: 'reddit', count: 180, sent_avg: 0.1 },
        { code: 'youtube', count: 120, sent_avg: 0.2 },
      ],
    },
  },
  trends: {
    GS25: {
      product_code: 'GS25',
      granularity: 'day',
      data: [
        { date: '2026-05-30', positive: 10, negative: 30, neutral: 10, avg_score: -0.1 },
        { date: '2026-05-31', positive: 20, negative: 30, neutral: 10, avg_score: 0.0 },
      ],
    },
    GS26: {
      product_code: 'GS26',
      granularity: 'day',
      data: [
        { date: '2026-05-31', positive: 25, negative: 10, neutral: 15, avg_score: 0.2 },
        { date: '2026-06-01', positive: 30, negative: 10, neutral: 30, avg_score: 0.25 },
      ],
    },
  },
  topIssues: {
    GS25: {
      product_code: 'GS25',
      period_days: 30,
      issues: [
        { rank: 1, category: 'battery', name_ko: '배터리', count: 40, negative_rate: 60, sample_texts: [] },
        { rank: 2, category: 'camera', name_ko: '카메라', count: 30, negative_rate: 40, sample_texts: [] },
      ],
    },
    GS26: {
      product_code: 'GS26',
      period_days: 30,
      issues: [
        { rank: 1, category: 'camera', name_ko: '카메라', count: 50, negative_rate: 30, sample_texts: [] },
      ],
    },
  },
};

describe('buildKpiRows — KPI 4행 빌더', () => {
  it('제품 순서대로 KPI 행 생성', () => {
    const rows = buildKpiRows(MOCK);
    expect(rows.length).toBe(2);
    expect(rows[0].product).toBe('GS25');
    expect(rows[1].product).toBe('GS26');
  });
  it('24h VOC 는 overview.trend14d 마지막 점 count', () => {
    const rows = buildKpiRows(MOCK);
    expect(rows[0].voc24h).toBe(90); // GS25 마지막 = 90
    expect(rows[1].voc24h).toBe(70); // GS26 마지막 = 70
  });
  it('7일 감성 / 부정 비율 / 활발 사이트 수', () => {
    const rows = buildKpiRows(MOCK);
    expect(rows[0].sent7d).toBeCloseTo(-0.12, 5);
    expect(rows[0].negRate).toBeCloseTo(50, 5); // backend 이미 % 단위
    expect(rows[0].activeSites).toBe(3);
    expect(rows[1].sent7d).toBeCloseTo(0.18, 5);
    expect(rows[1].negRate).toBeCloseTo(25, 5);
    expect(rows[1].activeSites).toBe(2);
  });
  it('overview 없는 제품 → 0/null fallback', () => {
    const stub: CompareData = { ...MOCK, productCodes: ['GS99'], overviews: { GS99: null }, sentiment7d: [], categories: [], trends: { GS99: null }, topIssues: { GS99: null } };
    const rows = buildKpiRows(stub);
    expect(rows[0].voc24h).toBe(0);
    expect(rows[0].sent7d).toBeNull();
    expect(rows[0].activeSites).toBe(0);
  });
});

describe('buildTrendChart — 시계열 라인', () => {
  it('xAxis 는 모든 제품 date union, 정렬', () => {
    const ch = buildTrendChart(MOCK);
    expect(ch.xAxis).toEqual(['2026-05-30', '2026-05-31', '2026-06-01']);
  });
  it('series 는 제품 수만큼, 누락 날짜는 null', () => {
    const ch = buildTrendChart(MOCK);
    expect(ch.series.length).toBe(2);
    expect(ch.series[0].name).toBe('GS25');
    // GS25: 05-30 = 50, 05-31 = 60, 06-01 = null
    expect(ch.series[0].data).toEqual([50, 60, null]);
    // GS26: 05-30 = null, 05-31 = 50, 06-01 = 70
    expect(ch.series[1].data).toEqual([null, 50, 70]);
  });
});

describe('buildCategoryChart — stacked bar', () => {
  it('카테고리는 전체 합 기준 상위 정렬', () => {
    const ch = buildCategoryChart(MOCK);
    // camera (30+50=80) > battery (40+10=50) > price 20 > display 10
    expect(ch.categories[0]).toBe('camera');
    expect(ch.categories[1]).toBe('battery');
  });
  it('series.data 는 제품 순서, 누락은 0', () => {
    const ch = buildCategoryChart(MOCK);
    const camera = ch.series.find((s) => s.name === 'camera')!;
    expect(camera.data).toEqual([30, 50]);
    const price = ch.series.find((s) => s.name === 'price')!;
    expect(price.data).toEqual([0, 20]); // GS25 에는 price 없음
  });
});

describe('buildIssueTable — 부정 키워드 표', () => {
  it('항상 5 행 (rank 1..5), 부족분은 null 셀', () => {
    const rows = buildIssueTable(MOCK);
    expect(rows.length).toBe(5);
    expect(rows[0].rank).toBe(1);
    expect(rows[4].rank).toBe(5);
  });
  it('rank 1 셀 — 라벨/카운트/부정률 매칭', () => {
    const rows = buildIssueTable(MOCK);
    const c1 = rows[0].cells['GS25']!;
    expect(c1.label).toBe('배터리');
    expect(c1.count).toBe(40);
    expect(c1.negRate).toBeCloseTo(60, 5); // backend 이미 % 단위
  });
  it('데이터 없는 셀은 null', () => {
    const rows = buildIssueTable(MOCK);
    expect(rows[1].cells['GS26']).toBeNull(); // GS26 은 issue 1 개뿐
    expect(rows[2].cells['GS25']).toBeNull();
  });
});
