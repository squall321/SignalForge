// 전역 필터 타입 정의 (P1 MVP)
export interface DateRange {
  start: string; // ISO date (YYYY-MM-DD)
  end: string;
}

export interface GlobalFilters {
  dateRange: DateRange;
  products: string[];   // product_code 목록 (다중 선택)
  regions: string[];    // 지역 코드 목록
  platforms: string[];  // platform_code 목록
  // P4.3 트랙 B: 카드 인터랙션 — 기간 빠른 선택 + 카테고리 다중 선택
  // period_days = 0 → custom (dateRange 사용), > 0 → 최근 N일 (dateRange 무시)
  periodDays: number;
  categories: string[]; // 카테고리 코드 목록 (다중 선택)
}

export const DEFAULT_FILTERS: GlobalFilters = {
  dateRange: { start: '', end: '' },
  products: [],
  regions: [],
  platforms: [],
  periodDays: 30,
  categories: [],
};

// 카드 useQuery 가 period_days 인자로 쓸 정수.
// custom range 일 때는 dateRange 길이를 일수로 환산, 기본은 periodDays 그대로.
export function effectivePeriodDays(f: Pick<GlobalFilters, 'periodDays' | 'dateRange'>): number {
  if (f.periodDays > 0) return f.periodDays;
  const { start, end } = f.dateRange;
  if (!start || !end) return 30;
  const ms = new Date(end).getTime() - new Date(start).getTime();
  const days = Math.ceil(ms / 86_400_000) + 1;
  return days > 0 ? days : 30;
}
