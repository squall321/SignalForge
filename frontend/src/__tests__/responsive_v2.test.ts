import { describe, it, expect } from 'vitest';
import { resolveSize } from '../utils/useViewport';
import { buildHourlyOption } from '../components/insights/HourlyPatternCard';
import { buildWeekdayOption } from '../components/insights/WeekdayPatternCard';
import { cardBodyStyle, chartHeight } from '../components/common/ResponsiveCard';
import type { HourlyPatternResponse, WeekdayPatternResponse } from '../types/insights';

// 트랙 D — viewport 기반 카드/차트 적응형 단위 테스트.
//
// useViewport() 자체는 React hook 이라 DOM 없이 직접 호출 못함 — 순수 로직인 resolveSize() 만 검증.
// 차트 옵션과 카드 body 스타일은 mobile/desktop 분기에 따른 차이를 픽업.

describe('resolveSize — ScreenMap → ViewportSize', () => {
  it('xs only(=375px viewport) → "xs"', () => {
    expect(resolveSize({ xs: true })).toBe('xs');
  });

  it('xs+sm → "sm" (가장 큰 활성)', () => {
    expect(resolveSize({ xs: true, sm: true })).toBe('sm');
  });

  it('xs..md → "md" (태블릿 경계)', () => {
    expect(resolveSize({ xs: true, sm: true, md: true })).toBe('md');
  });

  it('xs..xl → "xl" (데스크탑)', () => {
    expect(
      resolveSize({ xs: true, sm: true, md: true, lg: true, xl: true }),
    ).toBe('xl');
  });

  it('빈 객체(SSR 초기) → "lg" fallback (깜빡임 방지)', () => {
    expect(resolveSize({})).toBe('lg');
  });
});

describe('cardBodyStyle / chartHeight', () => {
  it('모바일 padding 축소 + mobileHeight 사용', () => {
    const s = cardBodyStyle(true, 300, 220);
    expect(s.height).toBe(220);
    expect(s.padding).toBe(8);
  });

  it('데스크탑 desktopH 사용', () => {
    const s = cardBodyStyle(false, 300, 220);
    expect(s.height).toBe(300);
    expect(s.padding).toBe(12);
  });

  it('chartHeight — 모바일: -40 / 데스크탑: -60 (UI 텍스트 영역)', () => {
    expect(chartHeight(true, 300, 220)).toBe(180);
    expect(chartHeight(false, 300, 220)).toBe(240);
  });
});

const hourlySample: HourlyPatternResponse = {
  points: Array.from({ length: 24 }, (_, h) => ({
    hour: h,
    count: 100 + h,
    sent_avg: 0.1,
  })),
  meta: { total: 2400, peak_hour: 12 },
};

describe('buildHourlyOption — viewport 적응', () => {
  it('데스크탑: legend 표시 + grid 표준', () => {
    const opt = buildHourlyOption(hourlySample);
    // legend object → show 가 false 가 아님
    const legend = opt.legend as { show?: boolean } | undefined;
    expect(legend?.show).not.toBe(false);
    const grid = opt.grid as { left?: number; bottom?: number };
    expect(grid?.left).toBe(56);
    expect(grid?.bottom).toBe(36);
    // 데스크탑 분기 — dataZoom 없음
    expect(opt.dataZoom).toBeUndefined();
  });

  it('모바일: legend hide + grid 축소 + dataZoom 표시 + axisLabel 10px', () => {
    const opt = buildHourlyOption(hourlySample, { mobile: true });
    const legend = opt.legend as { show?: boolean };
    expect(legend?.show).toBe(false);
    const grid = opt.grid as { left?: number; bottom?: number };
    expect(grid?.left).toBe(36);
    expect(grid?.bottom).toBe(40);
    expect(Array.isArray(opt.dataZoom)).toBe(true);
    const xAxis = opt.xAxis as { axisLabel?: { fontSize?: number } };
    expect(xAxis?.axisLabel?.fontSize).toBe(10);
  });
});

const weekdaySample: WeekdayPatternResponse = {
  points: ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'].map((label, i) => ({
    weekday: i,
    label,
    count: 50 + i * 10,
    sent_avg: 0.1,
    neg_rate: 5 + i,
  })),
  meta: { total: 700 },
};

describe('buildWeekdayOption — viewport 적응', () => {
  it('모바일 분기: legend off + axisLabel 10px', () => {
    const opt = buildWeekdayOption(weekdaySample, { mobile: true });
    const legend = opt.legend as { show?: boolean };
    expect(legend?.show).toBe(false);
    const xAxis = opt.xAxis as { axisLabel?: { fontSize?: number } };
    expect(xAxis?.axisLabel?.fontSize).toBe(10);
  });

  it('데스크탑 분기 — legend.data 존재', () => {
    const opt = buildWeekdayOption(weekdaySample);
    const legend = opt.legend as { data?: string[] };
    expect(legend?.data).toContain('count');
    expect(legend?.data).toContain('neg_rate(%)');
  });
});
