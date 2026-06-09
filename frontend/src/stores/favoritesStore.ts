// P4.2 R6 트랙 D — 즐겨찾기 store.
// cardId 를 Set 으로 관리. zustand persist 미들웨어로 localStorage 영속화.
// (Set 은 JSON 직렬화 안 되므로 partialize 에서 Array 로 변환, merge 시 Set 으로 복원.)
import { create } from 'zustand';
import { persist } from 'zustand/middleware';

export interface FavoritesState {
  ids: Set<string>;
  toggle: (id: string) => void;
  has: (id: string) => boolean;
  list: () => string[];
  clear: () => void;
}

const STORAGE_KEY = 'signalforge.favorites.v1';

export const useFavoritesStore = create<FavoritesState>()(
  persist(
    (set, get) => ({
      ids: new Set<string>(),
      toggle: (id: string) =>
        set((s) => {
          const next = new Set(s.ids);
          if (next.has(id)) next.delete(id);
          else next.add(id);
          return { ids: next };
        }),
      has: (id: string) => get().ids.has(id),
      list: () => Array.from(get().ids),
      clear: () => set({ ids: new Set<string>() }),
    }),
    {
      name: STORAGE_KEY,
      // Set <-> Array 변환
      partialize: (s) => ({ ids: Array.from(s.ids) }) as unknown as FavoritesState,
      merge: (persisted, current) => {
        const persistedIds = (persisted as { ids?: string[] } | undefined)?.ids ?? [];
        return { ...current, ids: new Set(persistedIds) };
      },
    },
  ),
);
