import { describe, it, expect } from 'vitest';
import {
  isMobileBreakpoint,
  contentPadding,
  headerPadding,
} from '../components/layout/responsive';

// 트랙 D: 모바일 반응형 — Sider <-> Drawer 전환 결정 로직 단위 테스트.
// AntD useBreakpoint() 가 반환하는 ScreenMap 을 대상으로 isMobile 판정.
describe('isMobileBreakpoint', () => {
  it('xs only(=375px viewport) → mobile', () => {
    expect(isMobileBreakpoint({ xs: true })).toBe(true);
  });

  it('xs + sm (≤768px 미만) → mobile', () => {
    expect(isMobileBreakpoint({ xs: true, sm: true })).toBe(true);
  });

  it('md 이상 진입 → desktop', () => {
    expect(isMobileBreakpoint({ xs: true, sm: true, md: true })).toBe(false);
  });

  it('xl 데스크탑 → desktop', () => {
    expect(
      isMobileBreakpoint({ xs: true, sm: true, md: true, lg: true, xl: true }),
    ).toBe(false);
  });

  it('빈 객체(SSR/초기) → desktop fallback (Sider 깜빡임 방지)', () => {
    expect(isMobileBreakpoint({})).toBe(false);
  });
});

describe('paddings', () => {
  it('모바일 padding 축소', () => {
    expect(contentPadding(true)).toBe(12);
    expect(headerPadding(true)).toBe('0 12px');
  });
  it('데스크탑 기본 padding 유지', () => {
    expect(contentPadding(false)).toBe(24);
    expect(headerPadding(false)).toBe('0 24px');
  });
});
