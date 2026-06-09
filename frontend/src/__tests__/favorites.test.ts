// P4.2 R6 트랙 D — favoritesStore 단위 테스트.
// zustand store 의 toggle/has/list/clear 동작과 cardRegistry 와의 정합성 검증.
// localStorage 영속은 별도 e2e 에서 검증 (vitest node 환경에서는 in-memory).
import { describe, it, expect, beforeEach } from 'vitest';
import { useFavoritesStore } from '../stores/favoritesStore';
import { CARD_REGISTRY, lookupCardMeta } from '../components/global/cardRegistry';

describe('favoritesStore — toggle/list', () => {
  beforeEach(() => {
    // 각 테스트마다 초기화
    useFavoritesStore.getState().clear();
  });

  it('toggle 으로 add → list 1건, has=true', () => {
    const s = useFavoritesStore.getState();
    expect(s.list()).toEqual([]);

    s.toggle('hourly-pattern');
    const after = useFavoritesStore.getState();
    expect(after.has('hourly-pattern')).toBe(true);
    expect(after.list()).toEqual(['hourly-pattern']);
  });

  it('동일 id 재토글 → 제거, 다른 id 추가 후 clear → 빈 리스트', () => {
    const s = useFavoritesStore.getState();
    s.toggle('hourly-pattern');
    s.toggle('emerging-keywords');
    expect(useFavoritesStore.getState().list().sort()).toEqual([
      'emerging-keywords',
      'hourly-pattern',
    ]);

    s.toggle('hourly-pattern'); // 제거
    expect(useFavoritesStore.getState().has('hourly-pattern')).toBe(false);
    expect(useFavoritesStore.getState().list()).toEqual(['emerging-keywords']);

    s.clear();
    expect(useFavoritesStore.getState().list()).toEqual([]);
  });
});

describe('cardRegistry — id → meta lookup', () => {
  it('FavoriteButton 이 사용하는 카드 id 가 registry 에 모두 존재', () => {
    // 트랙 D 에서 통합한 2개 카드 (hourly-pattern, emerging-keywords) 는 반드시 등록되어 있어야 함.
    expect(lookupCardMeta('hourly-pattern')).toBeTruthy();
    expect(lookupCardMeta('emerging-keywords')).toBeTruthy();
    expect(lookupCardMeta('hourly-pattern')?.path).toBe('/insights');
  });

  it('CARD_REGISTRY 의 id 는 중복 없음 (단일 진실)', () => {
    const ids = CARD_REGISTRY.map((c) => c.id);
    const unique = new Set(ids);
    expect(ids.length).toBe(unique.size);
  });

  it('없는 id 는 undefined', () => {
    expect(lookupCardMeta('does-not-exist')).toBeUndefined();
  });
});
