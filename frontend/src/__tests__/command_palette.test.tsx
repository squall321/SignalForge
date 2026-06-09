// P4.2 R6 트랙 D — CommandPalette 의 순수 로직 단위 테스트.
// 다른 테스트와 동일하게 DOM 미사용 (vitest 기본 node env). filterEntries 의 검색 동작과
// 빈 쿼리 시 페이지만 반환되는 가드를 검증한다.
import { describe, it, expect } from 'vitest';
import {
  PAGE_ENTRIES,
  filterEntries,
  toPaletteOptions,
  type SearchEntry,
} from '../components/global/commandPaletteUtils';

const EXTRA: SearchEntry[] = [
  { kind: 'product', label: 'Galaxy S25', key: 'gs25 galaxy s25', path: '/dashboard', payload: { products: ['GS25'] } },
  { kind: 'platform', label: 'Reddit', key: 'reddit', path: '/community', payload: { platforms: ['reddit'] } },
  { kind: 'keyword', label: 'battery drain', key: 'battery drain', path: '/insights', payload: { keyword: 'battery drain' } },
];

describe('filterEntries — 단축키 트리거 후 빈 입력', () => {
  it('빈 쿼리는 PAGE 만 반환 (페이지 8개 — overview/temporal/kg/geo/community/insights/alerts/compare)', () => {
    const all = [...PAGE_ENTRIES, ...EXTRA];
    const out = filterEntries(all, '', 20);
    expect(out.every((e) => e.kind === 'page')).toBe(true);
    expect(out.length).toBe(PAGE_ENTRIES.length);
  });

  it('공백만 입력 → 빈 쿼리와 동일하게 페이지만', () => {
    const out = filterEntries([...PAGE_ENTRIES, ...EXTRA], '   ', 20);
    expect(out.every((e) => e.kind === 'page')).toBe(true);
  });
});

describe('filterEntries — 결과 클릭/선택 시 매칭', () => {
  it('"galaxy" 입력 → product 매칭 + page 보다 product 가 score 가능 (kindWeight)', () => {
    const out = filterEntries([...PAGE_ENTRIES, ...EXTRA], 'galaxy', 10);
    const hasProduct = out.some((e) => e.kind === 'product' && e.label === 'Galaxy S25');
    expect(hasProduct).toBe(true);
  });

  it('"reddit" → platform Reddit 1건', () => {
    const out = filterEntries([...PAGE_ENTRIES, ...EXTRA], 'reddit', 10);
    expect(out.length).toBeGreaterThan(0);
    expect(out[0].kind).toBe('platform');
    expect(out[0].label).toBe('Reddit');
  });

  it('매칭 없는 입력 → 빈 배열', () => {
    const out = filterEntries([...PAGE_ENTRIES, ...EXTRA], 'zzz-no-match', 10);
    expect(out).toEqual([]);
  });

  it('toPaletteOptions — 결과 entry 가 option 에 그대로 첨부', () => {
    const out = filterEntries([...PAGE_ENTRIES, ...EXTRA], 'galaxy', 10);
    const opts = toPaletteOptions(out);
    expect(opts.length).toBe(out.length);
    expect(opts[0].entry.label).toContain('Galaxy');
    expect(opts[0].value).toContain(opts[0].entry.path);
  });
});
