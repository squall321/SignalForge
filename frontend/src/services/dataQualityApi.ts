// Data Clean 2 — Track D4
// 백엔드 /api/v1/_internal/data-quality 호출. localhost-only 가드는 backend.
// 사용자가 진짜 voc 비율 (mx_match_active / active) 을 실시간 확인.
import api from './api';

export interface DataQualityWorstSite {
  code: string;
  active: number;
  mx_match: number;
  /** "9.8" 처럼 문자열로 들어옴 (backend 직접 round). */
  match_pct: string;
}

export interface DataQualityResponse {
  generated_at: string;
  total: number;
  active: number;
  archived: number;
  archived_pct: number;
  mx_match_active: number;
  mx_match_pct: number;
  mx_rich_active: number;
  mx_rich_pct: number;
  by_site_worst: DataQualityWorstSite[];
}

/** 데이터 품질 단일 조회. 백엔드 cache TTL 동일. */
export async function fetchDataQuality(): Promise<DataQualityResponse> {
  const { data } = await api.get<DataQualityResponse>('/_internal/data-quality');
  return data;
}

// ── 순수 유틸 (테스트 친화) ────────────────────────────────────────────

/** "27.0" 등 문자열 match_pct 를 number 로 안전 변환 (NaN → 0). */
export function parseMatchPct(s: string | number | null | undefined): number {
  if (s == null) return 0;
  const n = typeof s === 'number' ? s : parseFloat(s);
  return Number.isFinite(n) ? n : 0;
}

/** active 대비 비율 % (소수 1자리). active===0 이면 0. */
export function activeRatio(active: number, total: number): number {
  if (!total) return 0;
  return Math.round((active / total) * 1000) / 10;
}

/** worst 사이트를 match_pct 오름차순 (가장 더러운 곳부터). */
export function worstSorted(
  sites: DataQualityWorstSite[],
): DataQualityWorstSite[] {
  return [...sites].sort(
    (a, b) => parseMatchPct(a.match_pct) - parseMatchPct(b.match_pct),
  );
}

/** match_pct 임계 색상 — Statistic / Tag 공유. */
export function matchPctTone(
  pct: number,
): 'danger' | 'warning' | 'normal' | 'good' {
  if (pct < 20) return 'danger';
  if (pct < 35) return 'warning';
  if (pct < 60) return 'normal';
  return 'good';
}

// ── Data Grow R5 (L7) — 신규 수집 사이트 추적 ──────────────────────────────
//
// R4/R5 사이클에서 신규/재활성한 collector 목록.
// 백엔드 별도 endpoint 없이 by_site_worst 의 code 매칭으로 표시.
// (수집되지 않은 사이트는 자동으로 빠짐 — 활성 voc 0 또는 active<=30)
export const DATA_GROW_CODES: ReadonlySet<string> = new Set([
  // R4 K7 신규 6
  'arxiv',
  'hackerone',
  'misskey',
  'fourchan_g',
  'pikabu',
  'quora',
  // Harvest / 글로벌 IT 매칭 100% 그룹
  'notebookcheck',
  'zdnet_kr',
  'reddit_rss',
  'resetera',
  'ifixit',
]);

/** Data Grow 신규 사이트만 추출 (worst 응답 내). */
export function dataGrowSites(
  sites: DataQualityWorstSite[],
): DataQualityWorstSite[] {
  return sites.filter((s) => DATA_GROW_CODES.has(s.code));
}

/** match_pct >= threshold 인 사이트만 (글로벌 IT 매칭 양호 표시용, 기본 90). */
export function highMatchSites(
  sites: DataQualityWorstSite[],
  threshold = 90,
): DataQualityWorstSite[] {
  return sites.filter((s) => parseMatchPct(s.match_pct) >= threshold);
}
