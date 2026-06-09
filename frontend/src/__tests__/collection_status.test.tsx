// Track B — CollectionStatus 페이지 순수 유틸 단위 테스트.
// vitest node 환경 (jsdom 미사용) — collectionStatusApi 의 헬퍼만 검증.
//
// 검증:
//   1) countByHealth — 각 health 별 카운트가 정확히 누적되는지
//   2) regionSorted  — records_24h 내림차순 정렬이 유지되는지
//   3) HEALTH_BADGE  — 4 enum 모두 color / label / status 필드 보유
import { describe, it, expect } from 'vitest';
import {
  HEALTH_BADGE,
  countByHealth,
  regionSorted,
  type CollectionPlatform,
} from '../services/collectionStatusApi';

function mk(
  code: string,
  health: CollectionPlatform['health'],
  rec24 = 0,
): CollectionPlatform {
  return {
    code,
    name: code,
    region: 'KR',
    is_active: true,
    records_24h: rec24,
    records_1h: 0,
    records_7d: rec24 * 7,
    last_collected: null,
    hours_since_last: null,
    avg_per_day_7d: rec24,
    health,
  };
}

describe('countByHealth', () => {
  it('각 health 별 카운트 누적', () => {
    const c = countByHealth([
      mk('a', 'active'),
      mk('b', 'active'),
      mk('c', 'slow'),
      mk('d', 'stale'),
      mk('e', 'dead'),
      mk('f', 'dead'),
    ]);
    expect(c.active).toBe(2);
    expect(c.slow).toBe(1);
    expect(c.stale).toBe(1);
    expect(c.dead).toBe(2);
  });

  it('빈 입력 → 모두 0', () => {
    const c = countByHealth([]);
    expect(c.active).toBe(0);
    expect(c.slow).toBe(0);
    expect(c.stale).toBe(0);
    expect(c.dead).toBe(0);
  });
});

describe('regionSorted', () => {
  it('records_24h 내림차순 정렬', () => {
    const out = regionSorted({
      KR: { active: 10, total: 12, records_24h: 5000 },
      US: { active: 5, total: 6, records_24h: 1200 },
      JP: { active: 2, total: 3, records_24h: 8000 },
    });
    expect(out.map(([k]) => k)).toEqual(['JP', 'KR', 'US']);
    expect(out[0][1].records_24h).toBe(8000);
  });

  it('빈 입력 → 빈 배열', () => {
    expect(regionSorted({})).toEqual([]);
  });
});

describe('HEALTH_BADGE 메타', () => {
  it('4 enum 모두 color / label / status 보유', () => {
    (['active', 'slow', 'stale', 'dead'] as const).forEach((h) => {
      const meta = HEALTH_BADGE[h];
      expect(typeof meta.color).toBe('string');
      expect(meta.label.length).toBeGreaterThan(0);
      expect(['success', 'warning', 'default', 'error']).toContain(meta.status);
    });
  });
});
