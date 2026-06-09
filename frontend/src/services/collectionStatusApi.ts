// Track B — 수집 채널 모니터링 API.
// 백엔드 /api/v1/_internal/collection-status 호출.  localhost-only 가드는 backend.
import api from './api';

export type PlatformHealth = 'active' | 'slow' | 'stale' | 'dead';

export interface CollectionPlatform {
  code: string;
  name: string;
  region: string | null;
  is_active: boolean;
  records_24h: number;
  records_1h: number;
  records_7d: number;
  last_collected: string | null;
  hours_since_last: number | null;
  avg_per_day_7d: number;
  health: PlatformHealth;
}

export interface CollectionRegionStat {
  active: number;
  total: number;
  records_24h: number;
}

export interface CollectionStatusResponse {
  hours: number;
  generated_at: string;
  summary: {
    total_active: number;
    total_inactive: number;
    total_records_24h: number;
    total_records_1h: number;
  };
  platforms: CollectionPlatform[];
  by_region: Record<string, CollectionRegionStat>;
}

/** 수집 상태 단일 조회. 백엔드 redis 캐시 TTL 300s. */
export async function fetchCollectionStatus(
  hours = 24,
): Promise<CollectionStatusResponse> {
  const { data } = await api.get<CollectionStatusResponse>(
    '/_internal/collection-status',
    { params: { hours } },
  );
  return data;
}

// ── 순수 유틸 (테스트 친화) ────────────────────────────────────────────
// health 별 색·라벨 — AntD Tag/Badge 양쪽에서 쓰는 dict.
export const HEALTH_BADGE: Record<
  PlatformHealth,
  { color: string; label: string; status: 'success' | 'warning' | 'default' | 'error' }
> = {
  active: { color: 'green', label: '정상', status: 'success' },
  slow: { color: 'orange', label: '둔화', status: 'warning' },
  stale: { color: 'gold', label: '정체', status: 'warning' },
  dead: { color: 'red', label: '중단', status: 'error' },
};

/** health 별 카운트 — 상단 KPI 분포. */
export function countByHealth(
  platforms: CollectionPlatform[],
): Record<PlatformHealth, number> {
  const out: Record<PlatformHealth, number> = {
    active: 0, slow: 0, stale: 0, dead: 0,
  };
  for (const p of platforms) out[p.health] += 1;
  return out;
}

/** 지역별 합계를 records_24h 내림차순 ordered tuple 로. */
export function regionSorted(
  byRegion: Record<string, CollectionRegionStat>,
): Array<[string, CollectionRegionStat]> {
  return Object.entries(byRegion).sort(
    (a, b) => b[1].records_24h - a[1].records_24h,
  );
}
