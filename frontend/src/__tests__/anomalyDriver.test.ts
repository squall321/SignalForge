import { describe, it, expect } from 'vitest';
import type { AnomalyWithDriversEntry } from '../types/deep';
import {
  driverColor,
  selectEntry,
  sortByDate,
} from '../components/deep/anomalyDriverUtils';

// P3.7 트랙 B — AnomalyDriverCard 선택 로직 단위 테스트.
const sample: AnomalyWithDriversEntry[] = [
  {
    date: '2026-05-26',
    metric: 'category_daily_count',
    category: 'connectivity',
    z: 2.1,
    baseline: 107.8,
    value: 266,
    top_drivers: [
      { keyword: 'wifi', delta_pct: 300, sentiment: -0.3 },
      { keyword: 'bt', delta_pct: 200, sentiment: 0.0 },
    ],
  },
  {
    date: '2026-05-24',
    metric: 'category_daily_count',
    category: 'build_quality',
    z: 2.7,
    baseline: 61.8,
    value: 199,
    top_drivers: [{ keyword: 'crack', delta_pct: 250, sentiment: -0.6 }],
  },
];

describe('sortByDate', () => {
  it('날짜 오름차순', () => {
    const r = sortByDate(sample);
    expect(r.map((x) => x.date)).toEqual(['2026-05-24', '2026-05-26']);
  });
  it('빈 배열', () => {
    expect(sortByDate([])).toEqual([]);
  });
});

describe('selectEntry', () => {
  it('selectedDate 일치 → 해당 entry', () => {
    const r = selectEntry(sample, '2026-05-26');
    expect(r?.category).toBe('connectivity');
  });
  it('selectedDate 없음 → 최고 z entry (build_quality, z=2.7)', () => {
    const r = selectEntry(sample, null);
    expect(r?.category).toBe('build_quality');
  });
  it('selectedDate 불일치 → 최고 z 폴백', () => {
    const r = selectEntry(sample, '2099-01-01');
    expect(r?.category).toBe('build_quality');
  });
  it('빈 배열 → null', () => {
    expect(selectEntry([], null)).toBeNull();
  });
});

describe('driverColor', () => {
  it('부정 sentiment → 빨강', () => {
    expect(driverColor({ keyword: 'x', delta_pct: 0, sentiment: -0.3 })).toBe('#cf1322');
  });
  it('긍정 sentiment → 파랑', () => {
    expect(driverColor({ keyword: 'x', delta_pct: 0, sentiment: 0.2 })).toBe('#1677ff');
  });
  it('중립 sentiment (|x|<=0.05) → 회색', () => {
    expect(driverColor({ keyword: 'x', delta_pct: 0, sentiment: 0.0 })).toBe('#8c8c8c');
    expect(driverColor({ keyword: 'x', delta_pct: 0, sentiment: 0.05 })).toBe('#8c8c8c');
  });
});
