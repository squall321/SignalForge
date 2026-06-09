import { useEffect, useRef } from 'react';
import { useSearchParams } from 'react-router-dom';
import { useFilterStore } from '../stores/useFilterStore';
import type { GlobalFilters } from '../types/filters';

const PARAM_KEYS = {
  start: 'start',
  end: 'end',
  products: 'products',
  regions: 'regions',
  platforms: 'platforms',
  period: 'period',
  categories: 'categories',
} as const;

function parseList(v: string | null): string[] {
  if (!v) return [];
  return v
    .split(',')
    .map((s) => s.trim())
    .filter(Boolean);
}

function listEq(a: string[], b: string[]) {
  if (a.length !== b.length) return false;
  for (let i = 0; i < a.length; i++) if (a[i] !== b[i]) return false;
  return true;
}

/**
 * URL <-> store 양방향 동기화.
 * - mount 시 URL을 store에 반영 (incognito 새창 재현 가능)
 * - store 변경 시 URL의 query string을 갱신 (history.replace)
 */
export function useFilterUrlSync() {
  const [searchParams, setSearchParams] = useSearchParams();
  const filters = useFilterStore();
  const initialized = useRef(false);

  // 1) URL -> store : mount + URL 변경 시
  useEffect(() => {
    const start = searchParams.get(PARAM_KEYS.start) ?? '';
    const end = searchParams.get(PARAM_KEYS.end) ?? '';
    const products = parseList(searchParams.get(PARAM_KEYS.products));
    const regions = parseList(searchParams.get(PARAM_KEYS.regions));
    const platforms = parseList(searchParams.get(PARAM_KEYS.platforms));
    const categories = parseList(searchParams.get(PARAM_KEYS.categories));
    const periodRaw = searchParams.get(PARAM_KEYS.period);
    const periodDays = periodRaw === null ? 30 : Number(periodRaw) || 0;

    const next: GlobalFilters = {
      dateRange: { start, end },
      products,
      regions,
      platforms,
      periodDays,
      categories,
    };

    const cur = useFilterStore.getState();
    const same =
      cur.dateRange.start === next.dateRange.start &&
      cur.dateRange.end === next.dateRange.end &&
      listEq(cur.products, next.products) &&
      listEq(cur.regions, next.regions) &&
      listEq(cur.platforms, next.platforms) &&
      listEq(cur.categories, next.categories) &&
      cur.periodDays === next.periodDays;

    if (!same) {
      useFilterStore.getState().setAll(next);
    }
    initialized.current = true;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [searchParams]);

  // 2) store -> URL : 필터 변경 시
  useEffect(() => {
    if (!initialized.current) return;
    const next = new URLSearchParams();
    if (filters.dateRange.start) next.set(PARAM_KEYS.start, filters.dateRange.start);
    if (filters.dateRange.end) next.set(PARAM_KEYS.end, filters.dateRange.end);
    if (filters.products.length) next.set(PARAM_KEYS.products, filters.products.join(','));
    if (filters.regions.length) next.set(PARAM_KEYS.regions, filters.regions.join(','));
    if (filters.platforms.length) next.set(PARAM_KEYS.platforms, filters.platforms.join(','));
    if (filters.categories.length) next.set(PARAM_KEYS.categories, filters.categories.join(','));
    // 기본값(30)이 아닐 때만 URL 에 반영 — 깨끗한 URL 유지
    if (filters.periodDays !== 30) next.set(PARAM_KEYS.period, String(filters.periodDays));

    // 동일하면 set 호출 안함 (loop 방지)
    if (next.toString() !== searchParams.toString()) {
      setSearchParams(next, { replace: true });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    filters.dateRange.start,
    filters.dateRange.end,
    filters.products,
    filters.regions,
    filters.platforms,
    filters.categories,
    filters.periodDays,
  ]);
}
