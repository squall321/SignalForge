// P5 R6 트랙 A — Dashboard KPI 유틸 단위 테스트.
// vitest node 환경 (jsdom 미사용) — kpiUtils 의 순수 함수 동작만 검증.
//
// 검증 케이스:
// 1) KPI Δ% 계산 (전반 7d vs 후반 7d 트렌드) — total_voc 화살표가 올바른 방향인지
// 2) 부정 비율(neg_rate) 처럼 "올라가면 나쁨" 지표의 색 결정
// 3) fallback narrative — LLM 미입력 환경에서도 사람이 읽을 수 있는 문장 생성
import { describe, it, expect } from 'vitest';
import type { DashboardKPIs, TrendPoint } from '../types/dashboard';
import {
  buildFallbackSignal,
  computeKPIDeltas,
  deltaColor,
  deltaDirection,
  pctChange,
} from '../components/dashboard/kpiUtils';

const TREND_GROWING: TrendPoint[] = [
  // 직전 7일 (count=100 each) → 합 700
  ...Array.from({ length: 7 }, (_, i) => ({
    date: `2026-05-${20 + i}`,
    count: 100,
    sent_avg: 0.1,
  })),
  // 최근 7일 (count=200 each) → 합 1400, 즉 +100%
  ...Array.from({ length: 7 }, (_, i) => ({
    date: `2026-05-${27 + i}`,
    count: 200,
    sent_avg: -0.1,
  })),
];

const KPIS_SAMPLE: DashboardKPIs = {
  total_voc: 2100,
  neg_rate: 32.5,
  top_product: 'GS26U',
  alert_count: 2,
};

describe('pctChange / deltaDirection', () => {
  it('이전값 0 → null', () => {
    expect(pctChange(100, 0)).toBeNull();
  });
  it('+100% 상승 케이스', () => {
    expect(pctChange(200, 100)).toBeCloseTo(100, 5);
  });
  it('flat 임계(±0.5%)', () => {
    expect(deltaDirection(0.3)).toBe('flat');
    expect(deltaDirection(-0.6)).toBe('down');
    expect(deltaDirection(0.6)).toBe('up');
    expect(deltaDirection(null)).toBe('flat');
  });
});

describe('deltaColor (색 결정 — KPI 카드 화살표 색)', () => {
  it('goodWhenUp=true & up → 초록(#237804)', () => {
    expect(deltaColor('up', true)).toBe('#237804');
  });
  it('goodWhenUp=true & down → 빨강(#cf1322)', () => {
    expect(deltaColor('down', true)).toBe('#cf1322');
  });
  it('goodWhenUp=false (부정률 등) & up → 빨강(#cf1322)', () => {
    expect(deltaColor('up', false)).toBe('#cf1322');
  });
  it('goodWhenUp=false & down → 초록(#237804)', () => {
    expect(deltaColor('down', false)).toBe('#237804');
  });
  it('flat → 회색(#8c8c8c)', () => {
    expect(deltaColor('flat', true)).toBe('#8c8c8c');
    expect(deltaColor('flat', false)).toBe('#8c8c8c');
  });
});

describe('computeKPIDeltas (트렌드 14d → KPI 변화율)', () => {
  it('total_voc — 후반 7일이 2배일 때 +100% 근사', () => {
    const d = computeKPIDeltas(TREND_GROWING, KPIS_SAMPLE);
    expect(d.total_voc).not.toBeNull();
    expect(d.total_voc as number).toBeCloseTo(100, 0);
  });
  it('sent_avg 가 +0.1 → -0.1 로 떨어지면 neg_rate Δ 가 양수(부정 증가)', () => {
    const d = computeKPIDeltas(TREND_GROWING, KPIS_SAMPLE);
    expect(d.neg_rate).not.toBeNull();
    expect((d.neg_rate as number) > 0).toBe(true);
  });
  it('빈 트렌드 → 모두 null', () => {
    const d = computeKPIDeltas([], KPIS_SAMPLE);
    expect(d.total_voc).toBeNull();
    expect(d.neg_rate).toBeNull();
    expect(d.alert_count).toBeNull();
  });
});

describe('buildFallbackSignal (LLM 미사용 fallback narrative)', () => {
  it('overview null → 로드 대기 메시지', () => {
    const s = buildFallbackSignal(null);
    expect(s.headline).toContain('로드 대기');
    expect(s.bullets.length).toBeGreaterThan(0);
  });

  it('급증 케이스(>=10% 증가) → headline 에 "급증" 포함', () => {
    const s = buildFallbackSignal({
      kpis: KPIS_SAMPLE,
      trend14d: TREND_GROWING,
      top_sites: [{ code: 'reddit', count: 1234, sent_avg: -0.2 }],
    });
    expect(s.headline).toContain('급증');
    // bullets 안에 top product / top site 가 모두 노출되어야 한다.
    expect(s.bullets.some((b) => b.includes('GS26U'))).toBe(true);
    expect(s.bullets.some((b) => b.includes('reddit'))).toBe(true);
  });

  it('알림 임계 초과 제품 > 0 → bullet 포함', () => {
    const s = buildFallbackSignal({
      kpis: { ...KPIS_SAMPLE, alert_count: 3 },
      trend14d: TREND_GROWING,
      top_sites: [],
    });
    expect(s.bullets.some((b) => b.includes('알림'))).toBe(true);
  });
});
