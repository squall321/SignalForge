// P4.3 트랙 B — GlobalFilterBar 보강 단위 테스트.
// 1) effectivePeriodDays — 기간 빠른 선택 vs custom range 환산
// 2) filter store — categories 누적 set + reset 동작
import { describe, it, expect, beforeEach } from 'vitest';
import { effectivePeriodDays } from '../types/filters';
import { useFilterStore } from '../stores/useFilterStore';

describe('effectivePeriodDays', () => {
  it('periodDays > 0 → 그대로 사용', () => {
    expect(
      effectivePeriodDays({ periodDays: 14, dateRange: { start: '', end: '' } }),
    ).toBe(14);
    expect(
      effectivePeriodDays({ periodDays: 90, dateRange: { start: '2026-01-01', end: '2026-01-31' } }),
    ).toBe(90);
  });

  it('periodDays === 0 + custom range → 일수 환산 (inclusive)', () => {
    // 6/1 ~ 6/3 → 3 일
    expect(
      effectivePeriodDays({ periodDays: 0, dateRange: { start: '2026-06-01', end: '2026-06-03' } }),
    ).toBe(3);
    // 1/1 ~ 1/1 → 1 일
    expect(
      effectivePeriodDays({ periodDays: 0, dateRange: { start: '2026-01-01', end: '2026-01-01' } }),
    ).toBe(1);
  });

  it('periodDays === 0 + 빈 range → 기본값 30', () => {
    expect(
      effectivePeriodDays({ periodDays: 0, dateRange: { start: '', end: '' } }),
    ).toBe(30);
  });
});

describe('useFilterStore — categories / periodDays', () => {
  beforeEach(() => {
    useFilterStore.getState().reset();
  });

  it('setCategories — 다중 선택 누적 + 초기화 가능', () => {
    const s = useFilterStore.getState();
    s.setCategories(['battery', 'camera']);
    expect(useFilterStore.getState().categories).toEqual(['battery', 'camera']);
    s.setCategories([]);
    expect(useFilterStore.getState().categories).toEqual([]);
  });

  it('setPeriodDays — dateRange 초기화하여 effectivePeriodDays 가 일관되게 동작', () => {
    const s = useFilterStore.getState();
    s.setDateRange({ start: '2026-06-01', end: '2026-06-03' });
    expect(useFilterStore.getState().periodDays).toBe(0); // custom 진입
    s.setPeriodDays(7);
    const cur = useFilterStore.getState();
    expect(cur.periodDays).toBe(7);
    expect(cur.dateRange).toEqual({ start: '', end: '' }); // custom 해제
    expect(effectivePeriodDays(cur)).toBe(7);
  });

  it('reset — 모든 필터를 기본값으로 복원', () => {
    const s = useFilterStore.getState();
    s.setProducts(['GS25']);
    s.setCategories(['battery']);
    s.setPeriodDays(90);
    s.reset();
    const cur = useFilterStore.getState();
    expect(cur.products).toEqual([]);
    expect(cur.categories).toEqual([]);
    expect(cur.periodDays).toBe(30);
  });
});
