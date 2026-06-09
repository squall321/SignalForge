// P4.2 R6 트랙 D — CommandPalette 의 순수 검색/필터 로직.
// DOM 미사용. vitest 단위 테스트 대상.
export type SearchKind = 'page' | 'product' | 'platform' | 'category' | 'keyword';

export interface SearchEntry {
  kind: SearchKind;
  /** 표시 라벨 */
  label: string;
  /** 검색 매칭에 쓰는 정규화된 키 (lowercase, ascii 권장) */
  key: string;
  /** 이동 경로 (예: '/dashboard') */
  path: string;
  /** 페이지 진입 시 적용할 필터(option) */
  payload?: {
    products?: string[];
    platforms?: string[];
    regions?: string[];
    keyword?: string;
  };
}

/** 페이지 7개 — 단축키 (g d / g t / ...) 와 동일 매핑. */
export const PAGE_ENTRIES: SearchEntry[] = [
  { kind: 'page', label: 'Overview (대시보드)', key: 'overview dashboard', path: '/dashboard' },
  { kind: 'page', label: '시계열 인사이트', key: 'temporal timeseries', path: '/temporal' },
  { kind: 'page', label: '지식 그래프', key: 'knowledge graph kg', path: '/kg' },
  { kind: 'page', label: '국가 분석', key: 'geo country', path: '/geo' },
  { kind: 'page', label: '커뮤니티', key: 'community', path: '/community' },
  { kind: 'page', label: '딥 인사이트', key: 'deep insights', path: '/insights' },
  { kind: 'page', label: '실시간 알림', key: 'alerts realtime', path: '/alerts' },
  { kind: 'page', label: '비교', key: 'compare 비교', path: '/compare' },
];

/**
 * 입력 쿼리에 매칭되는 후보를 score 순으로 정렬해 반환.
 * - 빈 쿼리: PAGE_ENTRIES 만 (사용자가 ⌘K 누르자마자 페이지 점프하도록).
 * - prefix 매칭에 가산점, substring 매칭은 기본 점수.
 * - kind 별 가중치: page > product > platform > category > keyword.
 */
export function filterEntries(
  entries: SearchEntry[],
  query: string,
  limit = 20,
): SearchEntry[] {
  const q = query.trim().toLowerCase();
  if (!q) {
    return entries.filter((e) => e.kind === 'page').slice(0, limit);
  }
  const kindWeight: Record<SearchKind, number> = {
    page: 5,
    product: 4,
    platform: 3,
    category: 2,
    keyword: 1,
  };
  const scored: { entry: SearchEntry; score: number }[] = [];
  for (const e of entries) {
    const label = e.label.toLowerCase();
    const key = e.key.toLowerCase();
    let s = 0;
    if (label.startsWith(q) || key.startsWith(q)) s = 10;
    else if (label.includes(q) || key.includes(q)) s = 5;
    if (s > 0) {
      s += kindWeight[e.kind];
      scored.push({ entry: e, score: s });
    }
  }
  scored.sort((a, b) => b.score - a.score);
  return scored.slice(0, limit).map((x) => x.entry);
}

/** AntD AutoComplete options 로 변환. */
export interface PaletteOption {
  value: string;
  label: string;
  entry: SearchEntry;
}
export function toPaletteOptions(list: SearchEntry[]): PaletteOption[] {
  return list.map((entry, idx) => ({
    value: `${entry.kind}:${entry.path}:${idx}`,
    label: `[${kindLabel(entry.kind)}] ${entry.label}`,
    entry,
  }));
}

function kindLabel(kind: SearchKind): string {
  switch (kind) {
    case 'page':
      return '페이지';
    case 'product':
      return '제품';
    case 'platform':
      return '플랫폼';
    case 'category':
      return '카테고리';
    case 'keyword':
      return '키워드';
  }
}
