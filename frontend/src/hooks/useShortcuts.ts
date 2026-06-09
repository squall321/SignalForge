// P4.2 R6 트랙 D — 전역 키보드 단축키.
// 의존성 0 — window.addEventListener('keydown') 기반.
// 패턴: 단일키(/, ?, f) + g 프리픽스 + 후속키(d/t/k/g/c/i/a) chord.
// 입력 중(input/textarea/contenteditable) 일 때는 모든 chord 무시 (Esc/Ctrl+K 등 modifier 조합은 별도 처리 가능).
import { useEffect, useRef } from 'react';
import { useNavigate } from 'react-router-dom';

export interface ShortcutHandlers {
  openSearch: () => void;
  openHelp: () => void;
  focusFilter: () => void;
}

/** g 프리픽스 후 1.2s 안에 다음 키가 들어와야 chord 인정. */
const CHORD_WINDOW_MS = 1200;

const PAGE_MAP: Record<string, string> = {
  d: '/dashboard',
  t: '/temporal',
  k: '/kg',
  g: '/geo',
  c: '/community',
  i: '/insights',
  a: '/alerts',
};

function isTypingTarget(el: EventTarget | null): boolean {
  if (!el || !(el instanceof HTMLElement)) return false;
  const tag = el.tagName;
  if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return true;
  if (el.isContentEditable) return true;
  return false;
}

export function useShortcuts(handlers: ShortcutHandlers) {
  const navigate = useNavigate();
  const gPrefixAt = useRef<number>(0);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      // Ctrl/Cmd+K → 검색 (입력 중에도 동작)
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault();
        handlers.openSearch();
        return;
      }
      // 다른 modifier 조합은 무시
      if (e.ctrlKey || e.metaKey || e.altKey) return;
      // 입력 필드에서는 chord/단일키 무시
      if (isTypingTarget(e.target)) return;

      const now = Date.now();
      const key = e.key;

      // g 프리픽스 대기 중인지 판단
      const inChord = now - gPrefixAt.current < CHORD_WINDOW_MS;

      if (inChord) {
        const k = key.toLowerCase();
        const path = PAGE_MAP[k];
        if (path) {
          e.preventDefault();
          navigate(path);
          gPrefixAt.current = 0;
          return;
        }
        // chord 윈도우 안에서 매핑 없는 키 → 취소
        gPrefixAt.current = 0;
        // 단일키로 폴백 처리 계속
      }

      if (key === 'g') {
        gPrefixAt.current = now;
        return;
      }
      if (key === '/') {
        e.preventDefault();
        handlers.openSearch();
        return;
      }
      if (key === '?') {
        e.preventDefault();
        handlers.openHelp();
        return;
      }
      if (key === 'f') {
        e.preventDefault();
        handlers.focusFilter();
        return;
      }
    };

    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [navigate, handlers]);
}
