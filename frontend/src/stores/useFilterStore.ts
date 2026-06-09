import { create } from 'zustand';
import { DEFAULT_FILTERS, type DateRange, type GlobalFilters } from '../types/filters';

interface FilterState extends GlobalFilters {
  setDateRange: (range: DateRange) => void;
  setProducts: (codes: string[]) => void;
  setRegions: (codes: string[]) => void;
  setPlatforms: (codes: string[]) => void;
  setPeriodDays: (n: number) => void;
  setCategories: (codes: string[]) => void;
  setAll: (filters: Partial<GlobalFilters>) => void;
  reset: () => void;
}

/**
 * 전역 필터 상태.
 * URL <-> store 양방향 동기화는 useFilterUrlSync 훅이 담당한다.
 */
export const useFilterStore = create<FilterState>((set) => ({
  ...DEFAULT_FILTERS,
  setDateRange: (dateRange) => set({ dateRange, periodDays: 0 }),
  setProducts: (products) => set({ products }),
  setRegions: (regions) => set({ regions }),
  setPlatforms: (platforms) => set({ platforms }),
  setPeriodDays: (periodDays) =>
    // 빠른 기간 선택 시 dateRange 는 비워서 effectivePeriodDays 가 periodDays 를 따르게 함
    set({ periodDays, dateRange: { start: '', end: '' } }),
  setCategories: (categories) => set({ categories }),
  setAll: (filters) => set((s) => ({ ...s, ...filters })),
  reset: () => set({ ...DEFAULT_FILTERS }),
}));
