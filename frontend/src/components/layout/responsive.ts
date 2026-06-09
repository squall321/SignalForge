// 반응형 결정 로직 — AppLayout / GlobalFilterBar 가 공유.
// AntD <Grid.useBreakpoint> 결과(Partial<Record<Breakpoint, boolean>>) 를
// "모바일이냐 아니냐" 단일 boolean 으로 정규화한다.
//
// 모바일 기준: md 미만(=화면폭 < 768px). xs/sm 진입 시 true.
// 테스트 가능성을 위해 순수 함수로 분리해두고 컴포넌트는 이 함수만 호출.

export type ScreenMap = Partial<Record<'xs' | 'sm' | 'md' | 'lg' | 'xl' | 'xxl', boolean>>;

export function isMobileBreakpoint(screens: ScreenMap): boolean {
  // useBreakpoint 가 빈 객체를 반환하는 SSR/초기 마운트 케이스 → 데스크탑으로 fallback.
  if (!screens || Object.keys(screens).length === 0) return false;
  return !screens.md;
}

export function contentPadding(mobile: boolean): number {
  return mobile ? 12 : 24;
}

export function headerPadding(mobile: boolean): string {
  return mobile ? '0 12px' : '0 24px';
}
