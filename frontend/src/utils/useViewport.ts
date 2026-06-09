// 트랙 D — 모바일 차트 reflow / 카드 적응형.
//
// AntD Grid.useBreakpoint() 를 한 번 호출 → 카드/차트가 필요로 하는
// "viewport 정보" 를 일관된 형태로 노출한다.
//
// 정의 (AntD breakpoint 와 동일):
//   xs <576px, sm <768px, md <992px, lg <1200px, xl <1600px, xxl >=1600px
//
// 호출자는 viewport.size('xs'|'sm'|'md'|'lg'|'xl'|'xxl') 또는 boolean flag 를 사용한다.
//
// AppLayout 의 isMobileBreakpoint() 와 책임이 겹치지 않게:
//   - isMobileBreakpoint: Sider ↔ Drawer 전환 한정 (md 미만=true)
//   - useViewport       : 카드 내부 chart height/font/legend/dataZoom 적응
//
// 빈 screens 객체(SSR/초기)는 "lg" 데스크탑으로 fallback (깜빡임 방지).

import { Grid } from 'antd';

const { useBreakpoint } = Grid;

export type ViewportSize = 'xs' | 'sm' | 'md' | 'lg' | 'xl' | 'xxl';

export interface Viewport {
  size: ViewportSize;
  xs: boolean;       // <576 (모바일 세로)
  sm: boolean;       // 576~767 (모바일 가로 / 작은 태블릿)
  md: boolean;       // 768~991 (태블릿)
  lg: boolean;       // 992~1199 (소형 데스크탑)
  xl: boolean;       // 1200~1599 (표준 데스크탑)
  xxl: boolean;      // >=1600 (대형 모니터)
  isMobile: boolean; // xs+sm 통합 — 차트 1열 / 폰트 축소 / legend hide
  isTablet: boolean; // md — 2열
  isDesktop: boolean;// lg+xl+xxl
}

// ScreenMap → ViewportSize 산정. 가장 큰 활성 breakpoint 가 size.
export function resolveSize(screens: Partial<Record<ViewportSize, boolean>>): ViewportSize {
  if (!screens || Object.keys(screens).length === 0) return 'lg';
  const order: ViewportSize[] = ['xxl', 'xl', 'lg', 'md', 'sm', 'xs'];
  for (const k of order) {
    if (screens[k]) return k;
  }
  return 'xs';
}

export function useViewport(): Viewport {
  const screens = useBreakpoint();
  const size = resolveSize(screens);
  // useBreakpoint 빈 객체 → resolveSize 가 'lg' 반환 → 모든 flag 안전 fallback.
  const xs = size === 'xs';
  const sm = size === 'sm';
  const md = size === 'md';
  const lg = size === 'lg';
  const xl = size === 'xl';
  const xxl = size === 'xxl';
  const isMobile = xs || sm;
  const isTablet = md;
  const isDesktop = lg || xl || xxl;
  return { size, xs, sm, md, lg, xl, xxl, isMobile, isTablet, isDesktop };
}
